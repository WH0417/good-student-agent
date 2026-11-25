"""Local speech-to-text adapter with source-bound resumable checkpoints.

The engine API is faster-whisper's public ``WhisperModel.transcribe`` API.
No speaker identity is inferred from the model's transcript. This module does
not install packages, upload audio, or use an LLM to fill missing speech.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

from .storage import atomic_json


_CACHE_VERSION = 1
_ENGINE = "faster-whisper"


def _fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _value(item: Any, name: str, default=None):
    return item.get(name, default) if isinstance(item, dict) else getattr(item, name, default)


def _timestamp(seconds: float) -> str:
    # This is a display conversion of the engine's actual time, not an
    # invented alignment derived from text length or paragraph number.
    total_ms = round(seconds * 1000)
    hours, remaining = divmod(total_ms, 3600000)
    minutes, remaining = divmod(remaining, 60000)
    whole, millis = divmod(remaining, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole:02d}.{millis:03d}"


def _normal_segment(raw, number: int) -> dict | None:
    text = _value(raw, "text")
    if not isinstance(text, str):
        raise ValueError("转写片段缺少有效文本")
    if not text.strip():
        return None
    start, end = _value(raw, "start"), _value(raw, "end")
    if isinstance(start, bool) or isinstance(end, bool):
        raise ValueError("转写时间戳不是有效数值")
    try:
        start, end = float(start), float(end)
    except (ValueError, TypeError):
        raise ValueError("转写时间戳不是有效数值") from None
    if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start:
        raise ValueError("转写时间戳无效，未自动修补")
    return {
        "text": text.strip(),
        "locator": {"paragraph": number, "timestamp": _timestamp(start), "start": start, "end": end},
        "speaker": "unknown",
    }


def _duration(info) -> float | None:
    value = _value(info, "duration")
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _result(segments, status, warnings, processing, failed_ranges=None):
    text = "\n".join(f"[{item['locator']['timestamp']}] {item['text']}" for item in segments)
    return {
        "pages": [{"number": 1, "text": text}] if text else [],
        "segments": deepcopy(segments),
        "status": status,
        "warnings": list(warnings),
        "processing": dict(processing),
        # Speech ranges are not presented as every second of an entire course.
        "completed_ranges": [
            {"start": item["locator"]["start"], "end": item["locator"]["end"]} for item in segments
        ],
        "failed_ranges": failed_ranges or [],
    }


def _default_transcriber(model_name: str, work_dir: Path):
    from faster_whisper import WhisperModel

    # A caller can share one project-local model cache between all courses.
    configured = os.environ.get("GOOD_STUDENT_MODELS_DIR")
    model_root = Path(configured).expanduser().resolve() if configured else work_dir / "models"
    model_root.mkdir(parents=True, exist_ok=True)
    return WhisperModel(model_name, device="cpu", compute_type="int8", download_root=str(model_root))


def _cache_path(work_dir: Path, key: str) -> Path:
    path = work_dir / ("asr-" + key + ".json")
    if not path.resolve().is_relative_to(work_dir):
        raise ValueError("转写缓存路径超出工作目录")
    return path


def _load_checkpoint(path: Path, identity: dict):
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("identity") != identity or data.get("version") != _CACHE_VERSION:
            return None
        result = data["result"]
        if result["status"] not in ("complete", "partial", "pending", "failed"):
            return None
        clean = []
        for record in result["segments"]:
            segment = _normal_segment({
                "text": record["text"], "start": record["locator"]["start"], "end": record["locator"]["end"],
            }, len(clean) + 1)
            if segment:
                clean.append(segment)
        result["segments"] = clean
        return result
    except (OSError, ValueError, TypeError, KeyError):
        # A malformed derived cache is ignored. The original recording is not
        # modified, and no foreign cached quote becomes classroom evidence.
        return None


def transcribe_audio(path: Path, work_dir: Path, *, model=None, language="zh", transcriber=None) -> dict:
    """Transcribe an audio file locally, preserving partial output on failure.

    ``transcriber`` may be a WhisperModel-like object or a callable with the
    same ``(audio_path, **options) -> (segments, info)`` contract. It is useful
    for host adapters and tests. Cached complete results are reused only when
    file bytes, model and language match; partial results resume with actual
    engine time via ``clip_timestamps``.
    """
    model_name = str(model or os.environ.get("GOOD_STUDENT_WHISPER_MODEL") or "small")
    processing = {"engine": _ENGINE, "model": model_name, "language": language,
                  "device": "cpu", "compute_type": "int8", "cache_hit": False}
    warnings = []
    segments = []
    path = Path(path).expanduser().resolve()
    work_dir = Path(work_dir).expanduser().resolve()
    if not path.is_file():
        return _result([], "failed", ["录音文件不存在"], processing)
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        identity = {"sha256": _fingerprint(path), "model": model_name, "language": language,
                    "engine": _ENGINE, "options_version": 1}
        key = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:24]
        checkpoint_path = _cache_path(work_dir, key)
        old = _load_checkpoint(checkpoint_path, identity)
    except (OSError, ValueError) as exc:
        return _result([], "failed", [f"无法建立转写缓存：{exc}"], processing)
    if old:
        segments = old["segments"]
        if old["status"] == "complete" and segments:
            result = deepcopy(old)
            result["processing"]["cache_hit"] = True
            return result
        processing["resumed_segments"] = len(segments)
        if old.get("processing", {}).get("duration") is not None:
            processing["duration"] = old["processing"]["duration"]
    resume_from = max((segment["locator"]["end"] for segment in segments), default=0.0)
    processing["resume_from"] = resume_from

    def save(status, issue=None):
        failed = []
        if status != "complete":
            failed = [{"start": max((s["locator"]["end"] for s in segments), default=0.0),
                       "end": processing.get("duration"), "reason": issue or "未完成转写"}]
        result = _result(segments, status, warnings, processing, failed)
        try:
            atomic_json(checkpoint_path, {"version": _CACHE_VERSION, "identity": identity, "result": result})
        except OSError as exc:
            result["warnings"].append(f"转写结果可用，但缓存保存失败：{exc}")
        return result

    if not path.stat().st_size:
        warnings.append("录音文件为空")
        return save("failed", "空文件")
    if transcriber is None:
        try:
            transcriber = _default_transcriber(model_name, work_dir)
        except ImportError:
            warnings.append("缺少本地语音转写组件，需由 Agent 准备 faster-whisper 运行环境")
            return save("partial" if segments else "pending", "转写组件不可用")
        except Exception as exc:
            warnings.append(f"本地转写模型尚未就绪，可准备模型后重试：{exc}")
            return save("partial" if segments else "pending", "模型尚未就绪")
    try:
        transcribe = getattr(transcriber, "transcribe", transcriber)
        options = {"language": None if language in (None, "auto") else language,
                   "beam_size": 5, "vad_filter": False, "condition_on_previous_text": False}
        if resume_from:
            options["clip_timestamps"] = str(resume_from)
        stream, info = transcribe(str(path), **options)
        duration = _duration(info)
        if duration is not None:
            processing["duration"] = duration
        detected_language = _value(info, "language")
        if detected_language:
            processing["detected_language"] = detected_language
        signatures = {(s["locator"]["start"], s["locator"]["end"], s["text"]) for s in segments}
        # Iteration is inside the try block: faster-whisper starts decoding here.
        for raw in stream:
            segment = _normal_segment(raw, len(segments) + 1)
            if segment is None:
                continue
            locator = segment["locator"]
            if duration is not None and locator["end"] > duration + 0.1:
                raise ValueError("片段时间超出实际录音长度，未作为有效转写保存")
            if locator["end"] <= resume_from and resume_from:
                continue
            signature = (locator["start"], locator["end"], segment["text"])
            if signature in signatures:
                continue
            if segments and locator["start"] < segments[-1]["locator"]["start"]:
                raise ValueError("转写片段时间顺序异常，保留已完成内容")
            signatures.add(signature)
            segments.append(segment)
            # Periodic progress also survives process interruption, without
            # rewriting a long lecture's whole cache for every short sentence.
            if len(segments) % 5 == 0:
                save("partial", "转写进行中")
    except Exception as exc:
        warnings.append(f"录音尚未全部转写，已保留识别出的片段，可重试：{exc}")
        return save("partial" if segments else "failed", str(exc))
    if not segments:
        warnings.append("未识别到可用语音，请检查录音内容与语言设置")
        return save("failed", "未识别到语音")
    return save("complete")
