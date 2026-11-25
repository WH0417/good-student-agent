"""Local OCR adapters with page-level evidence and honest partial results.

RapidOCR usage follows its official API (``RapidOCR()``, ``txts/scores/boxes``):
https://rapidai.github.io/RapidOCRDocs/main/install_usage/rapidocr/usage/
No OCR dependency is imported until a document actually needs it.
"""
from __future__ import annotations

from contextlib import contextmanager
import math
import os
from pathlib import Path
import re


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}
LOW_CONFIDENCE = 0.75


def _sequence(value):
    if value is None:
        return []
    if hasattr(value, "tolist"):
        value = value.tolist()
    return list(value)


def _box(value):
    if value is None:
        return None
    try:
        return [[float(x), float(y)] for x, y in _sequence(value)]
    except (TypeError, ValueError):
        return None


def _score(value):
    try:
        score = float(value)
        return score if math.isfinite(score) and 0 <= score <= 1 else None
    except (TypeError, ValueError):
        return None


def normalize_result(result):
    """Accept current RapidOCR dataclasses and the older ONNX tuple format."""
    if result is None:
        return []
    if isinstance(result, dict) or hasattr(result, "txts"):
        get = result.get if isinstance(result, dict) else lambda key, default=None: getattr(result, key, default)
        texts = _sequence(get("txts"))
        scores = _sequence(get("scores"))
        boxes = _sequence(get("boxes"))
        rows = [(boxes[i] if i < len(boxes) else None, text,
                 scores[i] if i < len(scores) else None) for i, text in enumerate(texts)]
    else:
        # rapidocr_onnxruntime: ( [[box, text, score], ...], elapsed ).
        rows = result[0] if isinstance(result, tuple) and len(result) == 2 else result
        rows = _sequence(rows)
    lines = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        box, text = row[0], row[1]
        if not isinstance(text, str) or not text.strip():
            continue
        lines.append({"text": text.strip(), "confidence": _score(row[2]) if len(row) > 2 else None,
                      "box": _box(box)})
    return lines


def _default_engine(work_dir):
    from rapidocr import RapidOCR
    configured = os.environ.get("GOOD_STUDENT_OCR_MODELS_DIR")
    models = Path(configured).expanduser().resolve() if configured else (work_dir / "models").resolve()
    if not configured and not models.is_relative_to(work_dir):
        raise ValueError("OCR 模型目录超出指定工作目录")
    return RapidOCR(params={"Det.model_dir": str(models), "Cls.model_dir": str(models),
                            "Rec.model_dir": str(models)})


def _open_pdf(path):
    import pypdfium2
    return pypdfium2.PdfDocument(str(path))


@contextmanager
def _image_pages(path):
    from PIL import Image, ImageSequence
    image = Image.open(path)
    try:
        yield ImageSequence.Iterator(image)
    finally:
        image.close()


def _close(value):
    close = getattr(value, "close", None)
    if close:
        close()


def _text_from_pdf_page(page):
    text_page = page.get_textpage()
    try:
        return text_page.get_text_range() or ""
    finally:
        _close(text_page)


def _usable_native_text(text):
    # Page numbers or a repeated footer are not evidence that a scan was read.
    meaningful = re.sub(r"\s|[^\w\u3400-\u9fff]", "", text)
    chinese = re.findall(r"[\u3400-\u9fff]", meaningful)
    return len(chinese) >= 8 or (len(meaningful) >= 24 and bool(re.search(r"[A-Za-z]", meaningful)))


class ProgressCallbackError(ValueError):
    """A reporting failure must not be mistaken for a broken document page."""


