from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from good_student.focus import topic_id
from good_student.knowledge import build_knowledge, make_mindmap, make_outline, plan_review
from good_student.exporter import export_course


def sample_state():
    a, b, c = (topic_id(t) for t in ("二叉树", "线性表", "前置概念"))
    sources = {
        "s1": {"id": "s1", "filename": "第一课.md", "kind": "transcript", "status": "complete",
               "stored_path": "sources/s1.md", "pages": [{"number": 1, "text": "# 二叉树\n原资料中的说明。"}]},
        "s2": {"id": "s2", "filename": "第二课.md", "kind": "transcript", "status": "partial",
               "missing_ranges": [[30, 60]], "pages": [{"number": 1, "text": "# 线性表\n另一堂课的说明。"}]},
    }
    focus = {
        "f1": {"id": "f1", "topic_id": a, "topic_title": "二叉树", "source_type": "teacher_explicit",
               "claim_type": "exam_assured", "status": "active",
               "evidence": [{"source_id": "s1", "locator": {"paragraph": 2}, "quote": "二叉树一定会考。"}]},
        "f2": {"id": "f2", "topic_id": b, "topic_title": "线性表", "source_type": "user_designated",
               "claim_type": "user_priority", "status": "active",
               "evidence": [{"source_id": None, "locator": {}, "quote": "我想优先复习线性表。"}]},
    }
    topics = build_knowledge(sources, focus)
    topics[c] = {"id": c, "title": "前置概念", "text": "", "source_refs": [], "prerequisite_ids": []}
    return {
        "version": 1, "updated_at": "2026-11-18T12:00:00+08:00",
        "course": {"id": "course1", "name": "数据结构", "mode": "high", "score_max": 100, "passing_score": 60},
        "sources": sources,
        "lectures": {
            "l1": {"id": "l1", "name": "第一课", "source_ids": ["s1"], "summary": "第一课保存的摘要。", "status": "complete"},
            "l2": {"id": "l2", "name": "第二课", "source_ids": ["s2"], "summary": "第二课保存的摘要。", "status": "partial"},
        },
        "topics": topics, "focus": focus,
        "candidates": {"candidate1": {"id": "candidate1", "source_id": "s2", "locator": {"paragraph": 4},
                                          "quote": "如果考到栈就看这一节。", "reason": "条件表述", "status": "pending"}},
        "questions": {"q1": {"question": "一道变式练习。", "answer": "答案在后面。", "explanation": "给定条件下的推理。",
                               "origin": "ai_generated", "source_id": "s1", "reference_ids": []}},
        "attempts": [], "progress": {},
    }


