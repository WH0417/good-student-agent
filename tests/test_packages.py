from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from good_student.packages import PackageManager
from good_student.jobs import JobManager
from good_student.service import StudentService
from good_student.storage import DataError, atomic_json


class PackageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = StudentService(self.root / "data")
        self.cid = self.service.store.create("数据结构", "pass")["course"]["id"]
        path = self.root / "栈.md"
        path.write_text("# 栈\n栈采用后进先出原则。push入栈、pop出栈。空栈不能出栈。", encoding="utf-8")
        self.sid = self.service.import_material(self.cid, path, "material")["source"]["id"]
        self.manager = PackageManager(self.service)

    def tearDown(self):
        self.temp.cleanup()

    def create(self, **kwargs):
        job = {"id": "job_123456789abc", "course_id": self.cid, "source_ids": [self.sid], "mode": "pass"}
        job.update(kwargs)
        return self.manager.create(self.cid, job, [{"id": "stack", "title": "栈", "source_ids": [self.sid], "question_count": 1}])

    def content(self):
        return {"summary": "栈是一种只在一端进行操作的线性结构。", "explanation": "先放入栈的元素要等后来放入的元素出栈后才能取出，这就是后进先出原则。",
                "examples": [{"title": "栈操作", "question": "依次push A、B，再pop得到谁？", "solution": "B最后入栈，所以pop取出B，A仍在栈中。"}],
                "pitfalls": ["出栈前需要检查栈是否为空。"], "mindmap": "flowchart TD\n A[栈] --> B[后进先出]",
                "source_refs": [{"source_id": self.sid, "locator": {"text_block": 1}}],
                "questions": [{"question": "栈采用什么原则？", "answer": "后进先出", "explanation": "只在栈顶插入和移除，最后进入的元素最先离开。"}]}

    def prepare(self):
        manifest = self.create()
        self.manager.write_chapter(self.cid, manifest["id"], "stack", self.content())
        self.manager.write_overview(self.cid, manifest["id"], "本课程从栈的操作原则开始复习，理解后进先出后完成例题和练习。", "flowchart TD\n A[课程] --> B[栈]")
        return manifest["id"]

    def test_complete_package_is_readable_after_restart_and_does_not_mark_answer_viewed(self):
        pid = self.prepare()
        manifest = self.manager.publish(self.cid, pid)
        self.assertEqual(manifest["status"], "complete")
        restarted = PackageManager(StudentService(self.root / "data"))
        package = restarted.read(self.cid)
        self.assertEqual(package["manifest"]["id"], pid)
        self.assertEqual(package["practice"][0]["origin"], "ai_generated")
        self.assertEqual(package["chapters"][0]["title"], "栈")
        state = self.service.store.read(self.cid)
        self.assertEqual(len(state["questions"]), 1)
        self.assertEqual(state["attempts"], [])
        self.assertEqual(state["question_views"], {})
        self.assertEqual(state["progress"], {})
        with self.assertRaises(DataError):
            self.manager.write_chapter(self.cid, pid, "stack", self.content())
        with self.assertRaises(DataError):
            self.manager.publish(self.cid, pid, partial=True)
        self.assertEqual(self.manager.publish(self.cid, pid)["status"], "complete")

    def test_agent_answer_has_verifiable_course_positions_and_invalid_positions_are_rejected(self):
        pid = self.create()['id']
        content = self.content()
        content['questions'][0]['answer_source_refs'] = [{'source_id': self.sid, 'locator': {'text_block': 999}}]
        with self.assertRaises(DataError):
            self.manager.write_chapter(self.cid, pid, 'stack', content)
        self.assertFalse(self.service.store.read(self.cid)['questions'])
        content['questions'][0]['answer_source_refs'][0]['locator']['text_block'] = 1
        chapter = self.manager.write_chapter(self.cid, pid, 'stack', content)
        answer = chapter['questions'][0]
        self.assertEqual(answer['answer_basis'], 'course_material')
        self.assertEqual(answer['answer_source_refs'][0]['filename'], '栈.md')
        self.assertEqual(answer['answer_source_refs'][0]['locator'], {'text_block': 1})

    def test_insufficient_material_is_not_published_as_confirmed_or_independent(self):
        pid = self.create()['id']
        content = self.content()
        content['questions'][0]['answer_basis'] = 'insufficient_material'
        content['questions'][0]['answer'] = '现有课程资料不足以可靠确定本题答案。'
        chapter = self.manager.write_chapter(self.cid, pid, 'stack', content)
        self.assertFalse(chapter['questions'][0]['independent_eligible'])
        question = chapter['questions'][0]
        attempt = self.service.answer(self.cid, question['id'], question['answer'])
        self.assertEqual(attempt['result'], 'unsure')
        self.assertFalse(attempt['independent'])
        with self.assertRaises(DataError):
            self.manager.publish(self.cid, pid)
        self.assertEqual(self.manager.publish(self.cid, pid, partial=True)['status'], 'partial')

    def test_directory_or_blank_content_cannot_be_published(self):
        pid = self.create()["id"]
        with self.assertRaises(DataError):
            self.manager.publish(self.cid, pid)
        with self.assertRaises(DataError):
            self.manager.publish(self.cid, pid, partial=True)
        content = self.content()
        content["explanation"] = "只有目录"
        with self.assertRaises(DataError):
            self.manager.write_chapter(self.cid, pid, "stack", content)
        self.assertFalse(self.service.store.read(self.cid)["questions"])

    def test_unread_or_changed_sources_prevent_complete_claim(self):
        pid = self.prepare()
        with self.service.store.transaction(self.cid) as state:
            state["sources"][self.sid]["status"] = "pending"
        with self.assertRaisesRegex(DataError, "未完整处理"):
            self.manager.publish(self.cid, pid)
        result = self.manager.publish(self.cid, pid, partial=True)
        self.assertEqual(result["status"], "partial")
        self.assertIsNone(self.manager.read(self.cid))
        self.assertEqual(self.manager.read(self.cid, preview=True)["manifest"]["id"], pid)
        with self.service.store.transaction(self.cid) as state:
            state["sources"][self.sid]["status"] = "complete"
            state["sources"][self.sid]["sha256"] = "changed"
        with self.assertRaisesRegex(DataError, "版本已变化"):
            self.manager.publish(self.cid, pid)

    def test_partial_preview_never_replaces_existing_complete_package(self):
        first = self.prepare()
        self.manager.publish(self.cid, first)
        second = self.create()["id"]
        self.manager.write_chapter(self.cid, second, "stack", self.content())
        partial = self.manager.publish(self.cid, second, partial=True)
        self.assertEqual(partial["status"], "partial")
        self.assertEqual(self.manager.read(self.cid)["manifest"]["id"], first)
        self.assertEqual(self.manager.read(self.cid, second)["manifest"]["id"], second)
        self.assertEqual(self.manager.read(self.cid, preview=True)["manifest"]["id"], second)
        # Retry uses stable question IDs and does not accumulate duplicate questions.
        self.manager.write_chapter(self.cid, second, "stack", self.content())
        self.assertEqual(len(self.service.store.read(self.cid)["questions"]), 1)

    def test_invalid_or_cross_course_paths_and_references_are_rejected(self):
        other = self.service.store.create("英语", "pass")["course"]["id"]
        pid = self.create()["id"]
        with self.assertRaises(DataError):
            self.manager.read(other, pid)
        with self.assertRaises(DataError):
            self.manager.read(self.cid, "../../outside")
        with self.assertRaises(DataError):
            self.manager.write_chapter(self.cid, pid, "../../outside", self.content())
        content = self.content()
        content["source_refs"][0]["locator"] = {"page": 999}
        with self.assertRaisesRegex(DataError, "不存在"):
            self.manager.write_chapter(self.cid, pid, "stack", content)
        content = self.content()
        content["source_refs"][0]["source_id"] = "foreign"
        with self.assertRaises(DataError):
            self.manager.write_chapter(self.cid, pid, "stack", content)

    def test_frozen_job_inputs_are_checked_before_creating_package(self):
        with self.assertRaisesRegex(DataError, "版本已经变化"):
            self.create(input_sources=[{"id": self.sid, "sha256": "wrong"}])

    def test_required_questions_and_whole_chapter_validate_before_insertion(self):
        pid = self.create()["id"]
        content = self.content()
        content["questions"].append({"question": "新题", "answer": "", "explanation": "无效答案"})
        with self.assertRaises(ValueError):
            self.manager.write_chapter(self.cid, pid, "stack", content)
        self.assertFalse(self.service.store.read(self.cid)["questions"])
        content = self.content()
        content["questions"] = []
        with self.assertRaisesRegex(DataError, "至少1道"):
            self.manager.write_chapter(self.cid, pid, "stack", content)

    def test_original_question_answers_and_explanation_keep_source_label(self):
        with self.service.store.transaction(self.cid) as state:
            state["questions"]["q_original"] = {"id": "q_original", "question": "写出栈的原则。", "answer": None,
                                                    "origin": "exercise", "source_id": self.sid, "options": []}
        pid = self.create()["id"]
        content = self.content()
        content["questions"] = [{"question_id": "q_original", "answer": "后进先出", "explanation": "最后进入的元素从栈顶最先取出。"}]
        chapter = self.manager.write_chapter(self.cid, pid, "stack", content)
        self.assertEqual(chapter["questions"][0]["origin"], "exercise")
        self.assertEqual(chapter["questions"][0]["answer_origin"], "agent_generated")
        self.manager.write_overview(self.cid, pid, "先理解栈的后进先出原则，再完成原题并核对推理过程。", "mindmap\n root((数据结构))\n  栈")
        self.assertEqual(self.manager.publish(self.cid, pid)["status"], "complete")

    def test_missing_original_questions_or_warnings_block_complete_package(self):
        with self.service.store.transaction(self.cid) as state:
            state["questions"]["q_original"] = {"id": "q_original", "question": "写出栈的原则。", "answer": None,
                                                    "origin": "exercise", "source_id": self.sid, "options": []}
        pid = self.prepare()
        with self.assertRaisesRegex(DataError, "原题未整理"):
            self.manager.publish(self.cid, pid)
        self.assertEqual(self.manager.publish(self.cid, pid, partial=True)["status"], "partial")

    def test_leaked_original_answer_is_flagged_without_rewriting_source_stem(self):
        stem = "1. 栈的操作原则是（B）。"
        with self.service.store.transaction(self.cid) as state:
            state["questions"]["q_original"] = {"id": "q_original", "question": stem, "answer": "B",
                                                    "origin": "exercise", "source_id": self.sid,
                                                    "options": ["A. 先进先出", "B. 后进先出"]}
        pid = self.create()["id"]
        content = self.content()
        content["questions"] = [{"question_id": "q_original", "explanation": "栈顶完成插入与删除，所以后进先出。"}]
        chapter = self.manager.write_chapter(self.cid, pid, "stack", content)
        question = chapter["questions"][0]
        self.assertEqual(question["question"], stem)
        self.assertTrue(question["answer_visible_in_stem"])
        self.assertFalse(question["independent_eligible"])
        self.assertIn("不作为独立检测", question["practice_note"])
        self.assertEqual(self.service.store.read(self.cid)["questions"]["q_original"]["question"], stem)
        self.assertFalse(self.service.store.read(self.cid)["question_views"])

    def test_same_original_question_id_gets_a_new_reference_version_when_answer_changes(self):
        with self.service.store.transaction(self.cid) as state:
            state["questions"]["q_original"] = {"id": "q_original", "question": "写出栈的原则。", "answer": None,
                                                    "origin": "exercise", "source_id": self.sid, "options": []}
        one = self.create()["id"]
        content = self.content()
        content["questions"] = [{"question_id": "q_original", "answer": "先进先出", "explanation": "旧的错误解析。"}]
        first = self.manager.write_chapter(self.cid, one, "stack", content)["questions"][0]
        two = self.create()["id"]
        content["questions"][0].update(answer="后进先出", explanation="最后进入的元素最先离开，旧解析已修正。")
        second = self.manager.write_chapter(self.cid, two, "stack", content)["questions"][0]
        self.assertEqual(first["id"], second["id"])
        self.assertNotEqual(first["reference_version"], second["reference_version"])
        self.assertTrue(first["reference_version"].startswith("ref_"))
        self.assertEqual(self.service.store.read(self.cid)["questions"]["q_original"]["answer"], None)

    def test_search_and_portable_exports_read_existing_content_only(self):
        pid = self.prepare()
        self.manager.publish(self.cid, pid)
        results = self.manager.search(self.cid, "后进先出")
        self.assertEqual(results[0]["chapter_id"], "stack")
        self.assertFalse(self.manager.search(self.cid, "不存在知识"))
        first = self.manager.export(self.cid)
        second = self.manager.export(self.cid)
        self.assertNotEqual(first["directory"], second["directory"])
        directory = Path(first["directory"])
        self.assertTrue((directory / "chapters/stack.md").exists())
        practice = (directory / "practice.md").read_text(encoding="utf-8")
        self.assertLess(practice.index("栈采用什么原则？"), practice.index("# 参考答案与解析"))
        self.assertIn("AI生成练习；来源：栈.md", practice)
        self.assertEqual(self.service.store.read(self.cid)["question_views"], {})

    def test_export_uses_current_active_focus_and_keeps_package_snapshot_unchanged(self):
        old = self.service.add_focus(self.cid, "旧的用户复习标记", "这条稍后会撤回")
        pid = self.prepare()
        self.manager.publish(self.cid, pid)
        snapshot = deepcopy(self.manager.read(self.cid, pid)["focus"])
        self.service.retract_focus(self.cid, old["id"], "用户已取消")
        self.service.add_focus(self.cid, "新的用户复习标记", "导出时应包含这条有效标记")
        outside = self.root / "本包未选入的课堂.txt"
        outside.write_text("老师：链表是考试重点。", encoding="utf-8")
        self.service.import_material(self.cid, outside, "transcript")
        exported = self.manager.export(self.cid, pid)
        focus = (Path(exported["directory"]) / "exam_focus.md").read_text(encoding="utf-8")
        self.assertIn("新的用户复习标记", focus)
        self.assertNotIn("旧的用户复习标记", focus)
        self.assertNotIn("链表", focus)
        self.assertIn("用户直接指定", focus)
        self.assertNotIn("src_", focus)
        self.assertEqual(self.manager.read(self.cid, pid)["focus"], snapshot)
        self.assertIn(old["id"], [f["id"] for f in snapshot])
        self.assertEqual(exported["learning_revision"], self.service.store.read(self.cid)["revision"])
        self.assertIn("导出时课程记录", exported["focus_note"])

    def test_export_includes_current_learning_attempts_notes_and_saved_answers(self):
        pid = self.prepare()
        self.manager.publish(self.cid, pid)
        question = self.manager.read(self.cid, pid)["practice"][0]
        attempt = self.service.answer(self.cid, question["id"], "后进先出", reference=question)
        with self.service.store.transaction(self.cid) as state:
            state["workbench"] = {"reading": {"stack": {"status": "self_assessed", "basis": "user_report"}},
                                  "last_chapter": "stack"}
            state["notes"].append({"title": "复习笔记", "summary": "栈顶操作决定了后进先出。"})
            # Keep genuine older-version evidence distinct in this export.
            state["attempts"][0]["reference_version"] = "ref_previous_fixture"
        jobs = JobManager(self.service)
        job = jobs.create(self.cid, kind="question", request="为什么只能从栈顶出栈？")
        response = {"job_id": job["id"], "request": job["request"], "text": "栈的定义限定所有插入删除都发生在栈顶。"}
        atomic_json(self.service.store.contained(self.cid, f"jobs/{job['id']}/response.json"), response)
        exported = self.manager.export(self.cid, pid)
        directory = Path(exported["directory"])
        records = json.loads((directory / "学习记录.json").read_text(encoding="utf-8"))
        readable = (directory / "学习记录.md").read_text(encoding="utf-8")
        self.assertEqual(records["attempts"][0]["id"], attempt["id"])
        self.assertTrue(records["attempts"][0]["reference_changed"])
        self.assertEqual(records["attempts"][0]["reference_version"], "ref_previous_fixture")
        self.assertEqual(records["attempts"][0]["current_reference_version"], question["reference_version"])
        self.assertEqual(records["reading"]["stack"]["chapter_title"], "栈")
        for text in ("栈：自评已理解", "栈采用什么原则？", "你的作答：后进先出", "历史解析版本", "复习笔记", response["text"]):
            self.assertIn(text, readable)
        self.assertNotIn(attempt["id"], readable)
        self.assertNotIn(question["id"], readable)
        self.assertIn("学习记录.md", exported["files"])
        self.assertIn("学习记录.json", exported["files"])

    def test_learning_export_does_not_include_executor_credentials(self):
        pid = self.prepare()
        self.manager.publish(self.cid, pid)
        jobs = JobManager(self.service)
        job = jobs.create(self.cid, kind="question", request="解释一个例子")
        lease = jobs.claim(self.cid, job["id"], "test-worker")["lease_token"]
        atomic_json(self.service.store.contained(self.cid, f"jobs/{job['id']}/response.json"), {
            "job_id": job["id"], "text": "这是应保留的答疑内容。", "lease_token": lease,
            "metadata": {"X-Session-Token": lease, "safe_note": "保留普通说明"},
        })
        with self.service.store.transaction(self.cid) as state:
            state["notes"].append({"title": "安全导出测试", "summary": "普通学习笔记应保留。",
                                   "nested": {"session_token": lease}})
        exported = self.manager.export(self.cid, pid)
        directory = Path(exported["directory"])
        for path in directory.rglob("*"):
            if path.is_file():
                self.assertNotIn(lease, path.read_text(encoding="utf-8"), path.name)
        records = json.loads((directory / "学习记录.json").read_text(encoding="utf-8"))
        self.assertEqual(records["responses"][0]["text"], "这是应保留的答疑内容。")
        self.assertEqual(records["responses"][0]["metadata"], {"safe_note": "保留普通说明"})


if __name__ == "__main__":
    unittest.main()
