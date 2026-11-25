from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest

from good_student.service import StudentService
from good_student.storage import DataError, Store


class CourseFlowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = StudentService(self.root / "data")
        self.cid = self.service.store.create("数据结构", "pass")["course"]["id"]

    def tearDown(self):
        self.temp.cleanup()

    def material(self, name, text):
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_full_text_focus_mode_export_and_resume(self):
        transcript = self.material("课堂.txt", "老师：二叉树遍历是考试重点。\n老师：队列一定会考。\n老师：栈不一定考。")
        first = self.service.import_material(self.cid, transcript, "transcript")
        state = self.service.store.read(self.cid)
        self.assertGreaterEqual(len(state["focus"]), 2)
        self.assertTrue(all("栈" not in f["topic_title"] for f in state["focus"].values()))
        old_count = len(state["focus"])
        repeated = self.service.import_material(self.cid, transcript, "transcript")
        self.assertTrue(repeated["duplicate"])
        self.assertEqual(len(self.service.store.read(self.cid)["focus"]), old_count)
        summary = self.material("豆包纪要.md", "老师强调链表是重点。")
        second = self.service.import_material(self.cid, summary, "summary", lecture_id=first["lecture_id"])
        self.assertEqual(second["new_focus"], 0)
        state = self.service.store.read(self.cid)
        candidate = next(c for c in state["candidates"].values() if c["source_id"] == second["source"]["id"])
        confirmed = self.service.confirm_focus(self.cid, candidate["id"], "链表", "用户确认链表为重点")
        self.assertEqual(confirmed["source_type"], "user_designated")
        self.service.save_summary(self.cid, first["lecture_id"], "树的遍历与队列是本节内容；链表为用户指定重点。")
        self.service.set_mode(self.cid, "high")
        recovered = StudentService(self.root / "data")
        self.assertEqual(recovered.status(self.cid)["course"]["mode"], "high")
        self.assertEqual(recovered.status(self.cid)["active_focus"], old_count + 1)
        manifest = recovered.export(self.cid)
        self.assertIsInstance(manifest, dict)
        exported = list(recovered.store.course_dir(self.cid).glob("exports/*/exam_focus.md"))
        self.assertEqual(len(exported), 1)
        self.assertIn("链表", exported[0].read_text(encoding="utf-8"))

    def test_same_topic_keeps_two_lecture_evidence_and_retraction(self):
        one = self.service.import_material(self.cid, self.material("first.txt", "老师：队列一定会考。"), "transcript")
        two = self.service.import_material(self.cid, self.material("second.txt", "老师：队列一定会考。\n补充说明。"), "transcript")
        state = self.service.store.read(self.cid)
        item = next(iter(state["focus"].values()))
        self.assertEqual(len(item["evidence"]), 2)
        self.assertNotEqual(one["lecture_id"], two["lecture_id"])
        self.service.retract_focus(self.cid, item["id"], "老师后续说明取消，保留原证据")
        self.assertEqual(self.service.status(self.cid)["active_focus"], 0)
        self.assertEqual(len(self.service.store.read(self.cid)["focus"][item["id"]]["evidence"]), 2)

    def test_unknown_teacher_and_summary_do_not_auto_promote(self):
        result = self.service.import_material(self.cid, self.material("unknown.txt", "队列一定会考。"), "transcript")
        self.assertEqual(result["new_focus"], 0)
        self.assertGreater(result["new_candidates"], 0)
        state = self.service.store.read(self.cid)
        self.assertFalse(state["focus"])

    def test_explicit_course_and_file_boundaries(self):
        other = self.service.store.create("大学英语", "high")["course"]["id"]
        result = self.service.import_material(self.cid, self.material("teacher.txt", "老师：队列一定会考。"), "transcript")
        with self.assertRaises(DataError):
            self.service.source_slice(other, result["source"]["id"])
        with self.assertRaises(DataError):
            self.service.source_slice("../elsewhere", result["source"]["id"])
        with self.assertRaises(DataError):
            self.service.store.contained(self.cid, "../../outside.txt")
        self.assertEqual(self.service.status(other)["active_focus"], 0)

    def test_failing_transaction_preserves_saved_progress(self):
        before = self.service.store.read(self.cid)
        with self.assertRaises(RuntimeError):
            with self.service.store.transaction(self.cid) as state:
                state["course"]["name"] = "should not persist"
                raise RuntimeError("simulated interrupted processing")
        self.assertEqual(self.service.store.read(self.cid), before)

    def test_concurrent_course_updates_do_not_lose_designations(self):
        def add(index):
            service = StudentService(self.root / "data")
            service.add_focus(self.cid, f"知识点{index}", f"用户指定{index}")
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(add, range(12)))
        self.assertEqual(self.service.status(self.cid)["active_focus"], 12)

    def test_mode_changes_keep_records_and_validate_scale(self):
        self.service.add_focus(self.cid, "队列", "用户指定")
        self.service.set_mode(self.cid, "pass", 150, 90)
        self.service.set_mode(self.cid, "high")
        status = self.service.status(self.cid)
        self.assertEqual(status["course"]["passing_score"], 90)
        self.assertEqual(status["active_focus"], 1)
        for maximum, target in ((100, 101), (0, 0), (100, float("nan"))):
            with self.assertRaises(DataError):
                self.service.set_mode(self.cid, "pass", maximum, target)

    def test_audio_is_pending_and_not_fake_transcribed(self):
        audio = self.root / "recording.wav"
        audio.write_bytes(b"synthetic audio fixture, not a valid recording")
        result = self.service.import_material(self.cid, audio, "audio", auto_process=False)
        self.assertEqual(result["source"]["status"], "pending")
        self.assertEqual(result["source"]["segment_count"], 0)
        with self.assertRaises(DataError):
            self.service.save_summary(self.cid, result["lecture_id"], "Unfounded invented summary")

    def test_corrupt_state_is_reported_not_cleared(self):
        path = self.service.store.course_dir(self.cid) / "state.json"
        path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(DataError):
            self.service.status(self.cid)
        self.assertEqual(path.read_text(encoding="utf-8"), "{broken")

    def test_no_selected_mode_does_not_start_review(self):
        cid = self.service.store.create("概率论")["course"]["id"]
        with self.assertRaises(DataError):
            self.service.export(cid)

    def test_invalid_saved_schema_is_reported_without_overwrite(self):
        path = self.service.store.course_dir(self.cid) / "state.json"
        state = self.service.store.read(self.cid)
        state["course"] = None
        raw = json.dumps(state)
        path.write_text(raw, encoding="utf-8")
        with self.assertRaises(DataError):
            self.service.status(self.cid)
        self.assertEqual(path.read_text(encoding="utf-8"), raw)

    def test_import_returns_metadata_instead_of_entire_long_transcript(self):
        text = "\n".join(f"第{i}段课堂内容" for i in range(2000))
        result = self.service.import_material(self.cid, self.material("long.txt", text), "transcript")
        self.assertNotIn("segments", result["source"])
        self.assertNotIn("pages", result["source"])
        self.assertEqual(result["source"]["segment_count"], 2000)
        first = self.service.source_slice(self.cid, result["source"]["id"], 0, 30)
        second = self.service.source_slice(self.cid, result["source"]["id"], first["next_start"], 30)
        self.assertEqual(second["material_data"][0]["text"], "第30段课堂内容")


if __name__ == "__main__":
    unittest.main()
