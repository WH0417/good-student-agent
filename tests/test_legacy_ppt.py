import io
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from good_student.legacy_ppt import read_legacy_ppt
from good_student.knowledge import build_knowledge


def record(kind, content, options=0):
    return struct.pack("<HHI", options, kind, len(content)) + content


class LegacyPowerPointTest(unittest.TestCase):
    def fixture(self):
        old = record(1000, record(4000, "过期的旧内容".encode("utf-16-le")), 15)
        slide_list = record(1011, struct.pack("<I", 2)) + record(4000, "第8章 数据查询".encode("utf-16-le"))
        current = record(1000, record(4080, slide_list, 15), 15)
        shape = record(1006, record(4008, b"SELECT Sname FROM Student"), 15)
        current_offset = len(old)
        slide_offset = current_offset + len(current)
        directory_offset = slide_offset + len(shape)
        directory = record(6002, struct.pack("<III", 1 | (2 << 20), current_offset, slide_offset))
        edit_offset = directory_offset + len(directory)
        edit_payload = bytearray(28)
        struct.pack_into("<III", edit_payload, 8, 0, directory_offset, 1)
        data = old + current + shape + directory + record(4085, edit_payload)
        user = b"\0" * 16 + struct.pack("<I", edit_offset)
        return data, user

    def test_current_edit_unicode_and_shape_text_are_preserved(self):
        data, user = self.fixture()
        class Package:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def openstream(self, name): return io.BytesIO(user if name == "Current User" else data)
        fake_module = SimpleNamespace(OleFileIO=lambda path: Package())
        with patch.dict(sys.modules, {"olefile": fake_module}):
            pages = read_legacy_ppt("synthetic.ppt")
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["number"], 1)
        self.assertIn("数据查询", pages[0]["text"])
        self.assertIn("SELECT Sname", pages[0]["text"])
        self.assertNotIn("过期", pages[0]["text"])

    def test_long_body_reference_is_not_promoted_to_chapter_title(self):
        paragraph = "第6章中已经介绍了，" + "主键不能为空也不能重复。" * 20
        topics = build_knowledge({"s": {"filename": "约束.ppt", "status": "partial", "location_type": "slide",
                                       "pages": [{"number": 1, "text": paragraph}]}}, {})
        self.assertEqual([topic["title"] for topic in topics.values()], ["约束"])

    def test_legacy_slide_title_is_used_instead_of_repeated_filename(self):
        topics = build_knowledge({"s": {"filename": "数据查询.ppt", "status": "partial", "location_type": "slide",
                                       "pages": [{"number": 3, "text": "8.1 查询语句\nSELECT ..."}]}}, {})
        topic = next(iter(topics.values()))
        self.assertEqual(topic["title"], "8.1 查询语句")
        self.assertEqual(topic["source_refs"][0]["locator"]["slide"], 3)


if __name__ == "__main__":
    unittest.main()