def process_document(path: Path, work_dir: Path, *, engine=None, on_progress=None) -> dict:
    """Read an image or PDF, retaining actual page numbers and OCR evidence.

    ``engine`` may be an already-configured callable. The optional local backend
    is loaded lazily once, only for pages without useful native text. No output
    path is derived from document content or its file name. Model cache files
    created by RapidOCR remain under ``work_dir/models``.
    """
    path, work_dir = Path(path).expanduser().resolve(), Path(work_dir).expanduser().resolve()
    result = {"pages": [], "segments": [], "page_results": [], "status": "failed", "warnings": [],
              "processing": {"engine": "rapidocr", "local": True, "layout": "recognized_line_order"}}
    if not path.is_file():
        result["warnings"].append("扫描文件不存在")
        return result
    if path.suffix.lower() not in IMAGE_SUFFIXES | {".pdf"}:
        result["warnings"].append("OCR 支持图片和 PDF")
        return result
    work_dir.mkdir(parents=True, exist_ok=True)
    import hashlib
    import json
    from .storage import atomic_json
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    fingerprint = digest.hexdigest()
    cache = work_dir / 'pages'
    cache.mkdir(exist_ok=True)
    total_pages = 1
    use_cache = engine is None
    def report_progress(number):
        if on_progress:
            try:
                on_progress({'completed': len(result['page_results']), 'total': total_pages,
                             'unit': '页', 'phase': '资料识别', 'page': number})
            except Exception as exc:
                raise ProgressCallbackError('进度上报中断，已完成页仍保留：' + str(exc)) from exc
    holder = {"engine": engine, "error": None}

    def recognize(image):
        if holder["error"]:
            raise holder["error"]
        if holder["engine"] is None:
            try:
                holder["engine"] = _default_engine(work_dir)
            except Exception as exc:
                holder["error"] = exc
                raise
        return normalize_result(holder["engine"](image))

    def save_page(number, native="", image=None, render=None, input_error=None):
        checkpoint = cache / f'{number:05d}.json'
        saved = None
        if use_cache and checkpoint.exists():
            try:
                saved = json.loads(checkpoint.read_text(encoding='utf-8'))
            except (OSError, ValueError, KeyError, TypeError):
                pass
        if (isinstance(saved, dict) and saved.get('sha256') == fingerprint
                and saved.get('record', {}).get('status') == 'complete'
                and isinstance(saved.get('page'), dict) and isinstance(saved.get('segments'), list)):
            result['pages'].append(saved['page'])
            result['page_results'].append(saved['record'])
            result['segments'].extend(saved['segments'])
            report_progress(number)
            return
        segment_start = len(result['segments'])
        record = {"page": number, "status": "complete", "method": "native_text", "warnings": []}
        lines = []
        if input_error:
            record.update(status="failed", method="unreadable")
            record["warnings"].append(str(input_error))
        elif _usable_native_text(native):
            lines = [{"text": line.strip(), "confidence": None, "box": None}
                     for line in native.splitlines() if line.strip()]
        else:
            record["method"] = "ocr"
            bitmap, rendered = None, None
            try:
                if render is not None:
                    bitmap = render()
                    rendered = bitmap.to_pil()
                    image = rendered
                lines = recognize(image)
                if not lines:
                    record["status"] = "partial"
                    record["warnings"].append("本页未识别到文字，需要查看原图")
                elif any(line["confidence"] is None or line["confidence"] < LOW_CONFIDENCE for line in lines):
                    record["status"] = "partial"
                    record["warnings"].append("本页存在低置信或未返回置信度的文字，需核对原图")
            except (ImportError, ModuleNotFoundError) as exc:
                record["status"] = "pending"
                record["warnings"].append("OCR 依赖未就绪：" + str(exc))
            except Exception as exc:
                record["status"] = "failed"
                record["warnings"].append("本页 OCR 未完成：" + str(exc))
            finally:
                _close(rendered)
                _close(bitmap)
            # Preserve usable original text even when a footer-only scan needs
            # additional OCR. Never turn that fallback into a complete scan.
            if not lines and native.strip():
                lines = [{"text": line.strip(), "confidence": None, "box": None}
                         for line in native.splitlines() if line.strip()]
                record["method"] = "partial_native_text"
                record["status"] = "partial"
        result["pages"].append({"number": number, "text": "\n".join(line["text"] for line in lines)})
        record["line_count"] = len(lines)
        result["page_results"].append(record)
        for index, line in enumerate(lines, 1):
            locator = {"page": number, "paragraph": index}
            if path.suffix.lower() != ".pdf":
                locator["image"] = path.name
            if line["box"] is not None:
                locator["box"] = line["box"]
            if line["confidence"] is not None:
                locator["ocr_confidence"] = line["confidence"]
            result["segments"].append({"text": line["text"], "locator": locator, "speaker": "unknown"})
        result["warnings"].extend(f"第 {number} 页：{warning}" for warning in record["warnings"])
        atomic_json(checkpoint, {'sha256': fingerprint, 'page': result['pages'][-1],
                                 'record': record, 'segments': result['segments'][segment_start:]})
        report_progress(number)

    if path.suffix.lower() == ".pdf":
        document = None
        try:
            document = _open_pdf(path)
            total_pages = len(document)
            for index in range(len(document)):
                page = None
                try:
                    page = document[index]
                    native = _text_from_pdf_page(page)
                    save_page(index + 1, native=native, render=lambda: page.render(scale=2.0))
                except ProgressCallbackError:
                    raise
                except Exception as exc:
                    save_page(index + 1, input_error="本页读取失败：" + str(exc))
                finally:
                    _close(page)
        except ProgressCallbackError:
            raise
        except (ImportError, ModuleNotFoundError) as exc:
            result["status"] = "pending"
            result["warnings"].append("PDF 读取依赖未就绪：" + str(exc))
            return result
        except Exception as exc:
            result["warnings"].append("PDF 无法读取：" + str(exc))
            return result
        finally:
            _close(document)
    else:
        try:
            with _image_pages(path) as frames:
                for number, frame in enumerate(frames, 1):
                    image = frame.copy()
                    try:
                        save_page(number, image=image)
                    finally:
                        _close(image)
        except ProgressCallbackError:
            raise
        except (ImportError, ModuleNotFoundError) as exc:
            result["status"] = "pending"
            result["warnings"].append("图片读取依赖未就绪：" + str(exc))
            return result
        except Exception as exc:
            result["warnings"].append("图片读取失败：" + str(exc))
            # A damaged later TIFF frame must not erase prior readable pages.
            if not result["page_results"]:
                return result
            result["page_results"].append({"page": len(result["page_results"]) + 1,
                                            "status": "failed", "method": "unreadable", "warnings": [str(exc)]})

    records = result["page_results"]
    has_text = any(page["text"].strip() for page in result["pages"])
    if records and all(page["status"] == "complete" for page in records) and has_text:
        result["status"] = "complete"
    elif has_text or any(page["status"] == "partial" for page in records):
        result["status"] = "partial"
    elif any(page["status"] == "pending" for page in records):
        result["status"] = "pending"
    else:
        result["status"] = "failed"
    result["processing"].update(page_count=len(records),
                                ocr_pages=[p["page"] for p in records if p["method"] == "ocr"],
                                complete_pages=[p["page"] for p in records if p["status"] == "complete"])
    return result
