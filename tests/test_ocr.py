from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from good_student.ocr import normalize_result, process_document


BOX = [[0, 0], [120, 0], [120, 30], [0, 30]]


class FakeImage:
    def copy(self):
        return FakeImage()

    def close(self):
        pass


class FakePage:
    def __init__(self, text="", broken=False):
        self.text = text
        self.broken = broken

    def get_textpage(self):
        if self.broken:
            raise RuntimeError("damaged page")
        return SimpleNamespace(get_text_range=lambda: self.text, close=lambda: None)

    def render(self, scale):
        return SimpleNamespace(to_pil=lambda: FakeImage(), close=lambda: None)

    def close(self):
        pass


class FakePdf(list):
    def close(self):
        pass


@contextmanager
def fake_frames(_):
    yield [FakeImage()]


class OcrTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.image = self.root / "扫描.png"
        self.image.write_bytes(b"fixture opened by the injected image loader")
        self.pdf = self.root / "扫描.pdf"
        self.pdf.write_bytes(b"fixture opened by the injected PDF loader")

    def image_run(self, engine):
        with patch("good_student.ocr._image_pages", fake_frames):
            return process_document(self.image, self.root / "work", engine=engine)

    def test_current_output_preserves_text_box_and_confidence(self):
        result = self.image_run(lambda _: SimpleNamespace(txts=("二叉树的遍历",), scores=(0.98,), boxes=[BOX]))
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["pages"][0]["text"], "二叉树的遍历")
        self.assertEqual(result["segments"][0]["speaker"], "unknown")
        locator = result["segments"][0]["locator"]
        self.assertEqual(locator["image"], "扫描.png")
        self.assertEqual(locator["ocr_confidence"], 0.98)
        self.assertEqual(locator["box"], BOX)

    def test_older_tuple_output_is_supported(self):
        lines = normalize_result(([[BOX, "队列", 0.93]], [0.1, 0.2]))
        self.assertEqual(lines[0]["text"], "队列")
        self.assertEqual(lines[0]["confidence"], 0.93)

    def test_low_confidence_is_partial_and_never_rewritten(self):
        result = self.image_run(lambda _: ([[BOX, "疑似公式 x=7", 0.42]], None))
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["pages"][0]["text"], "疑似公式 x=7")
        self.assertTrue(result["warnings"])

    def test_empty_result_does_not_claim_completed_recognition(self):
        result = self.image_run(lambda _: (None, None))
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["segments"], [])

    def test_missing_backend_is_pending_not_fake_text(self):
        with patch("good_student.ocr._image_pages", fake_frames), patch(
                "good_student.ocr._default_engine", side_effect=ModuleNotFoundError("rapidocr")):
            result = process_document(self.image, self.root / "work")
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["segments"], [])

    def test_mixed_pdf_reads_native_and_scanned_pages_in_original_order(self):
        document = FakePdf([FakePage("第一章课程知识已经包含完整的原生文字内容"), FakePage("")])
        calls = []
        def recognize(image):
            calls.append(image)
            return ([[BOX, "第二页扫描出的队列知识", 0.96]], None)
        with patch("good_student.ocr._open_pdf", return_value=document):
            result = process_document(self.pdf, self.root / "work", engine=recognize)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(calls), 1)
        self.assertEqual([p["number"] for p in result["pages"]], [1, 2])
        self.assertEqual(result["page_results"][0]["method"], "native_text")
        self.assertEqual(result["page_results"][1]["method"], "ocr")
        self.assertEqual(result["segments"][-1]["locator"]["page"], 2)

    def test_later_failed_pdf_page_does_not_make_whole_document_complete(self):
        document = FakePdf([FakePage("第一章课程知识已经包含完整的原生文字内容"), FakePage(broken=True)])
        with patch("good_student.ocr._open_pdf", return_value=document):
            result = process_document(self.pdf, self.root / "work", engine=lambda _: None)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["pages"][1]["number"], 2)
        self.assertEqual(result["pages"][1]["text"], "")
        self.assertEqual(result["page_results"][1]["status"], "failed")

    def test_short_native_footer_is_not_treated_as_complete_scan(self):
        with patch("good_student.ocr._open_pdf", return_value=FakePdf([FakePage("Page 1")])):
            result = process_document(self.pdf, self.root / "work", engine=lambda _: (None, None))
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["pages"][0]["text"], "Page 1")

    def test_missing_pdf_dependency_is_pending(self):
        with patch("good_student.ocr._open_pdf", side_effect=ModuleNotFoundError("pypdfium2")):
            result = process_document(self.pdf, self.root / "work")
        self.assertEqual(result["status"], "pending")
        self.assertFalse(result["pages"])


if __name__ == "__main__":
    unittest.main()
