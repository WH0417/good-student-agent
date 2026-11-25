"""Input adapters preserve original text; no synthetic teacher quotes or timings."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re

from .storage import DataError
from .vendor.coach import extract

KINDS = ("transcript", "summary", "material", "course_focus", "scope", "exam", "exercise", "solution", "audio")
AUDIO = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac", ".webm", ".mp4"}
IMAGES = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}
TEXT = {".txt", ".md", ".markdown", ".srt", ".vtt"}


def stable_id(prefix, *parts):
    content = "\0".join(str(part) for part in parts)
    return prefix + hashlib.sha256(content.encode("utf-8")).hexdigest()[:20]


def decode_text(raw):
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise DataError("文本编码无法识别，请另存为 UTF-8 后导入")


def _seconds(value):
    parts = value.replace(",", ".").split(":")
    try:
        if len(parts) == 3:
            hour, minute, second = int(parts[0]), int(parts[1]), float(parts[2])
            if minute >= 60:
                raise ValueError
        elif len(parts) == 2:
            hour, minute, second = 0, int(parts[0]), float(parts[1])
        else:
            raise ValueError
        if min(hour, minute, second) < 0 or second >= 60:
            raise ValueError
        return hour * 3600 + minute * 60 + second
    except ValueError:
        raise DataError(f"无效时间戳：{value}") from None


def _speaker(text):
    match = re.match(r"^\s*(老师|教师|teacher|学生|同学|student)(?:说)?\s*[:：]\s*(.*)$", text, re.I)
    if not match:
        return "unknown"
    return "teacher" if match[1].lower() in ("老师", "教师", "teacher") else "student"


def split_segments(text, suffix=".txt"):
    records = []
    if suffix in (".srt", ".vtt"):
        for block in re.split(r"\r?\n\s*\r?\n", text.strip()):
            lines = block.splitlines()
            timing_index = next((i for i, line in enumerate(lines) if "-->" in line), None)
            if timing_index is None:
                if not block.strip() or block.startswith(("WEBVTT", "NOTE", "STYLE", "REGION")):
                    continue
                raise DataError("字幕中有缺少时间戳的片段；原文件保留，未伪造时间")
            timing = lines[timing_index].strip().split("-->")
            if len(timing) != 2:
                raise DataError("字幕时间戳格式错误")
            begin, end = timing[0].strip(), timing[1].strip().split()[0]
            start_seconds, end_seconds = _seconds(begin), _seconds(end)
            if end_seconds < start_seconds:
                raise DataError("字幕片段结束时间早于开始时间")
            quote = "\n".join(lines[timing_index + 1:]).strip()
            if quote:
                records.append({"text": quote, "speaker": _speaker(quote),
                                "locator": {"paragraph": len(records) + 1, "timestamp": begin,
                                            "start": start_seconds, "end": end_seconds}})
        if not records:
            raise DataError("没有识别到有效字幕片段")
        return records
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        quote = line.strip()
        locator = {"paragraph": len(records) + 1, "line": line_number}
        match = re.match(r"^\s*\[?(\d{1,3}:\d{2}(?::\d{2})?(?:[.,]\d{1,3})?)\]?\s+(.+)$", quote)
        if match:
            _seconds(match[1])
            locator["timestamp"] = match[1]
            quote = match[2]
        records.append({"text": quote, "locator": locator, "speaker": _speaker(quote)})
    return records


def prepare(path, kind):
    path = Path(path).expanduser().resolve()
    if kind not in KINDS:
        raise DataError("未知资料类型")
    if not path.is_file():
        raise DataError(f"文件不存在：{path}")
    if path.stat().st_size > 200 * 1024 * 1024:
        raise DataError("单文件超过 200 MB，请先分段再导入")
    raw = path.read_bytes()
    if not raw:
        raise DataError("资料为空文件")
    digest = hashlib.sha256(raw).hexdigest()
    source_id = stable_id("src_", kind, digest)
    source = {"id": source_id, "filename": path.name, "kind": kind, "sha256": digest,
              "status": "complete", "pages": [], "segments": [], "warnings": [],
              "stored_path": "sources/" + source_id + path.suffix.lower(),
              "location_type": "page" if path.suffix.lower() == ".pdf" else
                               "slide" if path.suffix.lower() == ".pptx" else "text_block"}
    if kind == "audio":
        if path.suffix.lower() not in AUDIO:
            raise DataError("不支持该录音格式")
        source["status"] = "pending"
        source["media_task"] = "transcribe"
        source["warnings"] = ["录音已保存，等待自动转写"]
    elif path.suffix.lower() in IMAGES or path.suffix.lower() == ".pdf":
        source["status"] = "pending"
        source["media_task"] = "ocr"
        source["location_type"] = "page" if path.suffix.lower() == ".pdf" else "image"
        source["warnings"] = ["资料已保存，等待读取扫描页或图片文字"]
    elif path.suffix.lower() in TEXT:
        text = decode_text(raw)
        source["segments"] = split_segments(text, path.suffix.lower())
        source["pages"] = [{"number": 1, "text": text}]
    elif path.suffix.lower() == ".ppt":
        from .legacy_ppt import read_legacy_ppt
        source["pages"] = read_legacy_ppt(path)
        source["location_type"] = "slide"
        source["status"] = "partial"
        source["warnings"] = ["已读取旧版课件的当前幻灯片文本，图示仍需结合原件核对"]
        for page in source["pages"]:
            for segment in split_segments(page["text"]):
                segment["locator"]["slide"] = page["number"]
                source["segments"].append(segment)
    else:
        if path.suffix.lower() not in extract.READERS:
            raise DataError("支持 TXT/Markdown/SRT/VTT/PPTX/DOCX/PDF；旧版 PPT 请先转换")
        result = extract.extract_source(str(path.parent), str(path))
        if result.error:
            source["status"] = "failed"
            source["warnings"] = [result.error]
        else:
            source["pages"] = [{"number": page.number, "text": page.text} for page in result.pages]
            for page in result.pages:
                for segment in split_segments(page.text):
                    segment["locator"]["page"] = page.number
                    source["segments"].append(segment)
            source["warnings"] = list(result.warnings)
            if any(not page.text.strip() for page in result.pages) or "no_text" in result.warnings:
                source["status"] = "partial"
    if kind != "audio" and source["status"] == "complete" and not source["segments"]:
        source["status"] = "partial"
        source["warnings"].append("未提取到有效文本，需要查看原始页面")
    return source, raw


def extractive_summary(source):
    """A clearly labelled preview; the agent can separately save a semantic summary."""
    lines = ["原文摘录预览（尚未由 Agent 完成知识总结）"]
    snippets = []
    length = 0
    for segment in source["segments"]:
        if len(snippets) >= 12 or length > 1800:
            break
        snippets.append(segment["text"])
        length += len(segment["text"])
    lines.extend("- " + text.replace("\n", " ") for text in snippets)
    if len(snippets) < len(source["segments"]):
        lines.append("其余原文已留存，可通过 source-show 分段读取；此预览不代表完整总结。")
    if not snippets:
        lines.append("暂无可阅读文本，请完成转写或补充资料。")
    return "\n".join(lines)
