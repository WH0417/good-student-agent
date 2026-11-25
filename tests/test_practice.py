import unittest

from good_student.practice import build_bank, grade_answer, validate_generated


def source(sid, filename, kind, text, status="complete"):
    return {"id": sid, "filename": filename, "stored_path": "", "kind": kind,
            "pages": [{"number": 1, "text": text}], "status": status}


class PracticeExtractionTests(unittest.TestCase):
    def test_new_file_does_not_renumber_previous_question(self):
        old = source("s_old", "exercise9.txt", "exercise",
                     "1. How many elements are in {1, 2}?\nAnswer: 2")
        first = build_bank({"s_old": old})
        qid = next(iter(first))
        new = source("s_000", "000_exam.txt", "exam",
                     "1. What is one plus three?\nAnswer: 4")
        second = build_bank({"s_000": new, "s_old": old})
        self.assertEqual(second[qid]["question"], first[qid]["question"])
        self.assertEqual(len(second), 2)
        self.assertEqual(second[qid]["origin"], "original")
        self.assertEqual(second[qid]["source_id"], "s_old")

    def test_solution_pairing_preserves_both_sources(self):
        exam = source("s_exam", "hw2.txt", "exercise", "1. How many elements are in {1, 2}?")
        solution = source("s_solution", "hw2solutions.txt", "solution", "1. Answer: 2")
        bank = build_bank({"s_exam": exam, "s_solution": solution})
        self.assertEqual(len(bank), 1)
        item = next(iter(bank.values()))
        self.assertEqual(grade_answer(item, "2")["result"], "right")
        self.assertEqual(item["answer_source_id"], "s_solution")
        self.assertEqual(item["source"]["file"], "hw2.txt")
        self.assertEqual(item["answer_source"]["file"], "hw2solutions.txt")
        self.assertEqual(item["source_ids"], ["s_exam", "s_solution"])

    def test_new_solution_does_not_change_question_identity(self):
        exam = source("s_exam", "hw2.txt", "exercise", "1. How many elements are in {1, 2}?")
        before = build_bank({"s_exam": exam})
        after = build_bank({"s_exam": exam, "s_sol": source(
            "s_sol", "hw2solutions.txt", "solution", "1. Answer: 2")})
        self.assertEqual(set(before), set(after))
        self.assertIsNone(next(iter(before.values()))["answer"])
        self.assertEqual(grade_answer(next(iter(after.values())), "2")["result"], "right")

    def test_transcript_and_unprocessed_sources_do_not_become_questions(self):
        data = {
            "s_transcript": source("s_transcript", "lecture.txt", "transcript", "1. What is one plus one?\nAnswer: 2"),
            "s_pending": source("s_pending", "exam.txt", "exam", "1. What is one plus one?\nAnswer: 2", "pending"),
        }
        self.assertEqual(build_bank(data), {})

    def test_ambiguous_solution_revision_does_not_silently_choose_answer(self):
        data = {
            "q": source("q", "hw2.txt", "exercise", "1. How many elements are in {1, 2}?"),
            "a": source("a", "hw2solutions.txt", "solution", "1. Answer: 2"),
            "b": source("b", "hw2solutions.txt", "solution", "1. Answer: 3"),
        }
        item = next(iter(build_bank(data).values()))
        self.assertIsNone(item["answer"])


class GradingTests(unittest.TestCase):
    def test_exact_integer_rejects_decimal_nearby(self):
        question = {"question": "How many?", "answer": "2", "type": "subjective"}
        self.assertEqual(grade_answer(question, "2.4")["result"], "wrong")
        self.assertEqual(grade_answer(question, "2.0000000001")["result"], "wrong")
        self.assertEqual(grade_answer(question, "2个")["result"], "right")

    def test_equivalent_fraction_decimal_and_percent(self):
        question = {"answer": "1/2", "type": "subjective"}
        for answer in ("0.5", "50%", "2/4", "答案是 1/2"):
            with self.subTest(answer=answer):
                self.assertEqual(grade_answer(question, answer)["result"], "right")

    def test_explicit_tolerance_is_required_and_validated(self):
        question = {"answer": "2", "type": "subjective"}
        self.assertEqual(grade_answer(question, "2.4", 0.5)["result"], "right")
        self.assertEqual(grade_answer(question, "2.4", 0.3)["result"], "wrong")
        for tolerance in (-1, float("nan"), float("inf"), True):
            with self.assertRaises(ValueError):
                grade_answer(question, "2", tolerance)

    def test_unknown_reference_and_open_answer_remain_unsure(self):
        self.assertEqual(grade_answer({"answer": None}, "2")["result"], "unsure")
        self.assertEqual(grade_answer({"answer": "Discuss several factors in detail."}, "because")["result"], "unsure")
        self.assertEqual(grade_answer({"answer": "2"}, "不是2")["result"], "unsure")

    def test_choice_and_truth_still_use_source_answers(self):
        self.assertEqual(grade_answer({"answer": "B", "options": ["A. one", "B. two"]}, "B")["result"], "right")
        self.assertEqual(grade_answer({"answer": "False", "type": "true_false"}, "错误")["result"], "right")


class GeneratedPracticeTests(unittest.TestCase):
    def setUp(self):
        self.state = {
            "sources": {"s1": {"status": "complete"}},
            "questions": {"q1": {"source_id": "s1", "source_ids": ["s1"]}},
            "topics": {"t1": {"source_refs": [{"source_id": "s1", "page": 1}]}},
        }
        self.generated = {"question": "How many elements are in {1, 2, 3}?", "answer": "3",
                          "explanation": "Count each distinct element once.", "reference_ids": ["q1"]}

    def test_generated_origin_and_inferred_source_are_stable(self):
        item = validate_generated(dict(self.generated, origin="teacher_original"), self.state)
        self.assertEqual(item["origin"], "ai_generated")
        self.assertEqual(item["source_ids"], ["s1"])
        self.assertEqual(item["reference_ids"], ["q1"])
        self.assertEqual(item["id"], validate_generated(self.generated, self.state)["id"])

    def test_topic_backed_practice_is_valid(self):
        item = dict(self.generated, reference_ids=[], topic_ids=["t1"])
        self.assertEqual(validate_generated(item, self.state)["source_ids"], ["s1"])

    def test_missing_and_cross_course_evidence_is_rejected(self):
        for changes in ({"reference_ids": []}, {"reference_ids": ["other_course_q"]},
                        {"source_ids": ["other_course_source"]}, {"topic_ids": ["other_topic"]},
                        {"explanation": ""}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_generated(dict(self.generated, **changes), self.state)

    def test_pending_evidence_is_rejected(self):
        self.state["sources"]["s1"]["status"] = "pending"
        with self.assertRaises(ValueError):
            validate_generated(self.generated, self.state)

    def test_choice_answer_must_select_existing_option(self):
        values = dict(self.generated, options=["one", "two"], answer="C")
        with self.assertRaises(ValueError):
            validate_generated(values, self.state)
        values["answer"] = "B"
        self.assertEqual(validate_generated(values, self.state)["options"], ["A. one", "B. two"])


if __name__ == "__main__":
    unittest.main()
