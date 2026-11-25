"""Revision uses course materials even when only a few classes were recorded."""
from copy import deepcopy
import unittest

from good_student.focus import topic_id
from good_student.knowledge import build_knowledge, compute_coverage, plan_review


def state_with_materials(mode="pass", recorded=True):
    sources = {
        "slides": {
            "id": "slides", "filename": "全科课件.md", "kind": "material", "status": "complete",
            "pages": [{"number": 1, "text": "# 栈\n后进先出。\n# 队列\n先进先出。\n# 二叉树\n树结构。\n# 图\n顶点和边。"}],
        },
    }
    focus = {}
    if recorded:
        sources["lecture"] = {
            "id": "lecture", "filename": "仅第二课逐字稿.txt", "kind": "transcript", "status": "complete",
            "pages": [{"number": 1, "text": "老师：队列是重点。"}],
        }
        focus["queue-focus"] = {
            "id": "queue-focus", "topic_id": topic_id("队列"), "topic_title": "队列",
            "source_type": "teacher_explicit", "claim_type": "priority", "status": "active",
            "evidence": [{"source_id": "lecture", "quote": "老师：队列是重点。", "locator": {"paragraph": 1}}],
        }
    return {
        "course": {"name": "数据结构", "mode": mode, "score_max": 100, "passing_score": 60},
        "sources": sources, "focus": focus, "topics": build_knowledge(sources, focus), "progress": {},
    }


class PartialCoverageTest(unittest.TestCase):
    def test_partial_recording_cannot_remove_unrecorded_material_chapters(self):
        state = state_with_materials()
        plan = plan_review(state)
        items = {item["topic_id"]: item for item in plan["items"]}
        for title in ("栈", "队列", "二叉树", "图"):
            self.assertIn(topic_id(title), items)
        self.assertEqual(items[topic_id("队列")]["group"], "priority")
        self.assertEqual(items[topic_id("二叉树")]["group"], "core_supplement")
        self.assertFalse(items[topic_id("二叉树")]["focus_labels"])
        self.assertIn("课程主线", plan["coverage_note"])

    def test_no_recording_is_a_supported_materials_first_workflow(self):
        state = state_with_materials(recorded=False)
        plan = plan_review(state)
        self.assertEqual(len(plan["items"]), 4)
        self.assertEqual(plan["coverage"]["basis"], "course_materials")
        self.assertFalse(plan["coverage"]["has_classroom_records"])
        self.assertFalse(plan["coverage"]["recordings_required"])
        self.assertIn("没有录音也可以复习", plan["coverage"]["recording_note"])
        self.assertTrue(all(not item["focus_labels"] for item in plan["items"]))

    def test_classroom_only_does_not_claim_whole_course_coverage(self):
        state = state_with_materials()
        del state["sources"]["slides"]
        state["topics"] = build_knowledge(state["sources"], state["focus"])
        plan = plan_review(state)
        self.assertEqual(plan["coverage"]["basis"], "classroom_only")
        self.assertEqual(plan["coverage"]["recording_coverage"], "unknown")
        self.assertIn("补充课件", plan["coverage"]["summary"])
        self.assertIn("没有录到不表示不考", plan["coverage"]["recording_note"])
        self.assertNotIn("recording_coverage_percent", plan["coverage"])

    def test_adding_a_recording_strengthens_priority_without_shrinking_backbone(self):
        before_state = state_with_materials(recorded=False)
        after_state = state_with_materials(recorded=True)
        before = plan_review(before_state)
        frozen = deepcopy(after_state)
        after = plan_review(after_state)
        material_ids = {topic_id(title) for title in ("栈", "队列", "二叉树", "图")}
        self.assertTrue(material_ids <= {item["topic_id"] for item in before["items"]})
        self.assertTrue(material_ids <= {item["topic_id"] for item in after["items"]})
        self.assertEqual(after_state, frozen)
        self.assertEqual(len(after_state["focus"]), 1)

    def test_file_counts_are_not_misrepresented_as_recorded_lesson_counts(self):
        state = state_with_materials()
        state["sources"]["audio"] = {"kind": "audio", "status": "complete", "pages": [], "segments": [{"text": "队列"}]}
        state["sources"]["summary"] = {"kind": "summary", "status": "complete", "pages": [{"text": "队列"}]}
        coverage = compute_coverage(state)
        self.assertEqual(coverage["counts"]["audio"], 1)
        self.assertEqual(coverage["counts"]["transcript"], 1)
        self.assertEqual(coverage["counts"]["summary"], 1)
        self.assertEqual(coverage["recording_coverage"], "unknown")
        self.assertNotIn("recorded_lessons", coverage)
        self.assertNotIn("missing_lecture_names", coverage)

    def test_explicit_topic_scope_is_respected_by_both_modes(self):
        for mode in ("high", "pass"):
            with self.subTest(mode=mode):
                state = state_with_materials(mode=mode)
                state["course"]["exam_scope"] = {"topic_titles": ["队列", "二叉树"]}
                plan = plan_review(state)
                self.assertEqual({item["topic_id"] for item in plan["items"]}, {topic_id("队列"), topic_id("二叉树")})
                self.assertIn(topic_id("栈"), plan["uncovered_topic_ids"])

    def test_explicit_topic_exclusion_does_not_get_reintroduced_as_priority(self):
        state = state_with_materials()
        state["topics"][topic_id("队列")]["in_exam_scope"] = False
        plan = plan_review(state)
        self.assertNotIn(topic_id("队列"), {item["topic_id"] for item in plan["items"]})
        self.assertEqual(len(state["focus"]), 1, "Planning must retain historical evidence, not erase it")

    def test_free_prose_scope_is_not_guessed_into_a_restrictive_filter(self):
        state = state_with_materials()
        state["course"]["exam_scope"] = "按老师最后公布的范围为准，目前尚未整理成章节列表"
        plan = plan_review(state)
        self.assertTrue({topic_id("栈"), topic_id("二叉树")} <= {item["topic_id"] for item in plan["items"]})

    def test_questions_only_can_start_without_demanding_all_recordings(self):
        state = state_with_materials(recorded=False)
        state["sources"] = {"questions": {
            "id": "questions", "filename": "自测题.md", "kind": "exercise", "status": "complete",
            "pages": [{"number": 1, "text": "# 队列\n一道基础题。"}],
        }}
        state["topics"] = build_knowledge(state["sources"], {})
        plan = plan_review(state)
        self.assertTrue(plan["items"])
        self.assertEqual(plan["coverage"]["basis"], "questions_only")
        self.assertFalse(plan["coverage"]["recordings_required"])

    def test_unprocessed_audio_does_not_count_as_usable_classroom_content(self):
        coverage = compute_coverage({"sources": {"audio": {"kind": "audio", "status": "pending", "pages": [], "segments": []}}})
        self.assertEqual(coverage["basis"], "no_usable_materials")
        self.assertIn("尚未处理完成", coverage["summary"])
        self.assertEqual(coverage["counts"]["incomplete"], 1)


if __name__ == "__main__":
    unittest.main()
