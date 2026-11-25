"""User-requested answer reviews kept as immutable package sidecar versions."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re
import uuid

from .jobs import JobManager
from .storage import DataError, atomic_json, file_lock, now


class AnswerReviewManager:
    def __init__(self, service):
        from .packages import PackageManager
        self.service = service
        self.store = service.store
        self.packages = PackageManager(service)

    def _directory(self, course_id, package_id, question_id):
        self.packages._manifest(course_id, package_id)
        if not isinstance(question_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,159}", question_id):
            raise DataError("题目编号无效")
        return self.store.contained(course_id, f"packages/{package_id}/reviews/{question_id}")

    def _record(self, course_id, package_id, question_id, review_id):
        if not isinstance(review_id, str) or not re.fullmatch(r"review_[0-9a-f]{12}", review_id):
            raise DataError("答案复核版本编号无效")
        directory = self._directory(course_id, package_id, question_id)
        path = self.store.contained(course_id, str((directory / f"{review_id}.json").relative_to(self.store.course_dir(course_id))))
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DataError("答案复核记录无法读取，已保留原文件") from exc
        if (not isinstance(record, dict) or record.get("id") != review_id or record.get("course_id") != course_id
                or record.get("package_id") != package_id or record.get("question_id") != question_id):
            raise DataError("答案复核记录与课程或题目不一致")
        return record

    def history(self, course_id, package_id, question_id):
        directory = self._directory(course_id, package_id, question_id)
        records = [self._record(course_id, package_id, question_id, path.stem)
                   for path in directory.glob("review_*.json") if re.fullmatch(r"review_[0-9a-f]{12}", path.stem)]
        return sorted(records, key=lambda r: (r.get("reviewed_at", ""), r["id"]))

    def overlay(self, course_id, package_id, question):
        from .packages import _flag_visible_answer
        result = _flag_visible_answer(question)
        directory = self._directory(course_id, package_id, question["id"])
        pointer = directory / "current.json"
        if not pointer.exists():
            return result
        try:
            review_id = json.loads(pointer.read_text(encoding="utf-8"))["review_id"]
        except (OSError, ValueError, KeyError) as exc:
            raise DataError("当前答案复核版本指向损坏，已保留原文件") from exc
        record = self._record(course_id, package_id, question["id"], review_id)
        result.update(answer=record["answer"], explanation=record["explanation"],
                      answer_origin="agent_rechecked", answer_status="reviewed",
                      original_reference_answer=record["original_reference_answer"],
                      answer_review={k: deepcopy(record[k]) for k in ("id", "decision", "reason", "reviewed_at", "source_refs")},
                      answer_reviews=self.history(course_id, package_id, question["id"]))
        result["answer_note"] = "已根据用户质疑重新核对，保留原答案与复核原因。"
        result.pop("candidate_answer", None)
        result.pop("candidate_explanation", None)
        if not result.get("answer_visible_in_stem"):
            result.pop("practice_note", None)
        return _flag_visible_answer(result)

    def request(self, course_id, package_id, question_id, reason):
        if not isinstance(reason, str) or not reason.strip():
            raise DataError("请说明你质疑答案的原因")
        package = self.packages.read(course_id, package_id)
        question = next((q for q in package["practice"] if q["id"] == question_id), None)
        if question is None:
            raise DataError("题目不属于指定课程复习包")
        settings = {"package_id": package_id, "question_id": question_id, "qid": question_id,
                    "reason": reason.strip(), "reference_version": question["reference_version"]}
        jobs = JobManager(self.service)
        for job in jobs.list(course_id):
            if (job["kind"] == "challenge" and job["status"] in ("queued", "running", "waiting_agent")
                    and job["settings"].get("package_id") == package_id
                    and job["settings"].get("question_id") == question_id
                    and job["settings"].get("reference_version") == question["reference_version"]):
                return {**job, "reused": True}
        from .packages import _sources
        sources = sorted(_sources(question).intersection(package["manifest"]["source_ids"]))
        return jobs.create(course_id, sources or package["manifest"]["source_ids"],
                           request="请重新核对这道题的答案与解析：" + reason.strip(), kind="challenge", settings=settings)

    def resolve(self, course_id, job, result):
        from .packages import _flag_visible_answer, _text
        if (not isinstance(job, dict) or job.get("course_id") != course_id or job.get("kind") != "challenge"
                or not re.fullmatch(r"job_[0-9a-f]{12}", str(job.get("id", "")))):
            raise DataError("这不是当前课程的答案质疑任务")
        if not isinstance(result, dict) or result.get("decision") not in ("keep", "revise"):
            raise DataError("复核结论必须是keep或revise")
        settings = job.get("settings", {})
        package_id = settings.get("package_id")
        question_id = settings.get("question_id") or settings.get("qid")
        directory = self._directory(course_id, package_id, question_id)
        fingerprint = hashlib.sha256(json.dumps(result, ensure_ascii=False, sort_keys=True,
                                               separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
        with file_lock(self.packages._root(course_id) / ".packages.lock"):
            for previous in self.history(course_id, package_id, question_id):
                if previous.get("job_id") == job["id"]:
                    if previous.get("resolution_fingerprint") != fingerprint:
                        raise DataError("此任务已经保存复核结论，请新建质疑任务修改")
                    pointer = directory / "current.json"
                    current_id = None
                    if pointer.exists():
                        try:
                            current_id = json.loads(pointer.read_text(encoding="utf-8"))["review_id"]
                        except (OSError, ValueError, KeyError) as exc:
                            raise DataError("当前答案复核版本指向损坏，已保留原文件") from exc
                    # Recover a crash between writing the immutable record and
                    # its pointer, without rolling back a later review.
                    if current_id == previous.get("previous_review_id"):
                        atomic_json(pointer, {"review_id": previous["id"], "updated_at": now()})
                    return previous
            package = self.packages.read(course_id, package_id)
            question = next((q for q in package["practice"] if q["id"] == question_id), None)
            if question is None:
                raise DataError("题目不属于指定课程复习包")
            if not settings.get("reference_version") or settings["reference_version"] != question["reference_version"]:
                raise DataError("答案版本已更新，请按最新答案重新发起复核")
            answer = _text(result.get("answer", question["answer"]), "复核答案")
            explanation = _text(result.get("explanation", question["explanation"]), "复核解析")
            if result["decision"] == "keep" and answer != str(question["answer"]).strip():
                raise DataError("keep必须保留当前答案，修改答案请使用revise")
            if result["decision"] == "revise" and (not result.get("answer") or not result.get("explanation")):
                raise DataError("修订答案必须提供新答案和完整解析")
            reason = _text(result.get("reason"), "复核原因")
            plan = next(c for c in package["manifest"]["chapters"] if c["id"] == question["chapter_id"])
            state = self.store.read(course_id)
            refs = self.packages._check_refs(state, package["manifest"], plan, result.get("source_refs"))
            # Reuse the question validator to reject impossible objective keys;
            # this does not insert a duplicate question or judge open answers.
            from .practice import validate_generated
            validate_generated({"question": question["question"], "answer": answer, "explanation": explanation,
                                "options": question.get("options", []), "reference_ids": [question_id],
                                "source_ids": list(plan["source_ids"])}, state)
            reviewed = _flag_visible_answer({**question, "answer": answer, "explanation": explanation})
            source = state["questions"].get(question_id, {})
            original_answer = question.get("original_reference_answer")
            if original_answer is None:
                original_answer = source.get("answer") if source.get("answer") is not None else question["answer"]
            record = {"id": "review_" + uuid.uuid4().hex[:12], "job_id": job["id"],
                      "course_id": course_id, "package_id": package_id, "question_id": question_id,
                      "decision": result["decision"], "reason": reason, "source_refs": refs,
                      "previous_answer": deepcopy(question["answer"]),
                      "previous_explanation": deepcopy(question["explanation"]),
                      "previous_reference_version": question["reference_version"],
                      "previous_review_id": question.get("answer_review", {}).get("id"),
                      "answer": answer, "explanation": explanation,
                      "original_reference_answer": deepcopy(original_answer),
                      "reference_version": reviewed["reference_version"],
                      "reviewed_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
                      "resolution_fingerprint": fingerprint}
            atomic_json(directory / f"{record['id']}.json", record)
            atomic_json(directory / "current.json", {"review_id": record["id"], "updated_at": now()})
            return record
