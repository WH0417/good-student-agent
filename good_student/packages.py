"""Versioned, source-backed study packages. No model calls occur when reading."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import uuid

from .practice import grade_answer, validate_generated
from .storage import DataError, atomic_json, file_lock, now


def _text(value, label, minimum=1):
    if not isinstance(value, str) or len(value.strip()) < minimum:
        raise DataError(f"{label}必须提供实质内容")
    return value.strip()


def _strings(value, label, allow_empty=False):
    if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
        raise DataError(f"{label}必须是字符串列表")
    result = list(dict.fromkeys(v.strip() for v in value))
    if not allow_empty and not result:
        raise DataError(f"{label}不能为空")
    return result


def _mindmap(value, label):
    text = _text(value, label, 10)
    if not re.match(r"^(?:mindmap|flowchart|graph)\b", text):
        raise DataError(f"{label}应为Mermaid导图源码")
    return text


def _sources(item):
    return set(item.get("source_ids", [])) | ({item["source_id"]} if item.get("source_id") else set()) | {
        ref["source_id"] for ref in item.get("source_refs", []) if isinstance(ref, dict) and ref.get("source_id")}


def _flag_visible_answer(question):
    """Keep the source stem verbatim, but do not present leaked answers as a test."""
    question = deepcopy(question)
    reference = json.dumps({"answer": question.get("answer"), "explanation": question.get("explanation")},
                           ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    question["reference_version"] = "ref_" + hashlib.sha256(reference.encode("utf-8")).hexdigest()[:24]
    stem = question.get("question", "")
    answer = str(question.get("answer") or "").strip().upper()
    explicit = bool(re.search(r"(?:参考答案|正确答案|答案)\s*[:：]\s*\S", stem))
    labels = re.fullmatch(r"[A-H](?:[、,，\s]*[A-H])*", answer)
    bracketed = False
    if labels:
        for match in re.finditer(r"[\[【（(]\s*([A-H](?:[、,，\s]*[A-H])*)\s*[\]】）)]", stem, re.I):
            # Even an incorrect printed answer gives a hint; keep it out of
            # independent assessment when the prepared reference corrects it.
            bracketed = True
            break
    visible = explicit or bracketed
    question["answer_visible_in_stem"] = visible
    question["independent_eligible"] = not visible and question.get("answer_status") not in ("needs_review", "needs_support", "pending_generation")
    if visible:
        question["practice_note"] = "原题题干含答案提示，保留原文供讲解，不作为独立检测。"
    elif question.get("answer_status") == "needs_review":
        question["practice_note"] = question.get("answer_note") or "答案存在待复核疑点，暂不作为独立检测。"
    return question


def _atomic_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".package-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _position(locator):
    labels = (("page", "第{}页"), ("slide", "第{}张幻灯片"),
              ("text_block", "文本块{}"), ("paragraph", "第{}段"))
    parts = [template.format(locator[key]) for key, template in labels if locator.get(key) is not None]
    for key in ("timestamp", "time_start", "start"):
        if locator.get(key) is not None:
            value = locator[key]
            parts.append(f"时间{value}" + ("秒" if isinstance(value, (int, float)) else ""))
            break
    return "，".join(parts) or "未提供细分位置"


def _without_credentials(value):
    """Export learning content, never executor/session credential metadata."""
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            normalized = "".join(c for c in str(key).casefold() if c.isalnum())
            if normalized.endswith("token") or normalized in ("authorization", "cookie", "setcookie"):
                continue
            result[key] = _without_credentials(child)
        return result
    if isinstance(value, list):
        return [_without_credentials(child) for child in value]
    return deepcopy(value)


class PackageManager:
    def __init__(self, service):
        self.service = service
        self.store = service.store

    def _root(self, course_id):
        self.store.read(course_id)
        return self.store.contained(course_id, "packages")

    def _directory(self, course_id, package_id):
        if not isinstance(package_id, str) or not re.fullmatch(r"pkg_[0-9a-f]{12}", package_id):
            raise DataError("无效复习包编号")
        return self.store.contained(course_id, f"packages/{package_id}")

    def _manifest(self, course_id, package_id):
        directory = self._directory(course_id, package_id)
        try:
            manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DataError(f"复习包不存在或记录无法读取：{exc}") from exc
        if (not isinstance(manifest, dict) or manifest.get("id") != package_id
                or manifest.get("course_id") != course_id or not isinstance(manifest.get("chapters"), list)):
            raise DataError("复习包所属课程或结构异常，未修改原记录")
        return manifest

    @staticmethod
    def _chapter_id(chapter_id):
        if not isinstance(chapter_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", chapter_id):
            raise DataError("章节编号只能包含字母、数字、下划线或短横线")
        return chapter_id

    def _chapter_path(self, course_id, package_id, chapter_id, suffix="json"):
        self._chapter_id(chapter_id)
        return self.store.contained(course_id, f"packages/{package_id}/chapters/{chapter_id}.{suffix}")

    def create(self, course_id, job, chapters):
        state = self.store.read(course_id)
        if not isinstance(job, dict) or job.get("course_id") != course_id:
            raise DataError("任务不属于当前课程")
        source_ids = _strings(job.get("source_ids", []), "选定资料")
        if any(sid not in state["sources"] for sid in source_ids):
            raise DataError("选定资料不属于当前课程")
        if not isinstance(chapters, list) or not chapters:
            raise DataError("请先列出本次需要生成的章节")
        plans, seen = [], set()
        for chapter in chapters:
            if not isinstance(chapter, dict):
                raise DataError("章节计划必须是对象")
            identifier = self._chapter_id(chapter.get("id"))
            if identifier in seen:
                raise DataError("章节编号重复")
            seen.add(identifier)
            ids = _strings(chapter.get("source_ids", []), "章节资料")
            if not set(ids).issubset(source_ids):
                raise DataError("章节引用了本任务范围以外的资料")
            count = chapter.get("question_count", 3)
            if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 100:
                raise DataError("每章练习数量应为1到100")
            plans.append({"id": identifier, "title": _text(chapter.get("title"), "章节标题"),
                          "source_ids": ids, "question_count": count, "status": "pending"})
        package_id = "pkg_" + uuid.uuid4().hex[:12]
        snapshots = [{"id": sid, "sha256": state["sources"][sid].get("sha256"),
                      "filename": state["sources"][sid].get("filename"),
                      "status": state["sources"][sid].get("status")} for sid in source_ids]
        if job.get("input_sources"):
            frozen = {s["id"]: s.get("sha256") for s in job["input_sources"]}
            if any(sid not in frozen or frozen[sid] != state["sources"][sid].get("sha256") for sid in source_ids):
                raise DataError("任务资料版本已经变化，请创建更新任务")
        manifest = {"id": package_id, "course_id": course_id, "job_id": job.get("id"),
                    "course_name": state["course"]["name"], "mode": job.get("mode") or state["course"]["mode"],
                    "score_max": job.get("score_max", state["course"]["score_max"]),
                    "passing_score": job.get("passing_score", state["course"]["passing_score"]),
                    "status": "building", "chapters": plans, "source_ids": source_ids,
                    "input_sources": snapshots, "created_at": now(), "updated_at": now(), "warnings": [],
                    "original_question_ids": [qid for qid, q in state["questions"].items()
                                              if q.get("origin") != "ai_generated" and _sources(q).intersection(source_ids)]}
        with file_lock(self._root(course_id) / ".packages.lock"):
            directory = self._directory(course_id, package_id)
            directory.mkdir(parents=True, exist_ok=False)
            atomic_json(directory / "manifest.json", manifest)
            atomic_json(self._root(course_id) / "latest.json", {"package_id": package_id, "updated_at": now()})
            atomic_json(directory / "focus.json", [deepcopy(f) for f in state["focus"].values()
                                                   if f.get("status", "active") == "active"
                                                   and (f.get("source_type") == "user_designated"
                                                        or any(e.get("source_id") in source_ids for e in f.get("evidence", [])))])
        return manifest

    def _check_refs(self, state, manifest, plan, refs):
        if not isinstance(refs, list) or not refs:
            raise DataError("章节需要有效资料出处")
        result = []
        for ref in refs:
            if not isinstance(ref, dict) or ref.get("source_id") not in plan["source_ids"]:
                raise DataError("章节出处不在当前章节资料范围内")
            source = state["sources"][ref["source_id"]]
            if source.get("status") in ("pending", "failed"):
                raise DataError("未处理或失败的资料不能作为讲解依据")
            locator = ref.get("locator")
            if not isinstance(locator, dict) or not locator:
                raise DataError("资料出处需要实际页面、段落或时间戳")
            known = [p.get("number") for p in source.get("pages", [])]
            for kind in ("page", "slide", "text_block"):
                if kind in locator and locator[kind] not in known:
                    raise DataError("资料出处指向不存在的页面或文本块")
            if "paragraph" in locator and not any(s.get("locator", {}).get("paragraph") == locator["paragraph"]
                                                  for s in source.get("segments", [])):
                raise DataError("资料出处指向不存在的段落")
            timed = ("timestamp", "start", "time_start", "end", "time_end")
            for key in timed:
                if key in locator and not any(s.get("locator", {}).get(key) == locator[key]
                                              for s in source.get("segments", [])):
                    raise DataError("资料出处含有未提供的时间戳")
            if not any(k in locator for k in ("page", "slide", "text_block", "paragraph", *timed)):
                raise DataError("资料出处未提供可定位的页面、段落或时间戳")
            result.append(dict(deepcopy(ref), filename=source.get("filename", ref["source_id"])))
        return result

    def _prepare_question(self, state, plan, item):
        if not isinstance(item, dict):
            raise DataError("练习必须是对象")
        qid = item.get("question_id") or item.get("id")
        original = state["questions"].get(qid)
        if original and original.get("origin") != "ai_generated":
            if not _sources(original).intersection(plan["source_ids"]):
                raise DataError("原题不属于本章资料")
            if item.get("question") and item["question"].strip() != original["question"].strip():
                raise DataError("原题题干变化时应另存为AI变式")
            question = deepcopy(original)
            source_answer = original.get("answer")
            has_source_answer = source_answer is not None and bool(str(source_answer).strip())
            supplied_answer = item.get("answer")
            if has_source_answer:
                question["answer"] = deepcopy(source_answer)
                question["answer_origin"] = "source"
                question["original_reference_answer"] = deepcopy(source_answer)
                differs = (supplied_answer is not None and bool(str(supplied_answer).strip())
                           and str(supplied_answer).strip() != question["answer"]
                           and grade_answer(original, str(supplied_answer))["result"] != "right")
                if differs:
                    question["candidate_answer"] = _text(str(supplied_answer), "候选答案")
                    question["candidate_explanation"] = _text(item.get("explanation"), "候选解析")
                    question["answer_status"] = "needs_review"
                    question["answer_note"] = "资料答案与Agent候选答案不一致，尚待复核；保留资料答案，不据此确定评分。"
                    question["explanation"] = (original.get("explanation") or
                                                "此题出现答案分歧，请结合原题和资料复核后再确定解析。")
                else:
                    question["explanation"] = _text(item.get("explanation") or original.get("explanation"), "原题解析")
            else:
                question["answer"] = _text(supplied_answer, "原题缺失答案")
                question["explanation"] = _text(item.get("explanation"), "原题解析")
                question["answer_origin"] = "agent_generated"
            question["origin"] = original.get("origin", "original")
            question["id"] = qid
            return question, False
        if qid and qid not in state["questions"]:
            raise DataError("练习编号不属于当前课程")
        question = deepcopy(item)
        question["source_ids"] = question.get("source_ids") or list(plan["source_ids"])
        if not set(question["source_ids"]).issubset(plan["source_ids"]):
            raise DataError("练习引用了本章范围以外的资料")
        if not question.get("topic_ids") and not question.get("reference_ids"):
            question["topic_ids"] = [tid for tid, topic in state["topics"].items()
                                     if _sources(topic).intersection(question["source_ids"])][:10]
        return validate_generated(question, state), True

    def _answer_evidence(self, state, manifest, plan, question, item, chapter_refs):
        """Bind each prepared answer to actual course positions, preserving source keys."""
        question = deepcopy(question)
        source_answer = question.get('answer_origin') == 'source'
        raw_refs = item.get('answer_source_refs') or item.get('source_refs')
        if source_answer:
            sid = question.get('answer_source_id') or question.get('source_id')
            position = question.get('answer_source') or question.get('source') or {}
            locator = {k: v for k, v in position.items() if k in
                       ('page', 'slide', 'text_block', 'paragraph', 'timestamp', 'time_start')}
            kind = state['sources'].get(sid, {}).get('location_type')
            if 'page' in locator and kind in ('slide', 'text_block'):
                locator[kind] = locator.pop('page')
            if sid and locator:
                raw_refs = [{'source_id': sid, 'locator': locator}]
            elif not raw_refs:
                raw_refs = [r for r in chapter_refs if r['source_id'] == sid]
            question['answer_basis'] = 'source_answer'
        else:
            question['answer_basis'] = ('insufficient_material' if item.get('answer_basis') == 'insufficient_material'
                                        else 'course_material')
        refs = self._check_refs(state, manifest, plan, raw_refs or chapter_refs)
        question['answer_source_refs'] = refs
        question['answer_reference_precision'] = 'specific' if raw_refs else 'chapter'
        if question['answer_reference_precision'] == 'chapter':
            question['answer_note'] = (question.get('answer_note', '') + ' 当前列出章节级资料出处，具体知识位置仍需核对。').strip()
        if question['answer_basis'] == 'insufficient_material':
            question.update(answer_status='needs_support', independent_eligible=False,
                            answer_note='现有课程资料不足以可靠确定答案，暂不判定掌握。')
        return question

    def _focus_methods(self, state, manifest, plan, items, default_refs):
        if not isinstance(items, list):
            raise DataError("重点方法必须是列表")
        methods = []
        for item in items:
            if not isinstance(item, dict):
                raise DataError("重点方法必须是对象")
            title = _text(item.get("topic_title"), "方法知识点")
            ids = _strings(item.get("focus_ids", []), "方法重点关联", allow_empty=True)
            refs = self._check_refs(state, manifest, plan, item.get("source_refs") or default_refs)
            focus = []
            for fid in ids:
                actual = state["focus"].get(fid)
                if not actual or actual.get("status", "active") != "active":
                    raise DataError("方法关联的重点不存在、已撤回或不属于当前课程")
                if actual.get("topic_title", "").strip() != title:
                    raise DataError("方法知识点与关联重点不一致")
                if actual.get("source_type") != "user_designated" and not any(
                        e.get("source_id") in manifest["source_ids"] for e in actual.get("evidence", [])):
                    raise DataError("教师重点缺少本复习包选入资料的证据")
                focus.append(actual)
            claims = {f.get("claim_type", "user_priority") for f in focus}
            if ids:
                claim = item.get("claim_type") or ("exam_assured" if "exam_assured" in claims else
                                                   "priority" if "priority" in claims else "user_priority")
                if claim not in claims or claim not in ("exam_assured", "priority", "user_priority"):
                    raise DataError("方法重点类别超出实际重点证据")
                label = {"exam_assured": "老师明确会考", "priority": "老师重点", "user_priority": "用户重点"}[claim]
            else:
                if item.get("claim_type") not in (None, "suggestion", "review_suggestion") or item.get("label") in (
                        "老师明确会考", "老师重点", "用户重点", "必考"):
                    raise DataError("没有正式重点证据的方法只能标为复习建议")
                claim, label = "suggestion", "复习建议"
            example = item.get("example")
            if isinstance(example, dict):
                example = {"title": _text(example.get("title", "方法例题"), "方法例题标题"),
                           "question": _text(example.get("question"), "方法例题题干"),
                           "solution": _text(example.get("solution"), "方法例题步骤")}
            else:
                example = _text(example, "方法例题")
            methods.append({"topic_title": title, "focus_ids": ids, "claim_type": claim, "label": label,
                            "method_steps": _strings(item.get("method_steps", []), "解题方法步骤"),
                            "example": example, "source_refs": refs})
        return methods

    def write_chapter(self, course_id, package_id, chapter_id, content):
        self._chapter_id(chapter_id)
        if not isinstance(content, dict):
            raise DataError("章节内容必须是对象")
        with file_lock(self._root(course_id) / ".packages.lock"):
            manifest = self._manifest(course_id, package_id)
            if manifest["status"] == "complete":
                raise DataError("已发布的完整复习包不可修改，请创建新版本")
            plan = next((c for c in manifest["chapters"] if c["id"] == chapter_id), None)
            if not plan:
                raise DataError("章节不存在或不属于本复习包")
            state = self.store.read(course_id)
            chapter = {"id": chapter_id, "title": plan["title"], "status": "complete",
                       "source_ids": plan["source_ids"], "summary": _text(content.get("summary"), "章节总结", 8),
                       "explanation": _text(content.get("explanation"), "章节讲解", 20),
                       "mindmap": _mindmap(content.get("mindmap"), "章节导图"),
                       "pitfalls": _strings(content.get("pitfalls", []), "易错点"),
                       "warnings": _strings(content.get("warnings", []), "待核对说明", allow_empty=True),
                       "source_refs": self._check_refs(state, manifest, plan, content.get("source_refs")),
                       "updated_at": now()}
            raw_examples = content.get("examples")
            if not isinstance(raw_examples, list) or not raw_examples:
                raise DataError("章节至少需要一个有过程的典型例题")
            chapter["examples"] = [{"title": _text(e.get("title", "典型例题"), "例题标题"),
                                    "question": _text(e.get("question"), "例题题干"),
                                    "solution": _text(e.get("solution"), "例题解题过程")}
                                   for e in raw_examples if isinstance(e, dict)]
            if len(chapter["examples"]) != len(raw_examples):
                raise DataError("例题格式错误")
            chapter["focus_methods"] = self._focus_methods(state, manifest, plan, content.get("focus_methods", []),
                                                           chapter["source_refs"])
            raw_questions = content.get("questions")
            if not isinstance(raw_questions, list) or len(raw_questions) < plan["question_count"]:
                raise DataError(f"本章需要至少{plan['question_count']}道含答案和解析的练习")
            prepared = []
            for item in raw_questions:
                question, generated = self._prepare_question(state, plan, item)
                prepared.append((self._answer_evidence(state, manifest, plan, question, item,
                                                       chapter['source_refs']), generated))
            if len({q["id"] for q, _ in prepared}) != len(prepared):
                raise DataError("本章存在重复练习")
            # Validate the whole chapter before changing the shared question bank.
            chapter["questions"] = []
            for question, generated in prepared:
                if generated:
                    enriched = question
                    question = self.service.add_generated(course_id, question)
                    for key in ('answer_basis', 'answer_source_refs', 'answer_reference_precision',
                                'answer_note', 'answer_status', 'independent_eligible'):
                        if key in enriched:
                            question[key] = enriched[key]
                    with self.store.transaction(course_id) as current:
                        current['questions'][question['id']].update({key: deepcopy(question[key]) for key in
                            ('answer_basis', 'answer_source_refs', 'answer_reference_precision', 'answer_note',
                             'answer_status', 'independent_eligible') if key in question})
                chapter["questions"].append(_flag_visible_answer(question))
            atomic_json(self._chapter_path(course_id, package_id, chapter_id), chapter)
            _atomic_text(self._chapter_path(course_id, package_id, chapter_id, "md"), self._chapter_markdown(chapter))
            plan.update(status="complete", question_ids=[q["id"] for q in chapter["questions"]], updated_at=now())
            manifest["updated_at"] = now()
            if manifest["status"] == "partial":
                manifest["status"] = "building"
            atomic_json(self._directory(course_id, package_id) / "manifest.json", manifest)
            self._write_indexes(course_id, package_id)
            return chapter

    def write_overview(self, course_id, package_id, overview, mindmap):
        overview, mindmap = _text(overview, "全科概览", 20), _mindmap(mindmap, "全科导图")
        with file_lock(self._root(course_id) / ".packages.lock"):
            manifest = self._manifest(course_id, package_id)
            if manifest["status"] == "complete":
                raise DataError("已发布的完整复习包不可修改，请创建新版本")
            directory = self._directory(course_id, package_id)
            _atomic_text(directory / "overview.md", overview + "\n")
            _atomic_text(directory / "mindmap.mmd", mindmap + "\n")
            manifest["overview_ready"] = True
            manifest["updated_at"] = now()
            atomic_json(directory / "manifest.json", manifest)
            return manifest

    def _read_chapters(self, course_id, manifest):
        chapters = []
        for plan in manifest["chapters"]:
            if plan["status"] != "complete":
                continue
            try:
                chapter = json.loads(self._chapter_path(course_id, manifest["id"], plan["id"]).read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise DataError(f"章节内容无法读取：{plan['title']}；{exc}") from exc
            if chapter.get("id") != plan["id"]:
                raise DataError("章节内容编号异常")
            canonical_questions = self.store.read(course_id)['questions']
            for question in chapter['questions']:
                canonical = canonical_questions.get(question['id'], {})
                original_answer = canonical.get('answer')
                if (canonical.get('origin') != 'ai_generated' and original_answer is not None
                        and str(original_answer).strip() and question.get('answer') != original_answer):
                    question.update(candidate_answer=question.get('answer'),
                                    candidate_explanation=question.get('explanation'), answer=deepcopy(original_answer),
                                    original_reference_answer=deepcopy(original_answer), answer_origin='source',
                                    answer_status='needs_review', answer_basis='source_answer',
                                    explanation=canonical.get('explanation') or '旧版解析与资料答案存在分歧，保留资料原答案，等待质疑复核。')
            from .challenges import AnswerReviewManager
            reviews = AnswerReviewManager(self.service)
            chapter["questions"] = [reviews.overlay(course_id, manifest["id"], q) for q in chapter["questions"]]
            for question in chapter['questions']:
                if question.get('answer_review'):
                    question['answer_source_refs'] = question['answer_review']['source_refs']
                    question['answer_basis'] = 'course_material'
                    question['answer_reference_precision'] = 'specific'
                elif not question.get('answer_source_refs'):
                    question['answer_source_refs'] = deepcopy(chapter.get('source_refs', []))
                    question['answer_reference_precision'] = 'chapter'
                    question['answer_basis'] = 'source_answer' if question.get('answer_origin') == 'source' else 'course_material'
                    question['answer_note'] = (question.get('answer_note', '') + ' 旧资料包提供章节级出处，具体知识位置需核对。').strip()
            # A withdrawn marker does not remove useful methods, but it no longer
            # presents them as a current formal priority.
            live_focus = self.store.read(course_id)["focus"]
            for method in chapter.get("focus_methods", []):
                if method["focus_ids"] and not any(live_focus.get(fid, {}).get("status", "active") == "active"
                                                    and fid in live_focus for fid in method["focus_ids"]):
                    method.update(label="复习建议", claim_type="suggestion", focus_status="withdrawn")
            chapters.append(chapter)
        return chapters

    def _write_indexes(self, course_id, package_id):
        manifest = self._manifest(course_id, package_id)
        chapters = self._read_chapters(course_id, manifest)
        directory = self._directory(course_id, package_id)
        atomic_json(directory / "outline.json", [{k: c[k] for k in ("id", "title", "status", "source_ids")}
                                                for c in manifest["chapters"]])
        atomic_json(directory / "practice.json", [dict(q, chapter_id=c["id"], chapter_title=c["title"])
                                                 for c in chapters for q in c["questions"]])
        atomic_json(directory / "index.json", [{"chapter_id": c["id"], "title": c["title"],
                                               "text": "\n".join([c["summary"], c["explanation"], *c["pitfalls"],
                                                                    *[q["question"] for q in c["questions"]]])}
                                              for c in chapters])

    def _gaps(self, course_id, manifest, chapters):
        state = self.store.read(course_id)
        gaps = []
        covered = {ref["source_id"] for c in chapters for ref in c["source_refs"]}
        for item in manifest["input_sources"]:
            source = state["sources"].get(item["id"])
            if not source or source.get("sha256") != item.get("sha256"):
                gaps.append(f"资料版本已变化：{item.get('filename') or item['id']}")
            elif source.get("status") != "complete":
                gaps.append(f"资料未完整处理：{source.get('filename')}（{source.get('status')}）")
            if item["id"] not in covered:
                gaps.append(f"尚无章节覆盖：{item.get('filename') or item['id']}")
        for plan in manifest["chapters"]:
            if plan["status"] != "complete":
                gaps.append(f"章节未完成：{plan['title']}")
        received = {q["id"] for c in chapters for q in c["questions"]}
        missing = set(manifest.get("original_question_ids", [])) - received
        if missing:
            gaps.append(f"尚有{len(missing)}道原题未整理答案与解析")
        if not manifest.get("overview_ready"):
            gaps.append("全科概览或总导图未完成")
        for chapter in chapters:
            gaps.extend(f"{chapter['title']}：{w}" for w in chapter["warnings"])
            if any(q.get('answer_status') == 'needs_support' for q in chapter['questions']):
                gaps.append(f"{chapter['title']}：部分答案缺少课程资料依据，尚未确认")
        return list(dict.fromkeys(gaps))

    def publish(self, course_id, package_id, partial=False):
        with file_lock(self._root(course_id) / ".packages.lock"):
            manifest = self._manifest(course_id, package_id)
            if manifest["status"] == "complete":
                if partial:
                    raise DataError("已发布的完整复习包不可改为部分完成")
                return manifest
            chapters = self._read_chapters(course_id, manifest)
            gaps = self._gaps(course_id, manifest, chapters)
            if not partial and gaps:
                raise DataError("复习包未完整完成：" + "；".join(gaps[:8]))
            if partial and not chapters:
                raise DataError("没有可用章节，不能发布空复习包")
            manifest.update(status="partial" if gaps or partial else "complete", warnings=gaps,
                            updated_at=now(), published_at=now())
            self._write_indexes(course_id, package_id)
            atomic_json(self._directory(course_id, package_id) / "manifest.json", manifest)
            # Partial previews never replace a previously verified complete package.
            if manifest["status"] == "complete":
                atomic_json(self._root(course_id) / "current.json", {"package_id": package_id, "updated_at": now()})
            return manifest

    def read(self, course_id, package_id=None, preview=False):
        root = self._root(course_id)
        if package_id is None:
            if preview:
                latest = root / "latest.json"
                if latest.exists():
                    try:
                        package_id = json.loads(latest.read_text(encoding="utf-8"))["package_id"]
                    except (OSError, ValueError, KeyError) as exc:
                        raise DataError("最新复习包指向损坏，已保留文件") from exc
                candidates = []
                if package_id is None:
                    for path in root.glob("pkg_*/manifest.json"):
                        if re.fullmatch(r"pkg_[0-9a-f]{12}", path.parent.name):
                            candidate = self._manifest(course_id, path.parent.name)
                            candidates.append(candidate)
                    if not candidates:
                        return None
                    package_id = max(candidates, key=lambda m: (m.get("created_at", ""), m["id"]))["id"]
            else:
                pointer = root / "current.json"
                if not pointer.exists():
                    return None
                try:
                    package_id = json.loads(pointer.read_text(encoding="utf-8"))["package_id"]
                except (OSError, ValueError, KeyError) as exc:
                    raise DataError("当前复习包指向损坏，已保留文件") from exc
        manifest = self._manifest(course_id, package_id)
        chapters = self._read_chapters(course_id, manifest)
        directory = self._directory(course_id, package_id)
        def read_text(name):
            path = directory / name
            return path.read_text(encoding="utf-8") if path.exists() else ""
        focus_path = directory / "focus.json"
        focus = json.loads(focus_path.read_text(encoding="utf-8")) if focus_path.exists() else []
        practice = [dict(q, chapter_id=c["id"], chapter_title=c["title"]) for c in chapters for q in c["questions"]]
        return {"manifest": manifest, "chapters": chapters, "overview": read_text("overview.md"),
                "mindmap": read_text("mindmap.mmd"), "focus": focus, "practice": practice,
                "focus_methods": [dict(method, chapter_id=c["id"], chapter_title=c["title"])
                                  for c in chapters for method in c.get("focus_methods", [])]}

    def search(self, course_id, query, package_id=None):
        query = _text(query, "搜索词")
        package = self.read(course_id, package_id)
        if package is None:
            return []
        result = []
        tokens = query.casefold().split()
        for chapter in package["chapters"]:
            text = "\n".join((chapter["summary"], chapter["explanation"], *chapter["pitfalls"],
                               *[e["question"] + "\n" + e["solution"] for e in chapter["examples"]],
                               *[q["question"] for q in chapter["questions"]]))
            haystack = (chapter["title"] + "\n" + text).casefold()
            if not all(token in haystack for token in tokens):
                continue
            position = min((text.casefold().find(token) for token in tokens if token in text.casefold()), default=0)
            begin = max(0, position - 45)
            result.append({"chapter_id": chapter["id"], "title": chapter["title"],
                           "text": text[begin:begin + 240], "package_id": package["manifest"]["id"]})
        return result[:100]

    @staticmethod
    def _chapter_markdown(chapter):
        lines = [f"# {chapter['title']}", "", chapter["summary"], "", "## 知识讲解", "", chapter["explanation"],
                 "", "## 知识导图", "", "```mermaid", chapter["mindmap"], "```", "", "## 典型例题", ""]
        for example in chapter["examples"]:
            lines += [f"### {example['title']}", "", example["question"], "", example["solution"], ""]
        if chapter.get("focus_methods"):
            lines += ["## 重点与复习方法", ""]
            for method in chapter["focus_methods"]:
                lines += [f"### {method['topic_title']}", "", f"类别：{method['label']}", ""]
                lines += [f"{i}. {step}" for i, step in enumerate(method["method_steps"], 1)] + [""]
                example = method["example"]
                if isinstance(example, dict):
                    lines += [example["question"], "", example["solution"], ""]
                else:
                    lines += [example, ""]
        lines += ["## 易错点", "", *[f"- {pitfall}" for pitfall in chapter["pitfalls"]], "", "## 资料出处", ""]
        for ref in chapter["source_refs"]:
            lines.append(f"- {ref.get('filename', '课程资料')}：{_position(ref['locator'])}")
        if chapter["warnings"]:
            lines += ["", "## 待核对", "", *[f"- {warning}" for warning in chapter["warnings"]]]
        return "\n".join(lines) + "\n"

    @staticmethod
    def _current_focus(state, selected_sources):
        result = []
        for item in state["focus"].values():
            if item.get("status", "active") != "active":
                continue
            focus = deepcopy(item)
            if focus.get("source_type") != "user_designated":
                focus["evidence"] = [e for e in focus.get("evidence", []) if e.get("source_id") in selected_sources]
                if not focus["evidence"]:
                    continue
            result.append(focus)
        return result

    def _learning_records(self, course_id, state, package, exported_at):
        questions = {q["id"]: q for q in package["practice"]}
        titles = {c["id"]: c["title"] for c in package["manifest"]["chapters"]}
        attempts = []
        for attempt in state["attempts"]:
            record = deepcopy(attempt)
            question = questions.get(record.get("question_id")) or state["questions"].get(record.get("question_id"), {})
            record["question"] = question.get("question", "历史练习")
            record["chapter_title"] = question.get("chapter_title", "")
            record["in_package"] = record.get("question_id") in questions
            current = questions.get(record.get("question_id"), {}).get("reference_version")
            record["current_reference_version"] = current
            record["reference_changed"] = bool(current and record.get("reference_version")
                                                and current != record["reference_version"])
            attempts.append(record)
        reading = deepcopy(state.get("workbench", {}).get("reading", {}))
        for chapter_id, value in reading.items():
            value["chapter_title"] = titles.get(chapter_id, value.get("chapter_title") or "历史章节")
        responses, warnings = [], []
        for path in sorted(self.store.course_dir(course_id).glob("jobs/job_*/response.json")):
            if not re.fullmatch(r"job_[0-9a-f]{12}", path.parent.name):
                continue
            target = self.store.contained(course_id, f"jobs/{path.parent.name}/response.json")
            try:
                response = json.loads(target.read_text(encoding="utf-8"))
                if not isinstance(response, dict):
                    raise ValueError("答疑记录不是对象")
            except (OSError, ValueError):
                warnings.append("一份已保存答疑无法读取，原记录仍保留。")
                continue
            responses.append(response)
        return _without_credentials({
            "course_id": course_id, "course_name": state["course"]["name"],
            "package_id": package["manifest"]["id"], "learning_revision": state["revision"],
            "state_schema_version": state["version"], "exported_at": exported_at,
            "reading": reading, "last_chapter": state.get("workbench", {}).get("last_chapter"),
            "progress": state["progress"], "attempts": attempts, "notes": state["notes"],
            "responses": responses, "warnings": warnings,
        })

    @staticmethod
    def _learning_markdown(records):
        labels = {"read": "已阅读", "self_assessed": "自评已理解", "explained": "已讲解",
                  "needs_review": "待复习", "practice_passed": "独立练习通过"}
        result_labels = {"right": "本次回答正确", "wrong": "本次回答需改正", "unsure": "需要进一步评阅"}
        lines = [f"# {records['course_name']}学习记录", "", "## 章节阅读", ""]
        if not records["reading"]:
            lines += ["暂无章节阅读标记。", ""]
        for record in records["reading"].values():
            lines += [f"- {record['chapter_title']}：{labels.get(record.get('status'), '已保存学习标记')}"]
        lines += ["", "## 待复习清单", ""]
        pending = [a for a in records["attempts"] if a.get("result") in ("wrong", "unsure") or a["reference_changed"]]
        if not pending:
            lines += ["当前没有已记录的错题或待评阅作答。", ""]
        for attempt in pending:
            reason = "参考解析已更新" if attempt["reference_changed"] else result_labels.get(attempt.get("result"), "待复习")
            lines += [f"- {attempt['question']}：{reason}。"]
        lines += ["", "## 作答与待复习内容", ""]
        if not records["attempts"]:
            lines += ["暂无作答记录。", ""]
        for attempt in records["attempts"]:
            heading = attempt.get("chapter_title") or "练习记录"
            conditions = ("看过解析后作答" if attempt.get("answer_viewed") else
                          "使用提示后作答" if attempt.get("used_hint") else
                          "独立作答" if attempt.get("independent") else "已保存作答")
            reference = ("历史解析版本，当前参考已更新，建议重做。" if attempt["reference_changed"] else
                         "当前复习包参考版本。" if attempt["in_package"] and attempt.get("reference_version") else
                         "历史记录，未提供可比较的当前参考版本。")
            lines += [f"### {heading}", "", str(attempt["question"]), "",
                      f"你的作答：{attempt.get('response', '')}", "",
                      f"反馈：{result_labels.get(attempt.get('result'), '已保存反馈')}；{conditions}。", "",
                      f"参考：{reference}", ""]
            feedback = attempt.get("feedback") or {}
            if feedback.get("reason"):
                lines += [str(feedback["reason"]), ""]
        lines += ["## 笔记与复习记录", ""]
        readable_notes = [note for note in records["notes"] if isinstance(note, dict)
                          and any(note.get(key) for key in ("summary", "text", "content", "user_feedback", "assessment"))]
        if not readable_notes:
            lines += ["暂无额外笔记。", ""]
        for note in readable_notes:
            lines += [f"### {note.get('title') or '复习笔记'}", ""]
            for key in ("summary", "text", "content", "user_feedback", "assessment"):
                if note.get(key):
                    lines += [str(note[key]), ""]
        lines += ["## 已保存答疑", ""]
        if not records["responses"]:
            lines += ["暂无已保存答疑。", ""]
        for response in records["responses"]:
            lines += [f"### {response.get('request') or '课程答疑'}", "",
                      str(response.get("text") or response.get("answer") or "答疑正文未提供。"), ""]
        if records["warnings"]:
            lines += ["## 记录读取说明", "", *records["warnings"], ""]
        return "\n".join(lines)

    def export(self, course_id, package_id=None):
        package = self.read(course_id, package_id)
        if package is None:
            raise DataError("还没有已发布的复习包")
        manifest = package["manifest"]
        state = self.store.read(course_id)
        exported_at = now()
        current_focus = self._current_focus(state, set(manifest["source_ids"]))
        export_id = "package_export_" + uuid.uuid4().hex[:12]
        directory = self.store.contained(course_id, f"exports/{export_id}")
        directory.mkdir(parents=True, exist_ok=False)
        notice = ""
        if manifest["status"] != "complete":
            notice = "# 本次资料部分完成\n\n" + "\n".join(f"- {w}" for w in manifest["warnings"]) + "\n\n"
        _atomic_text(directory / "overview.md", notice + package["overview"])
        _atomic_text(directory / "mindmap.mmd", package["mindmap"])
        for chapter in package["chapters"]:
            _atomic_text(directory / "chapters" / f"{chapter['id']}.md", self._chapter_markdown(chapter))
        focus_lines = ["# 重点清单", "", "按导出时的有效标记整理；已撤回的重点不再列入。", ""]
        if not current_focus:
            focus_lines += ["本次资料中暂无已确认的正式重点。复习建议不等同于老师会考。", ""]
        for focus in current_focus:
            label = "用户重点" if focus.get("source_type") == "user_designated" else (
                "老师明确会考" if focus.get("claim_type") == "exam_assured" else "老师重点")
            focus_lines += [f"## {focus.get('topic_title', '')}", "", f"类别：{label}", ""]
            for evidence in focus.get("evidence", []):
                sid = evidence.get("source_id")
                source_name = (state["sources"].get(sid, {}).get("filename") or "课程资料") if sid else "用户直接指定"
                focus_lines += [str(evidence.get("quote") or evidence.get("text") or ""), "",
                                f"出处：{source_name}" + (f"；{_position(evidence.get('locator', {}))}" if sid else ""), ""]
        _atomic_text(directory / "exam_focus.md", "\n".join(focus_lines))
        atomic_json(directory / "focus.json", _without_credentials(current_focus))
        practice_lines = ["# 练习", ""]
        filenames = {s["id"]: s.get("filename", s["id"]) for s in manifest["input_sources"]}
        chapter_refs = {chapter["id"]: chapter["source_refs"] for chapter in package["chapters"]}
        for i, q in enumerate(package["practice"], 1):
            label = "AI生成练习" if q.get("origin") == "ai_generated" else "原题"
            source_names = []
            for sid in sorted(_sources(q)):
                refs = [ref for ref in chapter_refs.get(q["chapter_id"], []) if ref["source_id"] == sid]
                source_names.append((filenames.get(sid) or "课程资料") +
                                    ("（" + "；".join(_position(ref["locator"]) for ref in refs) + "）" if refs else ""))
            source_names = "、".join(source_names)
            practice_lines += [f"## {i}. {q['chapter_title']}", "", f"{label}；来源：{source_names}", "",
                               q["question"], "", *q.get("options", []), ""]
            if q.get("practice_note"):
                practice_lines += [q["practice_note"], ""]
        practice_lines += ["# 参考答案与解析", ""]
        for i, q in enumerate(package["practice"], 1):
            provenance = ("答案与解析已根据用户质疑重新核对。" if q.get("answer_origin") == "agent_rechecked" else
                          "答案与解析由Agent生成。" if q.get("origin") == "ai_generated" or q.get("answer_origin") == "agent_generated"
                          else "参考答案保留资料来源；解析由Agent整理。")
            practice_lines += [f"## 第{i}题", "", provenance, "", str(q["answer"]), "", str(q["explanation"]), ""]
            if q.get("answer_status") == "needs_review":
                practice_lines += [q["answer_note"], "", f"待核对候选答案：{q['candidate_answer']}", "",
                                   q.get("candidate_explanation", ""), ""]
            if q.get("answer_review"):
                review = q["answer_review"]
                practice_lines += [f"原参考答案：{q.get('original_reference_answer', '')}", "",
                                   f"复核原因：{review['reason']}", ""]
                for ref in review["source_refs"]:
                    practice_lines += [f"复核出处：{ref.get('filename', '课程资料')}；{_position(ref['locator'])}", ""]
        practice_lines += ['## 答案依据', '']
        for index, question in enumerate(package['practice'], 1):
            label = '资料原答案' if question.get('answer_basis') == 'source_answer' else '依据本课程资料整理'
            practice_lines += [f'### 第{index}题 · {label}', '']
            for reference in question.get('answer_source_refs', []):
                practice_lines += [f"- {reference.get('filename', '课程资料')}；{_position(reference.get('locator', {}))}"]
            if question.get('answer_reference_precision') == 'chapter':
                practice_lines += ['章节级出处，具体知识位置仍需核对。']
            practice_lines.append('')
        _atomic_text(directory / "practice.md", "\n".join(practice_lines))
        atomic_json(directory / "practice.json", package["practice"])
        learning = self._learning_records(course_id, state, package, exported_at)
        atomic_json(directory / "学习记录.json", learning)
        _atomic_text(directory / "学习记录.md", self._learning_markdown(learning))
        focus_times = [f.get("updated_at") or f.get("created_at") for f in state["focus"].values()]
        export_manifest = {"export_id": export_id, "package_id": manifest["id"], "course_id": course_id,
                           "status": manifest["status"], "warnings": manifest["warnings"], "exported_at": exported_at,
                           "learning_revision": state["revision"], "state_schema_version": state["version"],
                           "focus_snapshot_at": exported_at, "focus_updated_at": max((t for t in focus_times if t), default=None),
                           "focus_note": "重点采用导出时课程记录；教师证据限定为本包选入资料，用户指定重点采用全科有效记录。复习包本体保留生成时快照。",
                           "files": sorted(str(p.relative_to(directory)).replace("\\", "/") for p in directory.rglob("*") if p.is_file())}
        export_manifest["files"].append("export_manifest.json")
        atomic_json(directory / "export_manifest.json", export_manifest)
        return {**export_manifest, "directory": str(directory)}
