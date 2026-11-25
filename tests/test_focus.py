import unittest

from good_student.focus import extract_focus, extract_scope_updates, make_user_focus, topic_id


def segment(text, speaker="teacher", paragraph=1):
    return {"text": text, "speaker": speaker, "locator": {"paragraph": paragraph}}


class FocusExtractionTests(unittest.TestCase):
    def extract(self, text, *, kind="transcript", speaker="teacher", declared="unknown"):
        return extract_focus("source-1", [segment(text, speaker)], kind, declared)

    def test_explicit_exam_evidence_is_verbatim(self):
        text = "老师：二叉树的遍历期末一定会考。"
        formal, pending = self.extract(text)
        self.assertEqual(len(formal), 1)
        self.assertFalse(pending)
        focus = formal[0]
        self.assertEqual(focus["topic_title"], "二叉树的遍历")
        self.assertEqual(focus["claim_type"], "exam_assured")
        self.assertEqual(focus["source_type"], "teacher_explicit")
        self.assertEqual(focus["evidence"][0]["quote"], text)
        self.assertEqual(focus["evidence"][0]["locator"], {"paragraph": 1})
        self.assertNotIn("timestamp", focus["evidence"][0]["locator"])

    def test_priority_is_not_promoted_to_assured_exam(self):
        formal, pending = self.extract("二叉树的遍历是本章的重点。")
        self.assertEqual(formal[0]["topic_title"], "二叉树的遍历")
        self.assertEqual(formal[0]["claim_type"], "priority")
        self.assertFalse(pending)

    def test_one_character_concrete_topic_is_allowed(self):
        formal, pending = self.extract("栈是重点。")
        self.assertEqual(formal[0]["topic_title"], "栈")
        self.assertFalse(pending)

    def test_important_summary_is_reviewable_but_never_teacher_evidence(self):
        formal, pending = self.extract("老师强调栈很重要", kind="summary", speaker="unknown")
        self.assertFalse(formal)
        self.assertEqual(pending[0]["topic_guess"], "栈")
        self.assertEqual(pending[0]["quote"], "老师强调栈很重要")

    def test_prefix_forms(self):
        for text in ["考试重点是二叉树的遍历。", "请大家重点复习二叉树的遍历。", "考试会考二叉树的遍历。"]:
            with self.subTest(text=text):
                formal, pending = self.extract(text)
                self.assertEqual(formal[0]["topic_title"], "二叉树的遍历")
                self.assertFalse(pending)

    def test_summary_always_requires_user_designation(self):
        text = "二叉树的遍历是重点。"
        formal, pending = self.extract(text, kind="summary")
        self.assertFalse(formal)
        self.assertEqual(pending[0]["quote"], text)
        self.assertIn("纪要", pending[0]["reason"])
        user = make_user_focus("二叉树的遍历", "我确认把二叉树遍历列为重点", pending[0])
        self.assertEqual(user["source_type"], "user_designated")
        self.assertEqual(user["claim_type"], "user_priority")
        self.assertEqual(user["candidate_context"]["quote"], text)
        self.assertEqual(user["evidence"][0]["quote"], "我确认把二叉树遍历列为重点")

    def test_negative_conditional_and_past_examples_never_certified(self):
        for text in [
            "二叉树不一定考。", "二叉树不是重点。", "重点内容今年不考。",
            "如果今年考证明，二叉树证明是重点。", "二叉树可能是重点。",
            "二叉树去年必考。", "二叉树会不会是重点？", "二叉树必考吗？",
            "有人说二叉树是重点。", "同学说二叉树一定会考。",
        ]:
            with self.subTest(text=text):
                formal, pending = self.extract(text)
                self.assertFalse(formal)
                # Statements with an extraction signal remain inspectable.
                if "重点" in text or "必考" in text or "一定会考" in text:
                    self.assertTrue(pending)

    def test_following_negation_qualifies_whole_segment(self):
        formal, pending = self.extract("二叉树一定会考。刚才说错了，实际上不一定考。")
        self.assertFalse(formal)
        self.assertIn("否定", pending[0]["reason"])

    def test_no_question_mark_still_does_not_certify_a_question(self):
        for text in ["二叉树必考吗", "二叉树是重点吧", "考试会考什么", "哪些概念是重点"]:
            with self.subTest(text=text):
                formal, pending = self.extract(text)
                self.assertFalse(formal)
                self.assertTrue(pending)

    def test_negated_guarantee_cannot_be_read_as_guarantee(self):
        for text in [
            "二叉树是重点，但不能保证一定会考。",
            "二叉树是重点，不代表一定会考。",
            "无法确认二叉树是重点。",
        ]:
            with self.subTest(text=text):
                formal, pending = self.extract(text)
                self.assertFalse(formal)
                self.assertIn("否定", pending[0]["reason"])

    def test_words_or_examples_are_not_teacher_designations(self):
        for text in ["解释重点这个词的含义。", "例如二叉树必考这种说法就是一句承诺。"]:
            with self.subTest(text=text):
                formal, pending = self.extract(text)
                self.assertFalse(formal)
                self.assertTrue(pending)

    def test_role_must_be_confirmed_and_student_overrides_declaration(self):
        for role, declared in [("unknown", "unknown"), ("student", "teacher")]:
            with self.subTest(role=role):
                formal, pending = self.extract("二叉树是重点。", speaker=role, declared=declared)
                self.assertFalse(formal)
                self.assertTrue(pending)
        formal, pending = self.extract("二叉树是重点。", speaker="unknown", declared="teacher")
        self.assertEqual(len(formal), 1)
        self.assertFalse(pending)

    def test_embedded_student_or_unmapped_label_prevents_teacher_inheritance(self):
        for text in [
            "老师：今天讲定义。学生：图论一定会考。",
            "老师：今天讲定义。\n学生：图论一定会考。",
            "老师：今天讲定义。讲者2：图论一定会考。",
            "老师：今天讲定义。李同学：图论一定会考。",
        ]:
            with self.subTest(text=text):
                formal, pending = self.extract(text, declared="teacher")
                self.assertFalse(formal)
                self.assertTrue(pending)

    def test_ambiguous_reference_does_not_use_previous_segment(self):
        inputs = [segment("上一页讲二叉树。"), segment("这部分一定会考。", paragraph=2)]
        formal, pending = extract_focus("s", inputs, "transcript")
        self.assertFalse(formal)
        self.assertEqual(pending[0]["topic_guess"], "")
        self.assertEqual(pending[0]["locator"], {"paragraph": 2})

    def test_non_classroom_sources_cannot_create_teacher_evidence(self):
        for kind in ["material", "exam", "exercise", "solution"]:
            formal, pending = self.extract("二叉树是重点。", kind=kind)
            self.assertEqual((formal, pending), ([], []))

    def test_repeated_evidence_and_import_order_have_stable_ids(self):
        a = segment("二叉树是重点。", paragraph=1)
        b = segment("二叉树是重点。", paragraph=2)
        first, pending = extract_focus("s", [a, b, a], "transcript")
        second, _ = extract_focus("s", [b, a], "transcript")
        self.assertFalse(pending)
        self.assertEqual(len(first), 1)
        self.assertEqual(len(first[0]["evidence"]), 2)
        self.assertEqual(first[0]["id"], second[0]["id"])
        cross_source, _ = extract_focus("other", [a], "transcript")
        self.assertEqual(first[0]["id"], cross_source[0]["id"])
        self.assertNotEqual(first[0]["evidence"], cross_source[0]["evidence"])

    def test_multiple_clear_statements_retain_their_own_claim_strength(self):
        for text in ["二叉树是重点。图论一定会考。", "二叉树是重点，图论一定会考。"]:
            with self.subTest(text=text):
                formal, pending = self.extract(text)
                self.assertFalse(pending)
                self.assertEqual(
                    {f["topic_title"]: f["claim_type"] for f in formal},
                    {"二叉树": "priority", "图论": "exam_assured"},
                )
                self.assertTrue(all(f["evidence"][0]["quote"] == text for f in formal))

    def test_topic_identity_and_user_identity_are_stable(self):
        self.assertEqual(topic_id("  二叉树。"), topic_id("二叉树"))
        one = make_user_focus("二叉树", "第一次说明")
        two = make_user_focus("二叉树", "另一次说明")
        self.assertEqual(one["id"], two["id"])
        self.assertEqual(one["topic_id"], topic_id("二叉树"))
        with self.assertRaises(ValueError):
            topic_id("  ")
        with self.assertRaises(ValueError):
            make_user_focus("二叉树", " ")

    def test_input_locator_is_not_modified(self):
        source = segment("二叉树是重点。")
        formal, _ = extract_focus("s", [source], "transcript")
        formal[0]["evidence"][0]["locator"]["timestamp"] = "fake"
        self.assertNotIn("timestamp", source["locator"])


