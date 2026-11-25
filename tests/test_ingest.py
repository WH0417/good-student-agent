from pathlib import Path
import tempfile
import unittest
import zipfile

from good_student.ingest import prepare, split_segments
from good_student.storage import DataError


class InputTest(unittest.TestCase):
    def test_real_timestamps_and_original_quoted_words_survive(self):
        records = split_segments("1\n00:00:03,000 --> 00:00:05,000\n老师：队列是重点。\n", ".srt")
        self.assertEqual(records[0]["locator"]["start"], 3)
        self.assertEqual(records[0]["text"], "老师：队列是重点。")
        self.assertEqual(records[0]["speaker"], "teacher")

    def test_plain_summary_never_acquires_invented_time(self):
        record = split_segments("豆包纪要：老师强调第三章很重要。")[0]
        self.assertNotIn("timestamp", record["locator"])
        self.assertEqual(record["locator"]["paragraph"], 1)

    def test_invalid_subtitles_are_refused_without_repair(self):
        for text in ("1\n00:00:09,000 --> 00:00:02,000\nText", "1\nno timing\nText"):
            with self.assertRaises(DataError):
                split_segments(text, ".srt")

    def test_pptx_adapter_preserves_slide_number(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "第二章.pptx"
            with zipfile.ZipFile(path, "w") as package:
                for number, text in enumerate(("第二章 栈", "后进先出"), 1):
                    package.writestr(f"ppt/slides/slide{number}.xml",
                        '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                        f'<p:cSld><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:cSld></p:sld>')
            record, _ = prepare(path, "material")
            self.assertEqual([page["number"] for page in record["pages"]], [1, 2])
            self.assertIn("后进先出", record["pages"][1]["text"])


if __name__ == "__main__":
    unittest.main()
