"""Course-bound practice workflows through the real service and file store."""
from pathlib import Path
import tempfile
import unittest

from good_student.cli import dispatch, parser
from good_student.service import StudentService
from good_student.storage import DataError


class PracticeWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="good-student-practice-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.service = StudentService(self.root / "data")
        self.course_a = self.service.store.create("集合课程", "pass")["course"]["id"]
        self.course_b = self.service.store.create("另一门课程", "high")["course"]["id"]
        self.input_dir = self.root / "inputs"
        self.input_dir.mkdir()

    def import_question(self, course=None, filename="hw9.txt", question=None):
        course = course or self.course_a
        text = question or "1. How many elements are in {1, 2}?\nAnswer: 2\n"
        path = self.input_dir / filename
        path.write_text(text, encoding="utf-8")
        imported = self.service.import_material(course, path, "exercise")
        state = self.service.store.read(course)
        source_id = imported["source"]["id"]
        item = next(q for q in state["questions"].values() if q["source_id"] == source_id)
        return item, path

    def generated_with_topic(self):
        material = self.input_dir / "集合.md"
        material.write_text("# 集合计数\n集合中的每个不同元素计数一次。", encoding="utf-8")
        self.service.import_material(self.course_a, material, "material")
        state = self.service.store.read(self.course_a)
        topic_id = next(tid for tid, topic in state["topics"].items() if topic["title"] == "集合计数")
        generated = self.service.add_generated(self.course_a, {
            "question": "How many elements are in {1, 2, 3}?", "answer": "3",
            "explanation": "Count each of the three distinct elements once.",
            "topic_ids": [topic_id],
        })
        return generated, topic_id

    def test_incremental_import_and_restart_preserve_attempt_association(self):
        original, _ = self.import_question()
        first = self.service.answer(self.course_a, original["id"], "7")
        self.assertEqual(first["result"], "wrong")
        self.import_question(filename="000_exam.txt", question="1. What is five plus five?\nAnswer: 10\n")
        restarted = StudentService(self.root / "data")
        state = restarted.store.read(self.course_a)
        record = state["attempts"][0]
        self.assertEqual(record["question_id"], original["id"])
        self.assertEqual(state["questions"][record["question_id"]]["question"], original["question"])
        self.assertEqual(len(state["questions"]), 2)
        again = restarted.answer(self.course_a, original["id"], "2")
        self.assertFalse(again["independent"])
        self.assertTrue(again["answer_viewed"])

    def test_other_course_cannot_show_or_answer_foreign_question(self):
        original, _ = self.import_question()
        original_a = self.service.store.read(self.course_a)
        original_b = self.service.store.read(self.course_b)
        with self.assertRaises(DataError):
            self.service.show_question(self.course_b, original["id"], show_answer=True)
        with self.assertRaises(DataError):
            self.service.answer(self.course_b, original["id"], "2")
        self.assertEqual(self.service.store.read(self.course_a), original_a)
        self.assertEqual(self.service.store.read(self.course_b), original_b)

    def test_foreign_generated_reference_does_not_write_partial_question(self):
        original, _ = self.import_question()
        before = self.service.store.read(self.course_b)
        with self.assertRaises(ValueError):
            self.service.add_generated(self.course_b, {
                "question": "How many elements are in {1, 2, 3}?", "answer": "3",
                "explanation": "Count the distinct elements.", "reference_ids": [original["id"]],
                "source_ids": original["source_ids"],
            })
        self.assertEqual(self.service.store.read(self.course_b), before)

    def test_question_and_list_hide_answers_without_marking_them_viewed(self):
        original, _ = self.import_question()
        shown = self.service.show_question(self.course_a, original["id"])
        self.assertNotIn("answer", shown)
        self.assertNotIn("answer_source", shown)
        listed = dispatch(parser().parse_args([
            "--data-dir", str(self.root / "data"), "practice-list", "--course", self.course_a
        ]))
        self.assertNotIn("answer", listed[0])
        self.assertNotIn("explanation", listed[0])
        attempt = self.service.answer(self.course_a, original["id"], "2")
        self.assertTrue(attempt["independent"])
        self.assertFalse(attempt["answer_viewed"])

    def test_revealing_answer_is_persisted_before_later_correct_attempt(self):
        question, topic_id = self.generated_with_topic()
        shown = self.service.show_question(self.course_a, question["id"], show_answer=True)
        self.assertEqual(shown["answer"], "3")
        restarted = StudentService(self.root / "data")
        attempt = restarted.answer(self.course_a, question["id"], "3")
        self.assertEqual(attempt["result"], "right")
        self.assertTrue(attempt["answer_viewed"])
        self.assertFalse(attempt["independent"])
        self.assertEqual(restarted.store.read(self.course_a)["progress"][topic_id]["status"], "needs_review")

    def test_feedback_prevents_second_attempt_being_certified_independent(self):
        question, topic_id = self.generated_with_topic()
        first = self.service.answer(self.course_a, question["id"], "99")
        self.assertEqual(first["feedback"]["reference_answer"], "3")
        second = self.service.answer(self.course_a, question["id"], "3")
        self.assertTrue(first["independent"])
        self.assertFalse(second["independent"])
        self.assertTrue(second["answer_viewed"])
        self.assertEqual(self.service.store.read(self.course_a)["progress"][topic_id]["status"], "needs_review")

    def test_hint_used_cannot_be_recorded_as_independent_pass(self):
        question, topic_id = self.generated_with_topic()
        attempt = self.service.answer(self.course_a, question["id"], "3", used_hint=True)
        self.assertTrue(attempt["used_hint"])
        self.assertFalse(attempt["independent"])
        self.assertEqual(self.service.store.read(self.course_a)["progress"][topic_id]["status"], "needs_review")

    def test_duplicate_import_preserves_views_and_history(self):
        question, path = self.import_question()
        self.service.show_question(self.course_a, question["id"], show_answer=True)
        attempt = self.service.answer(self.course_a, question["id"], "2")
        duplicate = self.service.import_material(self.course_a, path, "exercise")
        state = self.service.store.read(self.course_a)
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(len(state["questions"]), 1)
        self.assertEqual([record["id"] for record in state["attempts"]], [attempt["id"]])
        self.assertTrue(state["question_views"][question["id"]]["answer_viewed"])

    def test_repeated_correct_answer_preserves_prior_independent_pass(self):
        question, topic_id = self.generated_with_topic()
        first = self.service.answer(self.course_a, question["id"], "3")
        self.assertTrue(first["independent"])
        self.assertEqual(self.service.store.read(self.course_a)["progress"][topic_id]["status"], "practice_passed")
        second = self.service.answer(self.course_a, question["id"], "3")
        self.assertFalse(second["independent"])
        # A non-independent repeat is no new evidence; it should not erase the
        # already saved independently successful attempt merely for being right.
        self.assertEqual(self.service.store.read(self.course_a)["progress"][topic_id]["status"], "practice_passed")


if __name__ == "__main__":
    unittest.main()
