"""Maintainer integration check against actual local ASR and OCR models."""
from pathlib import Path
import json
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
RUN = ROOT / "audit" / "media-smoke"
MODELS = ROOT / ".runtime" / "models"
os.environ.setdefault("GOOD_STUDENT_MODELS_DIR", str(MODELS / "asr"))
os.environ.setdefault("GOOD_STUDENT_OCR_MODELS_DIR", str(MODELS / "ocr"))

from good_student.audio import transcribe_audio
from good_student.ocr import process_document


def main():
    from PIL import Image, ImageDraw, ImageFont
    RUN.mkdir(parents=True, exist_ok=True)
    font_candidates = [Path("C:/Windows/Fonts/msyh.ttc"), Path("C:/Windows/Fonts/simhei.ttf")]
    font_file = next((path for path in font_candidates if path.is_file()), None)
    if font_file is None:
        raise RuntimeError("本地真实识别验证缺少中文字体")
    image = Image.new("RGB", (1500, 550), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(font_file), 54)
    expected = ["队列遵循先进先出。", "二叉树的遍历是复习重点。", "考试卷面及格线为六十分。"]
    for index, text in enumerate(expected):
        draw.text((70, 70 + index * 130), text, font=font, fill="black")
    png = RUN / "chinese-scan.png"
    pdf = RUN / "chinese-scan.pdf"
    image.save(png)
    image.save(pdf, "PDF", resolution=150)
    scanned_image = process_document(png, RUN / "ocr-image")
    scanned_pdf = process_document(pdf, RUN / "ocr-pdf")
    speech = transcribe_audio(RUN / "jfk.flac", RUN / "asr", model="small", language="en")
    result = {"actual_models": True, "image": scanned_image, "pdf": scanned_pdf, "audio": speech}
    (RUN / "actual-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: {"status": value["status"], "text": "\n".join(page["text"] for page in value["pages"]),
                           "warnings": value["warnings"]}
                      for key, value in (("image", scanned_image), ("pdf", scanned_pdf), ("audio", speech))},
                     ensure_ascii=False, indent=2))
    assert scanned_image["segments"] and "队列" in scanned_image["pages"][0]["text"]
    assert scanned_pdf["segments"] and "二叉树" in scanned_pdf["pages"][0]["text"]
    assert speech["status"] == "complete" and "country" in speech["pages"][0]["text"].lower()
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
