from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from good_student.service import StudentService
from good_student.storage import DataError


def recognition(text, status="complete", *, engine="test"):
    return {"status": status, "pages": [{"number": 1, "text": text}],
            "segments": [{"text": text, "speaker": "unknown",
                          "locator": {"paragraph": 1, "timestamp": "00:01", "start": 1, "end": 3}}],
            "warnings": [] if status == "complete" else ["后续部分未识别"],
            "processing": {"engine": engine}}


class MediaFlowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.service = StudentService(self.root / "data")
        self.course = self.service.store.create("数据结构", "pass")["course"]["id"]

    def binary(self, name):
        path = self.root / name
        path.write_bytes(b"Synthetic adapter input")
        return path

    def test_import_audio_automatically_transcribes_and_keeps_evidence(self):
        result = recognition("二叉树一定会考。")
        with patch("good_student.audio.transcribe_audio", return_value=result) as adapter:
            imported = self.service.import_material(self.course, self.binary("lecture.wav"), "audio", speaker="teacher")
        adapter.assert_called_once()
        self.assertEqual(imported["source"]["status"], "complete")
        self.assertEqual(imported["new_focus"], 1)
        state = self.service.store.read(self.course)
        focus = next(iter(state["focus"].values()))
        self.assertEqual(focus["evidence"][0]["source_id"], imported["source"]["id"])
        self.assertEqual(focus["evidence"][0]["locator"]["timestamp"], "00:01")
        self.assertEqual(state["lectures"][imported["lecture_id"]]["status"], "complete")

    def test_unknown_audio_speaker_is_not_automatically_teacher(self):
        with patch("good_student.audio.transcribe_audio", return_value=recognition("队列是重点。")):
            self.service.import_material(self.course, self.binary("unknown.m4a"), "audio")
        self.assertEqual(self.service.status(self.course)["active_focus"], 0)

    def test_scanned_exam_becomes_source_backed_question_bank(self):
        scanned = recognition("1. What is one plus one?\nAnswer: 2")
        with patch("good_student.ocr.process_document", return_value=scanned) as adapter:
            result = self.service.import_material(self.course, self.binary("scan.png"), "exam")
        adapter.assert_called_once()
        self.assertEqual(result["source"]["status"], "complete")
        state = self.service.store.read(self.course)
        self.assertEqual(len(state["questions"]), 1)
        question = next(iter(state["questions"].values()))
        self.assertEqual(question["source_id"], result["source"]["id"])
        self.assertEqual(self.service.answer(self.course, question["id"], "2.4")["result"], "wrong")

    def test_partial_media_retry_does_not_duplicate_focus(self):
        audio = self.binary("partial.wav")
        with patch("good_student.audio.transcribe_audio", return_value=recognition("队列是重点。", "partial")):
            first = self.service.import_material(self.course, audio, "audio", speaker="teacher")
        self.assertEqual(first["source"]["status"], "partial")
        with patch("good_student.audio.transcribe_audio", return_value=recognition("队列是重点。")):
            self.service.process_source(self.course, first["source"]["id"])
        state = self.service.store.read(self.course)
        self.assertEqual(len(state["focus"]), 1)
        self.assertEqual(len(next(iter(state["focus"].values()))["evidence"]), 1)
        self.assertEqual(state["sources"][first["source"]["id"]]["status"], "complete")

    def test_retry_cannot_read_other_courses_source(self):
        audio = self.binary("pending.wav")
        first = self.service.import_material(self.course, audio, "audio", auto_process=False)
        other = self.service.store.create("英语", "high")["course"]["id"]
        with self.assertRaises(DataError):
            self.service.process_source(other, first["source"]["id"])

    def test_existing_v01_audio_can_be_reprocessed_without_reupload(self):
        audio = self.binary("old.wav")
        first = self.service.import_material(self.course, audio, "audio", auto_process=False)
        source_id = first["source"]["id"]
        with self.service.store.transaction(self.course) as state:
            state["sources"][source_id].pop("media_task")
        with patch("good_student.audio.transcribe_audio", return_value=recognition("队列是重点。")):
            result = self.service.process_source(self.course, source_id)
        self.assertEqual(result["source"]["status"], "complete")
        self.assertEqual(len(self.service.store.read(self.course)["sources"]), 1)


if __name__ == "__main__":
    unittest.main()