class KnowledgeTest(unittest.TestCase):
    def test_build_has_stable_topics_and_real_page_references(self):
        state = sample_state()
        topics = build_knowledge(state["sources"], state["focus"])
        self.assertIn(topic_id("二叉树"), topics)
        self.assertEqual(topics[topic_id("二叉树")]["source_refs"][0]["locator"], {"page": 1})
        self.assertIn("原资料中的说明", topics[topic_id("二叉树")]["text"])
        self.assertNotIn("exam_probability", topics[topic_id("二叉树")])
        reordered = dict(reversed(list(state["sources"].items())))
        self.assertEqual(set(topics), set(build_knowledge(reordered, state["focus"])))

    def test_plain_text_uses_filename_not_invented_topic(self):
        sources = {"s": {"filename": "课堂记录.txt", "status": "complete", "kind": "transcript",
                          "pages": [{"number": 1, "text": "这是一段没有标题的课堂原文。"}]}}
        topics = build_knowledge(sources, {})
        self.assertEqual([t["title"] for t in topics.values()], ["课堂记录"])

    def test_source_location_types_do_not_turn_text_or_slides_into_pages(self):
        sources = {
            "text": {"filename": "课堂纪要.md", "location_type": "text_block", "status": "complete",
                     "kind": "summary", "pages": [{"number": 1, "text": "# 栈\n纪要中的内容。"}]},
            "slide": {"filename": "第1章.pptx", "location_type": "slide", "status": "complete",
                      "kind": "material", "pages": [{"number": 3, "text": "队列\n先进先出。"}]},
            "pdf": {"filename": "讲义.pdf", "location_type": "page", "status": "complete",
                    "kind": "material", "pages": [{"number": 8, "text": "# 链表\n讲义内容。"}]},
        }
        topics = build_knowledge(sources, {})
        self.assertEqual(topics[topic_id("栈")]["source_refs"][0]["locator"], {"text_block": 1})
        self.assertEqual(topics[topic_id("队列")]["source_refs"][0]["locator"], {"slide": 3})
        self.assertEqual(topics[topic_id("链表")]["source_refs"][0]["locator"], {"page": 8})
        outline = make_outline(topics, {})
        self.assertIn("文本块 1", outline)
        self.assertIn("第 3 张幻灯片", outline)
        self.assertIn("第 8 页", outline)
        self.assertNotIn("第 1 页", outline)

    def test_mode_is_explicit_and_date_does_not_switch_it(self):
        state = sample_state()
        state["course"]["exam_date"] = "2000-01-01"
        plan = plan_review(state)
        self.assertEqual(plan["mode"], "high")
        self.assertEqual(len(plan["items"]), len(state["topics"]))
        state["course"]["mode"] = None
        with self.assertRaises(ValueError):
            plan_review(state)

    def test_pass_prioritizes_focus_and_explicit_prerequisites(self):
        state = sample_state()
        state["course"]["mode"] = "pass"
        state["focus"].pop("f2")
        a, b, c = (topic_id(t) for t in ("二叉树", "线性表", "前置概念"))
        state["topics"][a]["prerequisite_ids"] = [c]
        plan = plan_review(state)
        ids = [item["topic_id"] for item in plan["items"]]
        self.assertEqual(set(ids), {a, c})
        self.assertLess(ids.index(c), ids.index(a))
        self.assertIn(b, plan["uncovered_topic_ids"])
        self.assertEqual(plan["target"], {"score_max": 100, "passing_score": 60})

    def test_pass_without_focus_is_honest_about_basic_material(self):
        state = sample_state()
        state["course"]["mode"] = "pass"
        state["focus"] = {}
        plan = plan_review(state)
        self.assertEqual(len(plan["items"]), len(state["topics"]))
        self.assertIn("尚无有效正式重点", plan["coverage_note"])
        self.assertTrue(all(not item["focus_labels"] for item in plan["items"]))

    def test_cycles_are_reported_and_do_not_loop(self):
        state = sample_state()
        a, b = topic_id("二叉树"), topic_id("线性表")
        state["topics"][a]["prerequisite_ids"] = [b]
        state["topics"][b]["prerequisite_ids"] = [a]
        plan = plan_review(state)
        self.assertEqual(len({item["topic_id"] for item in plan["items"]}), len(state["topics"]))
        self.assertTrue(plan["dependency_warnings"])

    def test_mindmap_keeps_source_as_one_label(self):
        title = '"]\nclick x "https://bad.example" <script> [%%'
        topics = {"t": {"title": title, "source_refs": []}}
        diagram = make_mindmap(topics, {})
        self.assertEqual(len(diagram.splitlines()), 3)
        self.assertNotIn("<script>", diagram)
        self.assertNotIn('\nclick ', diagram)
        self.assertIn("&#34;", diagram)
        self.assertIn("课程知识脉络", diagram)
        self.assertIn("知识脉络", make_outline(topics, {}))


