"""Regressions for same-source questions whose prepared reference changes."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from good_student.packages import PackageManager
from good_student.service import StudentService
from good_student.workbench import create_app


class ReferenceVersionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.service = StudentService(self.data)
        self.cid = self.service.store.create("引用版本回归测试", "pass")["course"]["id"]
        source = self.root / "自写测试资料.md"
        source.write_text("# 引用版本\n这是独立自写测试材料，用于验证同题号参考答案发生变化后的作答记录。", encoding="utf-8")
        self.sid = self.service.import_material(self.cid, source, "material")["source"]["id"]
        self.tid = next(iter(self.service.store.read(self.cid)["topics"]))
        self.qid = "q_original_version_fixture"
        self.original = {
            "id": self.qid, "question": "测试题：请选择当前示例参考中的选项。",
            "options": ["A. 示例选项甲", "B. 示例选项乙"], "answer": None,
            "explanation": None, "origin": "original", "type": "choice",
            "source_id": self.sid, "source_ids": [self.sid], "topic_ids": [self.tid],
        }
        with self.service.store.transaction(self.cid) as state:
            state["questions"][self.qid] = self.original.copy()
        self.packages = PackageManager(self.service)

    def tearDown(self):
        self.temp.cleanup()

    def reference(self, answer, version):
        return {**self.original, "answer": answer, "explanation": f"自写测试参考解析，版本{version}。",
                "reference_version": version, "reference_package_id": f"fixture_{version}"}

    def publish(self, answer):
        job = {"id": "job_123456789abc", "course_id": self.cid,
               "source_ids": [self.sid], "mode": "pass"}
        manifest = self.packages.create(self.cid, job, [{"id": "references", "title": "引用版本",
                                                        "source_ids": [self.sid], "question_count": 1}])
        pid = manifest["id"]
        self.packages.write_chapter(self.cid, pid, "references", {
            "summary": "本章用于测试同一道原题的参考答案版本变化与学习记录恢复。",
            "explanation": "测试只检查参考版本的识别和记录，不检查知识内容。新版本需要重新判断，旧版本的作答通过不能自动继承。",
            "examples": [{"title": "版本变化示意", "question": "参考从甲变为乙后如何记录？",
                          "solution": "保留历史作答，并使用新参考版本单独判断当前作答。"}],
            "pitfalls": ["稳定题号并不表示参考答案版本始终相同。"],
            "mindmap": "flowchart TD\n A[同一原题] --> B[旧参考]\n A --> C[新参考]",
            "source_refs": [{"source_id": self.sid, "locator": {"text_block": 1}}],
            "questions": [{"question_id": self.qid, "answer": answer,
                           "explanation": f"当前测试参考选择{answer}，用于检验版本隔离。"}],
        })
        self.packages.write_overview(self.cid, pid, "这是一个自写的引用版本测试复习包，检查参考更新后学习记录的投影。",
                                     "flowchart TD\n A[测试课程] --> B[引用版本]")
        self.packages.publish(self.cid, pid)
        question = self.packages.read(self.cid, pid)["practice"][0]
        return pid, {**question, "reference_package_id": pid}

    def test_changed_reference_allows_new_independent_answer_for_the_same_question_id(self):
        first = self.service.answer(self.cid, self.qid, "A", reference=self.reference("A", "v1"))
        second = self.service.answer(self.cid, self.qid, "B", reference=self.reference("B", "v2"))
        self.assertEqual(first["result"], "right")
        self.assertTrue(first["independent"])
        self.assertEqual(second["result"], "right")
        self.assertTrue(second["independent"])
        self.assertFalse(second["answer_viewed"])
        self.assertEqual(second["reference_version"], "v2")
        progress = self.service.store.read(self.cid)["progress"][self.tid]
        self.assertEqual(progress["reference_version"], "v2")
        self.assertEqual(progress["attempt_id"], second["id"])

    def test_revealing_the_current_version_prevents_later_independent_credit(self):
        self.service.answer(self.cid, self.qid, "A", reference=self.reference("A", "v1"))
        self.service.show_question(self.cid, self.qid, True, reference=self.reference("B", "v2"))
        attempt = self.service.answer(self.cid, self.qid, "B", reference=self.reference("B", "v2"))
        self.assertEqual(attempt["result"], "right")
        self.assertFalse(attempt["independent"])
        self.assertTrue(attempt["answer_viewed"])
        self.assertEqual(self.service.store.read(self.cid)["progress"][self.tid]["status"], "needs_review")
        views = self.service.store.read(self.cid)["question_views"][self.qid]["versions"]
        self.assertTrue(views["v1"]["answer_viewed"])
        self.assertTrue(views["v2"]["answer_viewed"])

    def test_unsure_answer_in_new_version_does_not_preserve_old_practice_pass(self):
        first = self.service.answer(self.cid, self.qid, "A", reference=self.reference("A", "v1"))
        self.assertEqual(self.service.store.read(self.cid)["progress"][self.tid]["status"], "practice_passed")
        second = self.service.answer(self.cid, self.qid, "不知道", reference=self.reference("B", "v2"))
        self.assertEqual(second["result"], "unsure")
        progress = self.service.store.read(self.cid)["progress"][self.tid]
        self.assertEqual(progress["status"], "needs_review")
        self.assertEqual(progress["attempt_id"], second["id"])
        self.assertNotEqual(progress["attempt_id"], first["id"])

    def test_course_api_projects_an_old_pass_as_needing_review_for_the_current_package(self):
        first_pid, old_reference = self.publish("A")
        self.service.answer(self.cid, self.qid, "A", reference=old_reference)
        second_pid, new_reference = self.publish("B")
        self.assertNotEqual(first_pid, second_pid)
        self.assertNotEqual(old_reference["reference_version"], new_reference["reference_version"])
        raw_progress = self.service.store.read(self.cid)["progress"][self.tid]
        self.assertEqual(raw_progress["status"], "practice_passed")
        self.assertEqual(raw_progress["reference_version"], old_reference["reference_version"])
        web = self.root / "web"
        web.mkdir()
        (web / "index.html").write_text("<!doctype html><title>test</title>", encoding="utf-8")
        app = create_app(self.data, "version-secret", web_dir=web, test_mode=True)
        with TestClient(app, base_url="http://127.0.0.1:8765",
                        headers={"x-session-token": "version-secret"}) as client:
            response = client.get("/api/course", params={"course_id": self.cid})
        self.assertEqual(response.status_code, 200, response.text)
        course = response.json()["data"]
        self.assertEqual(course["package"]["manifest"]["id"], second_pid)
        self.assertEqual(course["progress"][self.tid]["status"], "needs_review")
        self.assertEqual(course["progress"][self.tid]["basis"], "reference_changed")
        # Reading the course projects state for this package; historical evidence is retained.
        stored = self.service.store.read(self.cid)
        self.assertEqual(stored["progress"][self.tid], raw_progress)
        self.assertEqual(stored["questions"][self.qid]["answer"], None)


if __name__ == "__main__":
    unittest.main()
