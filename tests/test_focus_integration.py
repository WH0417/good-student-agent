"""Business-level source/evidence regressions through the real service API."""
from pathlib import Path
import tempfile
import unittest

from good_student.service import StudentService
from good_student.storage import DataError


class FocusIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="good-student-focus-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.service = StudentService(self.root / "data")
        self.course = self.service.store.create("数据结构", mode="pass")["course"]["id"]
        self.file_number = 0

    def import_text(self, text, *, kind="transcript", speaker="unknown", suffix=".txt", course=None):
        self.file_number += 1
        path = self.root / (f"lecture-{self.file_number}" + suffix)
        path.write_text(text, encoding="utf-8")
        return self.service.import_material(course or self.course, path, kind, speaker=speaker)

    def read(self):
        return self.service.store.read(self.course)

    def test_summary_speaker_labels_cannot_create_teacher_focus(self):
        self.import_text("老师：二叉树是考试重点。", kind="summary", speaker="teacher")
        state = self.read()
        self.assertFalse(state["focus"])
        candidate = next(iter(state["candidates"].values()))
        confirmed = self.service.confirm_focus(self.course, candidate["id"], "二叉树", "我把二叉树指定为重点")
        self.assertEqual(confirmed["source_type"], "user_designated")
        self.assertEqual(confirmed["evidence"][0]["quote"], "我把二叉树指定为重点")
        self.assertEqual(confirmed["candidate_context"]["quote"], "老师：二叉树是考试重点。")

    def test_unknown_speaker_remains_candidate(self):
        self.import_text("二叉树一定会考。")
        state = self.read()
        self.assertFalse(state["focus"])
        self.assertEqual(len(state["candidates"]), 1)

    def test_known_teacher_focus_retains_source_and_original_quote(self):
        text = "[00:03] 老师：二叉树一定会考。"
        result = self.import_text(text)
        item = next(iter(self.read()["focus"].values()))
        self.assertEqual(item["source_type"], "teacher_explicit")
        self.assertEqual(item["topic_title"], "二叉树")
        self.assertEqual(item["evidence"][0]["source_id"], result["source"]["id"])
        self.assertEqual(item["evidence"][0]["locator"]["timestamp"], "00:03")
        self.assertEqual(item["evidence"][0]["quote"], "老师：二叉树一定会考。")

    def test_mixed_speakers_in_one_record_do_not_promote_student_statement(self):
        for text, suffix, declared in [
            ("老师：今天讲二叉树的定义。学生：图论一定会考。", ".txt", "unknown"),
            ("1\n00:00:01,000 --> 00:00:06,000\n老师：今天讲二叉树的定义。\n学生：图论一定会考。\n", ".srt", "teacher"),
        ]:
            with self.subTest(suffix=suffix):
                course = self.service.store.create("混合说话人", mode="pass")["course"]["id"]
                self.import_text(text, suffix=suffix, speaker=declared, course=course)
                state = self.service.store.read(course)
                teacher_focus = [f for f in state["focus"].values() if f["source_type"] == "teacher_explicit"]
                self.assertFalse(teacher_focus, "学生明确发言不能继承整段的教师身份")

    def test_later_explicit_cancellation_removes_old_teacher_exam_assertion(self):
        self.import_text("老师：二叉树一定会考。")
        old = next(iter(self.read()["focus"].values()))
        user = self.service.add_focus(self.course, "二叉树", "我仍然想重点复习二叉树")
        self.import_text("老师：二叉树本次期末不考，之前说的一定会考取消。")
        state = self.read()
        self.assertNotEqual(state["focus"][old["id"]]["status"], "active",
                            "明确取消后旧必考结论不能继续作为有效教师重点")
        self.assertEqual(state["focus"][user["id"]]["status"], "active",
                         "教师取消考试范围不能自动撤回用户自己的复习指定")

    def test_candidate_from_another_course_does_not_write_either_course(self):
        self.import_text("二叉树是重点。", kind="summary")
        before = self.read()
        candidate = next(iter(before["candidates"].values()))
        other = self.service.store.create("线性代数", mode="high")["course"]["id"]
        other_before = self.service.store.read(other)
        with self.assertRaises(DataError):
            self.service.confirm_focus(other, candidate["id"], "二叉树", "错误课程里的指定")
        self.assertEqual(self.read(), before)
        self.assertEqual(self.service.store.read(other), other_before)


if __name__ == "__main__":
    unittest.main()