class ExportTest(unittest.TestCase):
    def test_export_has_both_lectures_sources_candidates_and_answers_separate(self):
        state = sample_state()
        original = copy.deepcopy(state)
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "snapshot"
            manifest = export_course(state, destination)
            self.assertEqual(manifest["counts"]["active_focus"], 2)
            focus = (destination / "exam_focus.md").read_text(encoding="utf-8")
            self.assertIn("二叉树一定会考", focus)
            self.assertIn("用户指定", focus)
            self.assertIn("第一课.md", focus)
            self.assertIn("第 2 段", focus)
            self.assertIn("待确认内容", focus)
            self.assertIn("以下未计入有效正式重点", focus)
            self.assertIn("partial", focus)
            review = (destination / "review_guide.md").read_text(encoding="utf-8")
            self.assertIn("第一课保存的摘要", review)
            self.assertIn("第二课保存的摘要", review)
            practice = (destination / "practice.md").read_text(encoding="utf-8")
            self.assertIn("AI 生成练习", practice)
            self.assertLess(practice.index("## 题目区"), practice.index("## 参考答案与解析"))
            self.assertGreater(practice.index("答案在后面"), practice.index("## 参考答案与解析"))
            saved = json.loads((destination / "export_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(saved, manifest)
            self.assertTrue(all((destination / name).is_file() for name in manifest["files"]))
        self.assertEqual(state, original)

    def test_retracted_focus_not_counted_and_existing_snapshot_refused(self):
        state = sample_state()
        state["focus"]["f1"]["status"] = "retracted"
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "snapshot"
            result = export_course(state, destination)
            self.assertEqual(result["counts"]["active_focus"], 1)
            focus = (destination / "exam_focus.md").read_text(encoding="utf-8")
            self.assertNotIn("二叉树一定会考", focus)
            with self.assertRaises(FileExistsError):
                export_course(state, destination)

    def test_scope_changes_show_cancellation_and_conflict_evidence_with_sources(self):
        state = sample_state()
        state["sources"]["s2"]["location_type"] = "text_block"
        state["sources"]["s2"]["pages"] = [{"number": 1, "text": "二叉树这一部分不考。\n队列是否考试还要等范围确定。"}]
        state["focus"]["f1"].update(status="retracted", scope_changes=[{
            "source_id": "s2", "locator": {"paragraph": 1}, "quote": "二叉树这一部分不考。",
            "action": "retract", "topic_id": topic_id("二叉树"), "topic_title": "二叉树",
        }])
        state["focus"]["f2"].update(status="conflicted", scope_changes=[{
            "source_id": "s2", "locator": {"paragraph": 2}, "quote": "队列是否考试还要等范围确定。",
            "action": "conflict", "topic_id": topic_id("队列"), "topic_title": "队列",
        }])
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "snapshot"
            manifest = export_course(state, destination)
            focus = (destination / "exam_focus.md").read_text(encoding="utf-8")
            self.assertEqual(manifest["counts"]["active_focus"], 0)
            self.assertIn("二叉树这一部分不考。", focus)
            self.assertIn("队列是否考试还要等范围确定。", focus)
            self.assertIn("变更依据：撤回范围", focus)
            self.assertIn("变更依据：范围冲突", focus)
            self.assertIn("第二课.md", focus)
            self.assertIn("第 1 段", focus)
            self.assertIn("第 2 段", focus)
            self.assertGreater(focus.index("二叉树这一部分不考。"), focus.index("## 已撤回或存在冲突的记录"))

    def test_export_without_selected_mode_does_not_invent_plan(self):
        state = sample_state()
        state["course"]["mode"] = None
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "snapshot"
            export_course(state, destination)
            review = (destination / "review_guide.md").read_text(encoding="utf-8")
            self.assertIn("请选择", review)
            self.assertIn("尚未生成教学计划", review)

    def test_malicious_filename_is_not_used_as_output_path(self):
        state = sample_state()
        state["sources"]["s1"]["filename"] = "../../outside.md"
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "snapshot"
            export_course(state, destination)
            self.assertFalse((Path(temporary) / "outside.md").exists())
            self.assertEqual(len(list(destination.iterdir())), 5)

    def test_text_import_export_uses_text_block_and_paragraph_labels(self):
        state = sample_state()
        state["sources"]["s1"]["location_type"] = "text_block"
        state["sources"]["s2"]["location_type"] = "text_block"
        state["topics"] = build_knowledge(state["sources"], state["focus"])
        # Practice extraction still has its upstream generic page field.
        state["questions"]["q1"]["source"] = {"file": "第一课.md", "page": 1}
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "snapshot"
            export_course(state, destination)
            review = (destination / "review_guide.md").read_text(encoding="utf-8")
            practice = (destination / "practice.md").read_text(encoding="utf-8")
            focus = (destination / "exam_focus.md").read_text(encoding="utf-8")
            self.assertIn("文本块 1", review)
            self.assertNotIn("第 1 页", review)
            self.assertIn("文本块 1", practice)
            self.assertNotIn("第 1 页", practice)
            self.assertIn("第 2 段", focus)


if __name__ == "__main__":
    unittest.main()