class ScopeUpdatesTests(unittest.TestCase):
    def updates(self, text, kind="transcript", speaker="teacher", declared="unknown"):
        return extract_scope_updates("s", [segment(text, speaker)], kind, declared)

    def test_concrete_current_exclusions_preserve_evidence(self):
        for text in [
            "老师：二叉树不考。",
            "老师：二叉树本次期末不考。",
            "老师：二叉树本学期不考。",
            "老师：取消二叉树必考。",
            "老师：之前二叉树会考取消。",
            "老师：二叉树本次期末不考，之前说的一定会考取消。",
        ]:
            with self.subTest(text=text):
                updates = self.updates(text)
                self.assertEqual(len(updates), 1)
                self.assertEqual(updates[0]["topic_title"], "二叉树")
                self.assertEqual(updates[0]["topic_id"], topic_id("二叉树"))
                self.assertEqual(updates[0]["quote"], text)
                self.assertEqual(updates[0]["locator"], {"paragraph": 1})
                self.assertEqual(updates[0]["action"], "retract")

    def test_no_cancellation_from_uncertainty_history_or_ambiguous_object(self):
        for text in [
            "二叉树不一定考。", "二叉树可能不考。", "如果二叉树不考。",
            "二叉树去年不考。", "二叉树不考吗", "二叉树不是不考。",
            "听说二叉树不考。", "这部分不考。", "之前说的一定会考取消。",
        ]:
            with self.subTest(text=text):
                self.assertEqual(self.updates(text), [])

    def test_only_confirmed_teacher_transcript_can_update_scope(self):
        self.assertFalse(self.updates("二叉树不考。", kind="summary"))
        self.assertFalse(self.updates("二叉树不考。", kind="material"))
        self.assertFalse(self.updates("二叉树不考。", speaker="student", declared="teacher"))
        self.assertFalse(self.updates("二叉树不考。", speaker="unknown"))
        self.assertTrue(self.updates("二叉树不考。", speaker="unknown", declared="teacher"))
        self.assertFalse(self.updates("老师：今天讲定义。学生：二叉树不考。", declared="teacher"))


if __name__ == "__main__":
    unittest.main()
