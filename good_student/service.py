"""Application operations. Every operation receives an explicit course ID."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import os
from pathlib import Path
import uuid

from . import focus as focus_tools
from . import ingest
from .storage import DataError, Store, file_lock, now, score_settings
from .vendor.coach import index as index_tools
from .vendor.coach.text import pack


class StudentService:
    def __init__(self, data_dir):
        self.store = Store(data_dir)

    def _event(self, state, action, **details):
        state["events"].append({"action": action, "at": now(), **details})

    @staticmethod
    def _source_metadata(source):
        return {**{key: deepcopy(value) for key, value in source.items() if key not in ("pages", "segments")},
                "page_count": len(source.get("pages", [])), "segment_count": len(source.get("segments", []))}

    def _refresh(self, state):
        from .knowledge import build_knowledge
        from .practice import build_bank
        topics = build_knowledge(state["sources"], state["focus"])
        for key, old in state["topics"].items():
            if old.get("user_defined") and key not in topics:
                topics[key] = old
            elif key in topics:
                topics[key]["prerequisite_ids"] = old.get("prerequisite_ids", [])
        state["topics"] = topics
        generated = {key: value for key, value in state["questions"].items()
                     if value.get("origin") == "ai_generated"}
        state["questions"] = {**build_bank(state["sources"]), **generated}

    def _merge_focus(self, state, item):
        key = item["id"]
        existing = state["focus"].get(key)
        if existing:
            new_evidence = False
            for evidence in item["evidence"]:
                if evidence not in existing["evidence"]:
                    existing["evidence"].append(evidence)
                    new_evidence = True
            # Explicitly adding an already retracted user focus reactivates it.
            if item["source_type"] == "user_designated":
                existing["status"] = "active"
            elif new_evidence and existing["status"] == "retracted":
                existing["status"] = "conflicted"
            for field in ("candidate_context", "confirmed_candidate_id"):
                if field in item:
                    existing[field] = item[field]
            existing["updated_at"] = now()
        else:
            state["focus"][key] = {**item, "created_at": now(), "updated_at": now()}
        return state["focus"][key]

    def set_mode(self, course_id, mode, score_max=None, passing_score=None):
        if mode not in ("high", "pass"):
            raise DataError("模式应为 high 或 pass")
        with self.store.transaction(course_id) as state:
            course = state["course"]
            maximum, target = score_settings(
                course["score_max"] if score_max is None else score_max,
                course["passing_score"] if passing_score is None else passing_score)
            course.update(mode=mode, score_max=maximum, passing_score=target)
            self._event(state, "mode_changed", mode=mode, passing_score=target, score_max=maximum)
        return state["course"]

    def import_material(self, course_id, path, kind, *, speaker="unknown", lecture_id=None, lesson_name=None,
                        auto_process=True):
        result = self._register_material(course_id, path, kind, speaker=speaker,
                                         lecture_id=lecture_id, lesson_name=lesson_name)
        source = result["source"]
        if auto_process and source.get("media_task") and source["status"] != "complete":
            processed = self.process_source(course_id, source["id"])
            result.update(processed)
        return result

    def _register_material(self, course_id, path, kind, *, speaker="unknown", lecture_id=None, lesson_name=None):
        if speaker not in ("teacher", "student", "unknown"):
            raise DataError("说话人应为 teacher、student 或 unknown")
        self.store.read(course_id)  # reject bad course before reading input
        source, raw = ingest.prepare(path, kind)
        with self.store.transaction(course_id) as state:
            if lecture_id and lecture_id not in state["lectures"]:
                raise DataError("指定课次不存在或不属于当前课程")
            if source["id"] in state["sources"]:
                existing = state["sources"][source["id"]]
                if lecture_id and existing.get("lecture_id") != lecture_id:
                    raise DataError("该资料已归属另一个课次，未重复创建或变更归属")
                if source.get("media_task"):
                    existing.setdefault("media_task", source["media_task"])
                    existing.setdefault("location_type", source["location_type"])
                return {"duplicate": True, "source": self._source_metadata(existing),
                        "lecture_id": existing.get("lecture_id")}
            if kind in ("audio", "transcript", "summary") and lecture_id is None:
                lecture_id = ingest.stable_id("lec_", source["id"])
                state["lectures"][lecture_id] = {
                    "id": lecture_id, "name": lesson_name or source["filename"],
                    "source_ids": [], "status": source["status"], "summary": "",
                    "summary_kind": "extractive_preview", "created_at": now(),
                }
            source.update(imported_at=now(), declared_speaker=speaker, lecture_id=lecture_id)
            original = self.store.contained(course_id, source["stored_path"])
            original.parent.mkdir(parents=True, exist_ok=True)
            if original.exists() and hashlib.sha256(original.read_bytes()).hexdigest() != source["sha256"]:
                raise DataError("原始资料归档存在内容冲突，未覆盖")
            if not original.exists():
                original.write_bytes(raw)
            state["sources"][source["id"]] = source
            if lecture_id:
                lecture = state["lectures"][lecture_id]
                lecture["source_ids"].append(source["id"])
                statuses = [state["sources"][sid]["status"] for sid in lecture["source_ids"]]
                lecture["status"] = "complete" if all(s == "complete" for s in statuses) else "partial"
                if lecture["summary_kind"] == "extractive_preview":
                    lecture["summary"] = "\n\n".join(
                        ingest.extractive_summary(state["sources"][sid]) for sid in lecture["source_ids"])
                else:
                    lecture["summary_stale"] = True
            formal, candidates = focus_tools.extract_focus(source["id"], source["segments"], kind, speaker)
            added = 0
            for item in formal:
                added += int(item["id"] not in state["focus"])
                self._merge_focus(state, item)
            for item in candidates:
                state["candidates"].setdefault(item["id"], item)
            # Negative scope updates are evidence about an existing claim,
            # never a new positive exam priority or a change to user priorities.
            for change in focus_tools.extract_scope_updates(source["id"], source["segments"], kind, speaker):
                for item in state["focus"].values():
                    if item["topic_id"] == change["topic_id"] and item["source_type"] == "teacher_explicit":
                        item["status"] = "retracted" if change["action"] == "retract" else "conflicted"
                        item.setdefault("scope_changes", []).append(change)
                        item["updated_at"] = now()
                        self._event(state, "teacher_scope_updated", focus_id=item["id"], source_id=source["id"])
            self._refresh(state)
            state["next_task"] = "复核候选重点，分段阅读课堂记录并保存知识总结"
            self._event(state, "source_imported", source_id=source["id"], lecture_id=lecture_id)
            result = {"duplicate": False, "source": self._source_metadata(source), "lecture_id": lecture_id,
                      "new_focus": added, "new_candidates": len(candidates),
                      "summary_kind": "extractive_preview" if lecture_id else None}
        return result

    def process_source(self, course_id, source_id, on_progress=None):
        """Run a long media operation outside the short course-state transaction."""
        snapshot = self.store.read(course_id)
        source = snapshot["sources"].get(source_id)
        if not source:
            raise DataError("资料不存在或不属于当前课程")
        task = source.get("media_task")
        if not task:
            suffix = Path(source.get("stored_path", "")).suffix.lower()
            task = "transcribe" if source.get("kind") == "audio" else "ocr" if suffix in ingest.IMAGES | {".pdf"} else None
        if task not in ("transcribe", "ocr"):
            raise DataError("该资料不需要录音转写或扫描识别")
        original = self.store.contained(course_id, source["stored_path"])
        work = self.store.contained(course_id, "processing/" + source_id)
        work.mkdir(parents=True, exist_ok=True)
        with file_lock(work / ".processing.lock", timeout=1):
            if task == "transcribe":
                from .audio import transcribe_audio
                language = os.environ.get("GOOD_STUDENT_AUDIO_LANGUAGE", "zh")
                output = transcribe_audio(original, work, language=language)
            else:
                from .ocr import process_document
                output = process_document(original, work, on_progress=on_progress) if on_progress else process_document(original, work)
        if output.get("status") not in ("complete", "partial", "pending", "failed"):
            raise DataError("媒体处理器返回了无效状态，原资料仍已保存")
        with self.store.transaction(course_id) as state:
            current = state["sources"][source_id]
            current["media_task"] = task
            for key in ("pages", "segments", "status", "warnings", "processing", "page_results", "completed_ranges", "failed_ranges"):
                if key in output:
                    current[key] = deepcopy(output[key])
            current["processed_at"] = now()
            kind = "transcript" if task == "transcribe" else current["kind"]
            formal, candidates = focus_tools.extract_focus(source_id, current["segments"], kind,
                                                           current.get("declared_speaker", "unknown"))
            added = 0
            for item in formal:
                added += int(item["id"] not in state["focus"])
                self._merge_focus(state, item)
            for item in candidates:
                state["candidates"].setdefault(item["id"], item)
            for change in focus_tools.extract_scope_updates(source_id, current["segments"], kind,
                                                             current.get("declared_speaker", "unknown")):
                for item in state["focus"].values():
                    if item["topic_id"] == change["topic_id"] and item["source_type"] == "teacher_explicit":
                        item["status"] = "retracted" if change["action"] == "retract" else "conflicted"
                        if change not in item.setdefault("scope_changes", []):
                            item["scope_changes"].append(change)
            lecture_id = current.get("lecture_id")
            if lecture_id:
                lecture = state["lectures"][lecture_id]
                statuses = [state["sources"][sid]["status"] for sid in lecture["source_ids"]]
                lecture["status"] = "complete" if all(s == "complete" for s in statuses) else "partial"
                if lecture["summary_kind"] == "extractive_preview":
                    lecture["summary"] = "\n\n".join(ingest.extractive_summary(state["sources"][sid])
                                                      for sid in lecture["source_ids"])
                else:
                    lecture["summary_stale"] = True
            self._refresh(state)
            self._event(state, "media_processed", source_id=source_id, status=current["status"])
            result = {"source": self._source_metadata(current), "lecture_id": lecture_id,
                      "new_focus": added, "new_candidates": len(candidates)}
        return result

    def add_focus(self, course_id, title, note):
        item = focus_tools.make_user_focus(title, note)
        with self.store.transaction(course_id) as state:
            item = self._merge_focus(state, item)
            self._refresh(state)
            self._event(state, "user_focus_added", focus_id=item["id"])
        return item

    def confirm_focus(self, course_id, candidate_id, title, note):
        with self.store.transaction(course_id) as state:
            candidate = state["candidates"].get(candidate_id)
            if not candidate:
                raise DataError("候选重点不存在或不属于当前课程")
            item = focus_tools.make_user_focus(title, note, candidate)
            item = self._merge_focus(state, item)
            candidate.update(status="confirmed", confirmed_focus_id=item["id"])
            self._refresh(state)
            self._event(state, "candidate_confirmed_as_user_focus", candidate_id=candidate_id)
        return item

    def retract_focus(self, course_id, focus_id, reason):
        if not reason.strip():
            raise DataError("请记录撤回原因")
        with self.store.transaction(course_id) as state:
            item = state["focus"].get(focus_id)
            if not item:
                raise DataError("重点不存在或不属于当前课程")
            item.update(status="retracted", retraction_reason=reason, updated_at=now())
            self._event(state, "focus_retracted", focus_id=focus_id, reason=reason)
        return item

    def save_summary(self, course_id, lecture_id, text, source_ids=None):
        if not text.strip():
            raise DataError("总结不能为空")
        with self.store.transaction(course_id) as state:
            lecture = state["lectures"].get(lecture_id)
            if not lecture:
                raise DataError("课次不存在或不属于当前课程")
            ids = source_ids or lecture["source_ids"]
            if not ids or any(sid not in lecture["source_ids"] for sid in ids):
                raise DataError("总结引用必须属于当前课次")
            if not any(state["sources"][sid]["segments"] for sid in ids):
                raise DataError("该课次尚无文本证据，不能根据未转写录音生成总结")
            lecture.update(summary=text, summary_kind="agent_summary", summary_source_ids=list(ids),
                           summary_stale=False, summary_updated_at=now())
            self._event(state, "summary_saved", lecture_id=lecture_id)
        return lecture

    def source_slice(self, course_id, source_id, start=0, limit=30):
        state = self.store.read(course_id)
        source = state["sources"].get(source_id)
        if not source:
            raise DataError("资料不存在或不属于当前课程")
        if start < 0 or not 1 <= limit <= 200:
            raise DataError("start 不得小于零，limit 应在 1 到 200 之间")
        segments = source["segments"]
        return {"source_id": source_id, "filename": source["filename"], "kind": source["kind"],
                "status": source["status"], "total_segments": len(segments), "start": start,
                "next_start": start + limit if start + limit < len(segments) else None,
                "material_data": segments[start:start + limit]}

    def search(self, course_id, query, limit=5):
        state = self.store.read(course_id)
        chunks = []
        for source in state["sources"].values():
            for page in source["pages"]:
                for piece in pack(page["text"], 600):
                    chunks.append({"source_id": source["id"], "file": source["filename"],
                                   "page": page["number"], "chapter": None, "text": piece})
        if not 1 <= limit <= 30:
            raise DataError("检索条数应在 1 到 30 之间")
        return [{**chunk, "retrieval_score": score} for chunk, score in index_tools.search(chunks, query, limit)]

    def link_prerequisite(self, course_id, topic_id, prerequisite_id):
        with self.store.transaction(course_id) as state:
            topics = state["topics"]
            if topic_id not in topics or prerequisite_id not in topics:
                raise DataError("知识点不属于当前课程")
            def reaches(start, target, seen):
                if start == target:
                    return True
                if start in seen:
                    return False
                seen.add(start)
                return any(reaches(child, target, seen) for child in topics[start].get("prerequisite_ids", []))
            if reaches(prerequisite_id, topic_id, set()):
                raise DataError("前置关系会形成循环")
            refs = topics[topic_id].setdefault("prerequisite_ids", [])
            if prerequisite_id not in refs:
                refs.append(prerequisite_id)
        return topics[topic_id]

    def add_generated(self, course_id, record):
        from .practice import validate_generated
        with self.store.transaction(course_id) as state:
            item = validate_generated(record, state)
            state["questions"].setdefault(item["id"], {**item, "created_at": now()})
            self._event(state, "generated_practice_saved", question_id=item["id"])
        return item

    @staticmethod
    def _reference_question(question, reference):
        if reference is None:
            return deepcopy(question)
        if (not isinstance(reference, dict) or reference.get("id") != question["id"]
                or reference.get("question") != question["question"]):
            raise DataError("复习包题目与课程原题不一致")
        # Only prepared answers/metadata may override the immutable source item.
        result = deepcopy(question)
        for key in ("answer", "explanation", "answer_origin", "original_reference_answer",
                    "reference_package_id", "reference_version", "answer_visible_in_stem",
                    "independent_eligible", "practice_note", "answer_status", "answer_note",
                    "candidate_answer", "candidate_explanation", "answer_review", "answer_reviews",
                    "answer_source_refs", "answer_basis", "answer_reference_precision", "answer_provenance"):
            if key in reference:
                result[key] = deepcopy(reference[key])
        return result

    @staticmethod
    def _reference_version(question):
        if question.get("reference_version"):
            return question["reference_version"]
        import json
        text = json.dumps({"answer": question.get("answer"), "explanation": question.get("explanation")},
                          ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "ref_" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _remember_answer_view(state, question_id, version):
        previous = state["question_views"].get(question_id, {})
        versions = deepcopy(previous.get("versions", {}))
        versions[version] = {"answer_viewed": True, "at": now()}
        state["question_views"][question_id] = {"answer_viewed": True, "at": now(),
                                                 "reference_version": version, "versions": versions}

    def show_question(self, course_id, question_id, show_answer=False, reference=None):
        with self.store.transaction(course_id) as state:
            question = state["questions"].get(question_id)
            if not question:
                raise DataError("题目不存在或不属于当前课程")
            result = self._reference_question(question, reference)
            if show_answer:
                self._remember_answer_view(state, question_id, self._reference_version(result))
            else:
                for key in ("answer", "explanation", "answer_source", "answer_source_id"):
                    result.pop(key, None)
        return result

    def answer(self, course_id, question_id, response, used_hint=False, tolerance=None, reference=None):
        from .practice import grade_answer
        if not response.strip():
            raise DataError("作答不能为空")
        with self.store.transaction(course_id) as state:
            question = state["questions"].get(question_id)
            if not question:
                raise DataError("题目不存在或不属于当前课程")
            question = self._reference_question(question, reference)
            result = grade_answer(question, response, tolerance)
            if question.get("answer_status") in ("needs_review", "needs_support", "pending_generation"):
                result.update(result="unsure", reason="答案存在疑点或课程资料依据不足，请先复核答案与出处。")
            version = self._reference_version(question)
            view = state["question_views"].get(question_id, {})
            if view.get("versions") is not None:
                viewed = bool(view["versions"].get(version, {}).get("answer_viewed"))
            else:
                viewed = bool(view.get("answer_viewed")) and view.get("reference_version", version) == version
            viewed = viewed or bool(question.get("answer_visible_in_stem"))
            independent = not viewed and not used_hint and question.get("independent_eligible", True)
            attempt = {"id": "a_" + uuid.uuid4().hex, "course_id": course_id,
                       "question_id": question_id, "response": response, "used_hint": bool(used_hint),
                       "answer_viewed": viewed, "independent": independent,
                       "result": result["result"], "feedback": result, "at": now()}
            attempt["reference_package_id"] = question.get("reference_package_id")
            attempt["reference_version"] = version
            state["attempts"].append(attempt)
            # The returned feedback reveals the reference; later attempts retain that fact.
            self._remember_answer_view(state, question_id, version)
            for topic_id in question.get("topic_ids", []):
                if topic_id in state["topics"]:
                    previous = state["progress"].get(topic_id, {})
                    if result["result"] == "right" and independent:
                        state["progress"][topic_id] = {"status": "practice_passed", "attempt_id": attempt["id"],
                                                        "question_id": question_id, "reference_version": version, "at": now()}
                    elif (result["result"] != "wrong" and previous.get("status") == "practice_passed"
                          and (previous.get("question_id") != question_id or previous.get("reference_version") == version)):
                        previous["last_attempt_id"] = attempt["id"]
                    else:
                        state["progress"][topic_id] = {"status": "needs_review", "attempt_id": attempt["id"], "at": now()}
            state["next_task"] = "复习错题和未覆盖知识；当前练习结果不代表真实考试分数"
        return attempt

    def mark_progress(self, course_id, topic_id, status):
        if status not in ("read", "explained", "needs_review"):
            raise DataError("只能手动记录 read、explained 或 needs_review，练习通过由独立作答产生")
        with self.store.transaction(course_id) as state:
            if topic_id not in state["topics"]:
                raise DataError("知识点不属于当前课程")
            state["progress"][topic_id] = {"status": status, "basis": "agent_or_user_report", "at": now()}
        return state["progress"][topic_id]

    def status(self, course_id):
        from .knowledge import compute_coverage
        state = self.store.read(course_id)
        return {"course": state["course"], "revision": state["revision"],
                "sources": [{key: source.get(key) for key in ("id", "filename", "kind", "status", "warnings", "lecture_id")}
                            for source in state["sources"].values()],
                "lectures": [{key: lecture.get(key) for key in ("id", "name", "status", "summary_kind", "summary_stale")}
                             for lecture in state["lectures"].values()],
                "active_focus": sum(item["status"] == "active" for item in state["focus"].values()),
                "pending_candidates": sum(item["status"] == "pending" for item in state["candidates"].values()),
                "topics": len(state["topics"]), "questions": len(state["questions"]),
                "attempts": len(state["attempts"]), "progress": state["progress"],
                "coverage": compute_coverage(state),
                "next_task": state["next_task"], "updated_at": state["updated_at"]}

    def set_scope(self, course_id, scope):
        if not isinstance(scope, (str, list, dict)):
            raise DataError("考试范围应为章节列表或文字说明")
        with self.store.transaction(course_id) as state:
            state["course"]["exam_scope"] = deepcopy(scope)
            self._event(state, "exam_scope_saved")
        return state["course"]

    def export(self, course_id):
        from .exporter import export_course
        state = self.store.read(course_id)
        if not state["course"]["mode"]:
            raise DataError("请先选择 high 或 pass 模式后导出复习计划")
        export_id = "export_" + uuid.uuid4().hex[:12]
        directory = self.store.contained(course_id, "exports/" + export_id)
        manifest = export_course(state, directory)
        return {**manifest, "export_id": export_id, "directory": str(directory),
                "file_paths": {name: str(directory / name) for name in
                               ("exam_focus.md", "review_guide.md", "practice.md", "mindmap.mmd", "export_manifest.json")}}
