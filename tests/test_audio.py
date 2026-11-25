from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from good_student.audio import transcribe_audio


def segment(start, end, text):
    return SimpleNamespace(start=start, end=end, text=text)


class FakeEngine:
    def __init__(self, parts, duration=12, fail=None):
        self.parts = parts
        self.duration = duration
        self.fail = fail
        self.calls = []

    def transcribe(self, path, **options):
        self.calls.append((path, options))

        def stream():
            yield from self.parts
            if self.fail:
                raise self.fail

        return stream(), SimpleNamespace(duration=self.duration, language="zh")


class AudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.recording = self.root / "lecture.wav"
        self.recording.write_bytes(b"synthetic audio bytes for adapter-only tests")
        self.work = self.root / "cache"

    def test_model_segments_keep_real_times_and_unknown_speaker(self):
        engine = FakeEngine([segment(1.25, 3, "队列是考试重点。"), segment(5, 8.5, "接着讲链表。")])
        result = transcribe_audio(self.recording, self.work, transcriber=engine)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["segments"][0]["locator"]["start"], 1.25)
        self.assertEqual(result["segments"][0]["locator"]["timestamp"], "00:00:01.250")
        self.assertTrue(all(s["speaker"] == "unknown" for s in result["segments"]))
        self.assertEqual(result["segments"][0]["text"], "队列是考试重点。")
        self.assertFalse(engine.calls[0][1]["vad_filter"])

    def test_generator_failure_preserves_partial_and_resumes_from_actual_time(self):
        first = FakeEngine([segment(1, 3, "第一段"), segment(4, 7, "第二段")], fail=RuntimeError("decoder stopped"))
        partial = transcribe_audio(self.recording, self.work, transcriber=first)
        self.assertEqual(partial["status"], "partial")
        self.assertEqual(len(partial["segments"]), 2)
        self.assertEqual(partial["failed_ranges"][0]["start"], 7)
        retry = FakeEngine([segment(7, 10, "第三段")])
        result = transcribe_audio(self.recording, self.work, transcriber=retry)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(retry.calls[0][1]["clip_timestamps"], "7.0")
        self.assertEqual([s["text"] for s in result["segments"]], ["第一段", "第二段", "第三段"])

    def test_completed_cache_is_reused_and_is_bound_to_file_model_language(self):
        engine = FakeEngine([segment(0, 2, "缓存内容")])
        transcribe_audio(self.recording, self.work, model="base", language="zh", transcriber=engine)
        unused = FakeEngine([], fail=RuntimeError("must not run"))
        cached = transcribe_audio(self.recording, self.work, model="base", language="zh", transcriber=unused)
        self.assertTrue(cached["processing"]["cache_hit"])
        self.assertFalse(unused.calls)
        changed_model = FakeEngine([segment(0, 2, "另一个模型")])
        different = transcribe_audio(self.recording, self.work, model="small", language="zh", transcriber=changed_model)
        self.assertFalse(different["processing"]["cache_hit"])
        self.assertTrue(changed_model.calls)
        self.recording.write_bytes(b"different audio content")
        changed_audio = FakeEngine([segment(0, 2, "另一份录音")])
        other = transcribe_audio(self.recording, self.work, model="base", language="zh", transcriber=changed_audio)
        self.assertFalse(other["processing"]["cache_hit"])
        changed_language = FakeEngine([segment(0, 2, "English")])
        result = transcribe_audio(self.recording, self.work, model="base", language="en", transcriber=changed_language)
        self.assertFalse(result["processing"]["cache_hit"])

    def test_invalid_times_cannot_be_saved_as_transcript(self):
        for index, (start, end) in enumerate(((-1, 2), (3, 2), (float("nan"), 2), (0, float("inf")), (True, 2))):
            with self.subTest(start=start, end=end):
                result = transcribe_audio(self.recording, self.root / f"bad-{index}",
                                          transcriber=FakeEngine([segment(start, end, "无效时间")]))
                self.assertEqual(result["status"], "failed")
                self.assertFalse(result["segments"])

    def test_end_beyond_media_and_reverse_order_return_partial_not_complete(self):
        for index, second in enumerate((segment(3, 13, "超出录音"), segment(0, 4, "回退时间"))):
            result = transcribe_audio(self.recording, self.root / f"range-{index}",
                                      transcriber=FakeEngine([segment(1, 2, "有效片段"), second]))
            self.assertEqual(result["status"], "partial")
            self.assertEqual(len(result["segments"]), 1)

    def test_no_speech_is_not_complete(self):
        result = transcribe_audio(self.recording, self.work, transcriber=FakeEngine([segment(0, 2, " ")]))
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["pages"])

    def test_missing_runtime_and_model_failure_are_pending(self):
        with patch("good_student.audio._default_transcriber", side_effect=ImportError):
            result = transcribe_audio(self.recording, self.work)
        self.assertEqual(result["status"], "pending")
        with patch("good_student.audio._default_transcriber", side_effect=RuntimeError("download unavailable")):
            result = transcribe_audio(self.recording, self.work)
        self.assertEqual(result["status"], "pending")
        self.assertFalse(result["segments"])

    def test_startup_failure_after_partial_keeps_existing_quotes(self):
        engine = FakeEngine([segment(0, 3, "保留这段原文")], fail=RuntimeError("stopped"))
        transcribe_audio(self.recording, self.work, transcriber=engine)
        with patch("good_student.audio._default_transcriber", side_effect=ImportError):
            result = transcribe_audio(self.recording, self.work)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["segments"][0]["text"], "保留这段原文")

    def test_bad_cache_does_not_replace_audio_or_raise_false_completion(self):
        engine = FakeEngine([segment(0, 2, "第一遍")])
        transcribe_audio(self.recording, self.work, transcriber=engine)
        cache = next(self.work.glob("asr-*.json"))
        cache.write_text("{broken", encoding="utf-8")
        fresh = FakeEngine([segment(0, 2, "重新识别")])
        result = transcribe_audio(self.recording, self.work, transcriber=fresh)
        self.assertFalse(result["processing"]["cache_hit"])
        self.assertEqual(result["segments"][0]["text"], "重新识别")
        self.assertEqual(self.recording.read_bytes(), b"synthetic audio bytes for adapter-only tests")

    def test_empty_or_missing_source_is_not_passed_to_engine(self):
        engine = FakeEngine([segment(0, 2, "不能出现")])
        self.recording.write_bytes(b"")
        result = transcribe_audio(self.recording, self.work, transcriber=engine)
        self.assertEqual(result["status"], "failed")
        self.assertFalse(engine.calls)
        result = transcribe_audio(self.root / "missing.mp3", self.work, transcriber=engine)
        self.assertEqual(result["status"], "failed")


if __name__ == "__main__":
    unittest.main()
