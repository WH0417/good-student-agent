from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from good_student.challenges import AnswerReviewManager
from good_student.packages import PackageManager
from good_student.service import StudentService
from good_student.storage import DataError
from good_student.storage import atomic_json


class AnswerChallengeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = StudentService(self.root / "data")
        self.cid = self.service.store.create("自写答案复核测试", "pass")["course"]["id"]
        source = self.root / "栈.md"
        source.write_text("# 栈\n栈遵守后进先出，示例选项A为先进先出，B为后进先出。本资料专供测试。", encoding="utf-8")
        self.sid = self.service.import_material(self.cid, source, "material")["source"]["id"]
        self.tid = next(iter(self.service.store.read(self.cid)["topics"]))
        self.qid = "q_original_fixture"
        with self.service.store.transaction(self.cid) as state:
            state["questions"][self.qid] = {
                "id": self.qid, "question": "栈遵守什么原则？", "answer": "A", "explanation": None,
                "options": ["A. 先进先出", "B. 后进先出"], "origin": "original", "type": "choice",
                "topic_ids": [self.tid], "source_id": self.sid, "source_ids": [self.sid],
            }
        self.packages = PackageManager(self.service)
        self.reviews = AnswerReviewManager(self.service)

    def tearDown(self):
        self.temp.cleanup()

    def content(self, answer="A", **extra):
        content = {
            "summary": "本章是自写测试材料，用于检查资料答案优先和用户质疑复核。",
            "explanation": "栈的操作只允许发生在栈顶，所以最后进入的元素最先离开。测试资料故意放入错误参考以验证版本保存。",
            "examples": [{"title": "栈操作", "question": "A与B依次入栈，谁先出栈？", "solution": "B最后入栈，因此B先出栈。"}],
            "pitfalls": ["不能把先进先出与后进先出混淆。"],
            "mindmap": "flowchart TD\n A[栈] --> B[后进先出]",
            "source_refs": [{"source_id": self.sid, "locator": {"text_block": 1}}],
            "questions": [{"question_id": self.qid, "answer": answer,
                           "explanation": "这是测试用解析，实际复核要结合资料核对选项。"}],
        }
        content.update(extra)
        return content

    def package(self, answer="A", **extra):
        job = {"id": "job_123456789abc", "course_id": self.cid, "mode": "pass", "source_ids": [self.sid]}
        manifest = self.packages.create(self.cid, job, [{"id": "stack", "title": "栈", "source_ids": [self.sid], "question_count": 1}])
        self.packages.write_chapter(self.cid, manifest["id"], "stack", self.content(answer, **extra))
        self.packages.write_overview(self.cid, manifest["id"], "这个测试课程用于验证原题来源、答案质疑、版本留存与解题方法关联。",
                                     "flowchart TD\n A[课程] --> B[栈]")
        self.packages.publish(self.cid, manifest["id"])
        return manifest["id"]

    def revision(self, answer="B", reason="按照栈顶操作规则重新核对后，B才是后进先出。"):
        return {"answer": answer, "explanation": "栈只在栈顶插入与删除，因此最后放入的元素最先离开，应选B。",
                "decision": "revise", "reason": reason,
                "source_refs": [{"source_id": self.sid, "locator": {"text_block": 1}}]}

    def test_source_answer_takes_priority_and_different_agent_answer_stays_a_candidate(self):
        pid = self.package("B")
        question = self.packages.read(self.cid, pid)["practice"][0]
        self.assertEqual(question["answer"], "A")
        self.assertEqual(question["answer_origin"], "source")
        self.assertEqual(question["candidate_answer"], "B")
        self.assertEqual(question["answer_status"], "needs_review")
        self.assertFalse(question["independent_eligible"])
        self.assertNotEqual(question["explanation"], question["candidate_explanation"])
        self.assertEqual(self.service.store.read(self.cid)["questions"][self.qid]["answer"], "A")

    def test_source_answer_keeps_exact_original_text_and_old_bundle_cannot_override_it(self):
        with self.service.store.transaction(self.cid) as state:
            state['questions'][self.qid]['answer'] = '  A  '
        pid = self.package('A')
        self.assertEqual(self.packages.read(self.cid, pid)['practice'][0]['answer'], '  A  ')
        path = self.service.store.contained(self.cid, f'packages/{pid}/chapters/stack.json')
        old = json.loads(path.read_text(encoding='utf-8'))
        old['questions'][0].update(answer='B', answer_origin='agent_generated')
        atomic_json(path, old)
        before = path.read_bytes()
        projected = self.packages.read(self.cid, pid)['practice'][0]
        self.assertEqual(projected['answer'], '  A  ')
        self.assertEqual(projected['candidate_answer'], 'B')
        self.assertEqual(projected['answer_status'], 'needs_review')
        self.assertEqual(path.read_bytes(), before)

    def test_agent_fills_an_answer_only_when_source_answer_is_missing(self):
        with self.service.store.transaction(self.cid) as state:
            state["questions"][self.qid]["answer"] = None
        pid = self.package("B")
        question = self.packages.read(self.cid, pid)["practice"][0]
        self.assertEqual(question["answer"], "B")
        self.assertEqual(question["answer_origin"], "agent_generated")
        self.assertNotIn("candidate_answer", question)
        self.assertIsNone(self.service.store.read(self.cid)["questions"][self.qid]["answer"])

    def test_review_creates_a_new_reference_without_changing_source_or_published_chapter(self):
        pid = self.package("B")
        path = self.service.store.contained(self.cid, f"packages/{pid}/chapters/stack.json")
        before = path.read_bytes()
        previous = self.packages.read(self.cid, pid)["practice"][0]
        job = self.reviews.request(self.cid, pid, self.qid, "我认为资料答案印错了")
        self.assertEqual(job["kind"], "challenge")
        self.assertEqual(job["settings"]["reference_version"], previous["reference_version"])
        self.assertTrue(self.reviews.request(self.cid, pid, self.qid, "相同题目再问一次")["reused"])
        record = self.reviews.resolve(self.cid, job, self.revision())
        question = self.packages.read(self.cid, pid)["practice"][0]
        self.assertEqual(question["answer"], "B")
        self.assertEqual(question["answer_origin"], "agent_rechecked")
        self.assertEqual(question["original_reference_answer"], "A")
        self.assertEqual(question["answer_review"]["reason"], record["reason"])
        self.assertNotEqual(question["reference_version"], previous["reference_version"])
        self.assertEqual(record["previous_reference_version"], previous["reference_version"])
        self.assertEqual(question["answer_status"], "reviewed")
        self.assertTrue(question["independent_eligible"])
        self.assertNotIn("candidate_answer", question)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.service.store.read(self.cid)["questions"][self.qid]["answer"], "A")
        self.assertEqual(len(self.reviews.history(self.cid, pid, self.qid)), 1)
        self.assertEqual(self.reviews.resolve(self.cid, job, self.revision())["id"], record["id"])
        with self.assertRaises(DataError):
            self.reviews.resolve(self.cid, job, self.revision(reason="同一任务不能改写已保存结论"))
        with self.assertRaises(DataError):
            self.packages.write_chapter(self.cid, pid, "stack", self.content("B"))

    def test_cross_course_and_stale_reference_challenges_are_rejected(self):
        pid = self.package()
        other = self.service.store.create("另一科", "pass")["course"]["id"]
        with self.assertRaises(DataError):
            self.reviews.request(other, pid, self.qid, "跨科")
        with self.assertRaises(DataError):
            self.reviews.request(self.cid, pid, "../outside", "跨路径")
        job = self.reviews.request(self.cid, pid, self.qid, "复核A")
        stale = deepcopy(job)
        stale["id"] = "job_abcdef123456"
        self.reviews.resolve(self.cid, job, self.revision())
        with self.assertRaisesRegex(DataError, "版本已更新"):
            self.reviews.resolve(self.cid, stale, self.revision())
        invalid = self.reviews.request(self.cid, pid, self.qid, "再次核对")
        result = self.revision()
        result["source_refs"][0]["locator"]["text_block"] = 999
        with self.assertRaisesRegex(DataError, "不存在"):
            self.reviews.resolve(self.cid, invalid, result)

    def test_keep_decision_is_recorded_but_cannot_secretly_change_answer(self):
        pid = self.package()
        job = self.reviews.request(self.cid, pid, self.qid, "复核是否保持")
        result = self.revision()
        result.update(decision="keep")
        with self.assertRaisesRegex(DataError, "keep必须保留"):
            self.reviews.resolve(self.cid, job, result)
        result.pop("answer")
        result.pop("explanation")
        record = self.reviews.resolve(self.cid, job, result)
        self.assertEqual(record["decision"], "keep")
        self.assertEqual(record["answer"], "A")
        self.assertEqual(self.packages.read(self.cid, pid)["practice"][0]["answer"], "A")

    def test_reviewed_reference_does_not_inherit_independent_credit_from_old_answer(self):
        pid = self.package()
        old = self.packages.read(self.cid, pid)["practice"][0]
        first = self.service.answer(self.cid, self.qid, "A", reference=old)
        self.assertTrue(first["independent"])
        job = self.reviews.request(self.cid, pid, self.qid, "原参考与栈顶操作定义冲突")
        self.reviews.resolve(self.cid, job, self.revision())
        new = self.packages.read(self.cid, pid)["practice"][0]
        self.assertNotEqual(old["reference_version"], new["reference_version"])
        second = self.service.answer(self.cid, self.qid, "B", reference=new)
        self.assertTrue(second["independent"])
        self.assertEqual(second["result"], "right")
        self.assertEqual(second["reference_version"], new["reference_version"])
        self.assertEqual(self.service.store.read(self.cid)["progress"][self.tid]["reference_version"], new["reference_version"])

    def test_invalid_objective_answer_cannot_be_persisted_as_a_review_version(self):
        pid = self.package()
        job = self.reviews.request(self.cid, pid, self.qid, "请检查选项")
        result = self.revision()
        result["answer"] = "Z"
        with self.assertRaisesRegex(ValueError, "有效选项"):
            self.reviews.resolve(self.cid, job, result)
        self.assertEqual(self.reviews.history(self.cid, pid, self.qid), [])
        self.assertEqual(self.packages.read(self.cid, pid)["practice"][0]["answer"], "A")

    def test_retry_recovers_a_review_saved_just_before_pointer_write_was_interrupted(self):
        pid = self.package()
        job = self.reviews.request(self.cid, pid, self.qid, "核对栈的选项")
        result = self.revision()
        def interrupted(path, value):
            if path.name == "current.json":
                raise OSError("simulated interruption")
            return atomic_json(path, value)
        with patch("good_student.challenges.atomic_json", side_effect=interrupted):
            with self.assertRaises(OSError):
                self.reviews.resolve(self.cid, job, result)
        self.assertEqual(self.packages.read(self.cid, pid)["practice"][0]["answer"], "A")
        self.assertEqual(len(self.reviews.history(self.cid, pid, self.qid)), 1)
        recovered = self.reviews.resolve(self.cid, job, result)
        self.assertEqual(self.packages.read(self.cid, pid)["practice"][0]["answer"], "B")
        self.assertEqual(len(self.reviews.history(self.cid, pid, self.qid)), 1)
        self.assertEqual(self.packages.read(self.cid, pid)["practice"][0]["answer_review"]["id"], recovered["id"])

    def test_formal_method_requires_real_matching_focus_and_retraction_removes_formal_label(self):
        question = deepcopy(self.service.store.read(self.cid)["questions"][self.qid])
        focus = self.service.add_focus(self.cid, "栈", "用户指定栈作为复习重点")
        # The synthetic original is not parsed from this tiny material; refresh
        # legitimately rebuilds the source bank, so reattach the test fixture.
        with self.service.store.transaction(self.cid) as state:
            state["questions"][self.qid] = question
        method = {"topic_title": "栈", "focus_ids": [focus["id"]], "claim_type": "user_priority",
                  "method_steps": ["先确定栈顶。", "按后进先出模拟操作。"],
                  "example": {"question": "A B依次入栈谁先出？", "solution": "B位于栈顶，所以B先出。"}}
        pid = self.package(focus_methods=[method])
        methods = self.packages.read(self.cid, pid)["focus_methods"]
        self.assertEqual(methods[0]["label"], "用户重点")
        self.assertEqual(methods[0]["chapter_title"], "栈")
        self.service.retract_focus(self.cid, focus["id"], "用户取消指定")
        self.assertEqual(self.packages.read(self.cid, pid)["focus_methods"][0]["label"], "复习建议")
        with self.assertRaises(DataError):
            self.package(focus_methods=[method])

    def test_suggested_method_cannot_be_promoted_to_exam_assurance(self):
        method = {"topic_title": "栈", "focus_ids": [], "claim_type": "exam_assured",
                  "method_steps": ["模拟操作。"], "example": "A B依次入栈，B先出。"}
        with self.assertRaisesRegex(DataError, "只能标为复习建议"):
            self.package(focus_methods=[method])
        method["claim_type"] = "suggestion"
        pid = self.package(focus_methods=[method])
        self.assertEqual(self.packages.read(self.cid, pid)["focus_methods"][0]["label"], "复习建议")


if __name__ == "__main__":
    unittest.main()
