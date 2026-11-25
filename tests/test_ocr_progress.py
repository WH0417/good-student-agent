"""Page-level OCR progress, immutable-input caching and resumable interruptions.

The PDF objects and recognition engine are local fakes; these tests never load
PDF/OCR dependencies, download models, or open windows.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from good_student.ocr import process_document
from tests.test_ocr import BOX, FakePage, FakePdf


class TaggedPage(FakePage):
    """Attach the real page number to a lightweight image passed to the engine."""

    def __init__(self, number, text="", broken=False):
        super().__init__(text, broken)
        self.number = number
        self.render_count = 0

    def render(self, scale):
        self.render_count += 1
        image = SimpleNamespace(page_number=self.number, close=lambda: None)
        return SimpleNamespace(to_pil=lambda: image, close=lambda: None)


def fake_engine(version="v1", low_confidence_pages=()):
    def recognize(image):
        confidence = 0.35 if image.page_number in low_confidence_pages else 0.98
        return ([[BOX, f"{version} 第 {image.page_number} 页的知识内容", confidence]], None)
    return Mock(side_effect=recognize)


class OcrProgressTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="good-student-ocr-progress-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.pdf = self.root / "扫描课程.pdf"
        self.pdf.write_bytes(b"fake immutable PDF input version one")
        self.work = self.root / "ocr-work"

    def checkpoint(self, number):
        return self.work / "pages" / f"{number:05d}.json"

    def cached(self, number):
        return json.loads(self.checkpoint(number).read_text(encoding="utf-8"))

    def run_pdf(self, pages, *, engine=None, callback=None):
        # Patch the lazy default factory rather than supplying engine=, so the
        # production engine=None cache path is exercised.
        engine = engine if engine is not None else fake_engine()
        with patch("good_student.ocr._open_pdf", return_value=FakePdf(pages)), patch(
                "good_student.ocr._default_engine", return_value=engine) as factory:
            result = process_document(self.pdf, self.work, on_progress=callback)
        return result, engine, factory

    def assert_progress(self, events, pages):
        self.assertEqual([(event["completed"], event["total"], event["page"])
                          for event in events], [(number, pages, number) for number in range(1, len(events) + 1)])
        self.assertTrue(all(event["unit"] == "页" and event["phase"] == "资料识别" for event in events))

    def test_every_native_or_ocr_page_reports_original_number_and_document_total(self):
        events = []
        pages = [TaggedPage(1, "这一页包含足够长的原生中文课程内容，无需调用识别引擎。"),
                 TaggedPage(2), TaggedPage(3)]
        result, engine, _ = self.run_pdf(pages, callback=events.append)
        self.assertEqual(result["status"], "complete")
        self.assertEqual([page["number"] for page in result["pages"]], [1, 2, 3])
        self.assert_progress(events, 3)
        self.assertEqual(engine.call_count, 2)
        self.assertEqual([page.render_count for page in pages], [0, 1, 1])

    def test_complete_pages_are_reused_without_loading_or_calling_ocr_again(self):
        first, first_engine, _ = self.run_pdf([TaggedPage(1), TaggedPage(2), TaggedPage(3)])
        self.assertEqual(first_engine.call_count, 3)
        frozen = [self.checkpoint(number).read_bytes() for number in (1, 2, 3)]
        events = []
        pages = [TaggedPage(1), TaggedPage(2), TaggedPage(3)]
        second, engine, factory = self.run_pdf(pages, callback=events.append)
        self.assertEqual(second["status"], "complete")
        self.assertEqual(second["pages"], first["pages"])
        self.assertEqual(second["segments"], first["segments"])
        self.assertEqual(second["page_results"], first["page_results"])
        factory.assert_not_called()
        engine.assert_not_called()
        self.assertEqual([page.render_count for page in pages], [0, 0, 0])
        self.assertEqual([self.checkpoint(number).read_bytes() for number in (1, 2, 3)], frozen)
        self.assert_progress(events, 3)

    def test_changed_source_bytes_invalidate_all_old_page_checkpoints(self):
        first, _, _ = self.run_pdf([TaggedPage(1), TaggedPage(2)])
        old_digest = self.cached(1)["sha256"]
        self.pdf.write_bytes(b"the same source path now contains different PDF bytes")
        updated_engine = fake_engine("v2")
        events = []
        second, engine, _ = self.run_pdf([TaggedPage(1), TaggedPage(2)], engine=updated_engine, callback=events.append)
        self.assertEqual(engine.call_count, 2)
        self.assertEqual(second["status"], "complete")
        self.assertNotEqual(second["pages"], first["pages"])
        expected = hashlib.sha256(self.pdf.read_bytes()).hexdigest()
        self.assertNotEqual(old_digest, expected)
        self.assertTrue(all(self.cached(number)["sha256"] == expected for number in (1, 2)))
        self.assertTrue(all(page["text"].startswith("v2") for page in second["pages"]))
        self.assert_progress(events, 2)

    def test_failed_page_reports_progress_and_retries_without_redoing_complete_neighbors(self):
        events = []
        first, engine, _ = self.run_pdf([TaggedPage(1), TaggedPage(2, broken=True), TaggedPage(3)], callback=events.append)
        self.assertEqual(first["status"], "partial")
        self.assertEqual(engine.call_count, 2)
        self.assertEqual([page["status"] for page in first["page_results"]], ["complete", "failed", "complete"])
        self.assert_progress(events, 3)
        self.assertEqual(self.cached(2)["record"]["status"], "failed")
        pages = [TaggedPage(1), TaggedPage(2), TaggedPage(3)]
        resumed_events = []
        resumed, resumed_engine, _ = self.run_pdf(pages, callback=resumed_events.append)
        self.assertEqual(resumed["status"], "complete")
        self.assertEqual(resumed_engine.call_count, 1)
        self.assertEqual(resumed_engine.call_args.args[0].page_number, 2)
        self.assertEqual([page.render_count for page in pages], [0, 1, 0])
        self.assertEqual([page["number"] for page in resumed["pages"]], [1, 2, 3])
        self.assert_progress(resumed_events, 3)

    def test_low_confidence_page_is_not_reused_as_a_complete_checkpoint(self):
        first, _, _ = self.run_pdf([TaggedPage(1), TaggedPage(2)], engine=fake_engine(low_confidence_pages={2}))
        self.assertEqual(first["status"], "partial")
        self.assertEqual(self.cached(2)["record"]["status"], "partial")
        result, engine, _ = self.run_pdf([TaggedPage(1), TaggedPage(2)])
        self.assertEqual(result["status"], "complete")
        self.assertEqual(engine.call_count, 1)
        self.assertEqual(engine.call_args.args[0].page_number, 2)

    def _interrupt_after_second_page(self, *, cached, exception_type):
        if cached:
            self.run_pdf([TaggedPage(1), TaggedPage(2), TaggedPage(3)])
        events = []

        def stop(event):
            events.append(dict(event))
            if event["page"] == 2:
                raise exception_type("test-requested interruption after a completed page")

        pages = [TaggedPage(1), TaggedPage(2), TaggedPage(3)]
        engine = fake_engine()
        result = None
        # Either an explicit abort exception or an honest partial/failed result
        # is acceptable. Treating a callback error as a new PDF page is not.
        try:
            result, engine, _ = self.run_pdf(pages, engine=engine, callback=stop)
        except exception_type:
            pass
        except ValueError as exc:
            from good_student.ocr import ProgressCallbackError
            self.assertIsInstance(exc, ProgressCallbackError)
            self.assertIsInstance(exc.__cause__, exception_type)
        if result is not None:
            self.assertNotEqual(result["status"], "complete")
            self.assertEqual([page["number"] for page in result["pages"]], [1, 2],
                             "A callback interruption must not append the same page twice")
        self.assertEqual([(event["completed"], event["total"], event["page"]) for event in events],
                         [(1, 3, 1), (2, 3, 2)], "A callback error must stop before the next page, not invoke the callback twice")
        self.assertEqual(engine.call_count, 0 if cached else 2)
        self.assertEqual(pages[2].render_count, 0)
        self.assertEqual([self.cached(number)["record"]["status"] for number in (1, 2)], ["complete", "complete"])
        resumed_events = []
        resumed, resumed_engine, _ = self.run_pdf([TaggedPage(1), TaggedPage(2), TaggedPage(3)], callback=resumed_events.append)
        self.assertEqual(resumed["status"], "complete")
        self.assertEqual(resumed_engine.call_count, 0 if cached else 1)
        self.assertEqual([page["number"] for page in resumed["pages"]], [1, 2, 3])
        self.assert_progress(resumed_events, 3)

    def test_callback_interruption_keeps_newly_completed_pages_resumable(self):
        self._interrupt_after_second_page(cached=False, exception_type=RuntimeError)

    def test_callback_value_error_on_reused_page_cannot_invalidate_or_overwrite_cache(self):
        self._interrupt_after_second_page(cached=True, exception_type=ValueError)


if __name__ == "__main__":
    unittest.main()
