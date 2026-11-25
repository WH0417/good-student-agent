"""Explicit course directories and a transactionally replaced JSON authority."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
import uuid


class DataError(ValueError):
    """An actionable input or saved-data error."""


def resolved_path(value):
    """Normalize Windows' optional device prefix after resolving symlinks.

    Windows may return an extended-length spelling when another process holds
    a file. Comparing that spelling to its unprefixed parent is a false escape.
    """
    path = Path(value).expanduser().resolve()
    if os.name == "nt":
        text = str(path)
        if text.startswith("\\\\?\\UNC\\"):
            path = Path("\\\\" + text[8:])
        elif text.startswith("\\\\?\\") and re.match(r"[A-Za-z]:", text[4:]):
            path = Path(text[4:])
    return path


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def score_settings(score_max=100, passing_score=60):
    try:
        maximum, target = float(score_max), float(passing_score)
    except (ValueError, TypeError):
        raise DataError("卷面满分和及格线必须是数值") from None
    if not math.isfinite(maximum) or not math.isfinite(target) or not 0 < target <= maximum:
        raise DataError("卷面满分必须大于零，及格线必须大于零且不超过满分")
    return maximum, target


def atomic_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


@contextmanager
def file_lock(path: Path, timeout=5):
    """OS-released lock: process termination does not leave a stale lock owner."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if path.stat().st_size == 0:
            stream.write(b"0")
            stream.flush()
        start = time.monotonic()
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() - start >= timeout:
                    raise DataError("该科目正在被其他操作更新，请稍后重试") from None
                time.sleep(0.05)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class Store:
    def __init__(self, root):
        self.root = resolved_path(root)

    def course_dir(self, course_id):
        if not isinstance(course_id, str) or not re.fullmatch(r"course_[0-9a-f]{12}", course_id):
            raise DataError("无效课程编号，请使用 course-list 查看明确的课程编号")
        target = resolved_path(self.root / "courses" / course_id)
        if not target.is_relative_to(self.root):
            raise DataError("课程路径超出项目数据目录")
        return target

    def contained(self, course_id, relative):
        base = self.course_dir(course_id)
        path = resolved_path(base / relative)
        if not path.is_relative_to(base):
            raise DataError("资料路径超出课程目录")
        return path

    def read(self, course_id):
        path = self.course_dir(course_id) / "state.json"
        if not path.exists():
            raise DataError("课程不存在，不会回退到其他科目")
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DataError(f"课程记录无法读取，已保留原文件：{path}；{exc}") from exc
        if not isinstance(state, dict) or state.get("version") != 1:
            raise DataError("课程记录版本或所属课程异常，未自动清空记录")
        course = state.get("course")
        if not isinstance(course, dict) or course.get("id") != course_id:
            raise DataError("课程记录版本或所属课程异常，未自动清空记录")
        for key in ("sources", "lectures", "topics", "focus", "candidates", "questions", "progress", "question_views"):
            if not isinstance(state.get(key), dict):
                raise DataError(f"课程记录字段 {key} 损坏，已保留原文件")
        for key in ("attempts", "notes", "events"):
            if not isinstance(state.get(key), list):
                raise DataError(f"课程记录字段 {key} 损坏，已保留原文件")
        if not isinstance(state.get("revision"), int) or state["revision"] < 1:
            raise DataError("课程记录版本号损坏，已保留原文件")
        return state

    @contextmanager
    def transaction(self, course_id):
        directory = self.course_dir(course_id)
        if not (directory / "state.json").exists():
            raise DataError("课程不存在")
        with file_lock(directory / ".write.lock"):
            state = self.read(course_id)
            yield state
            state["revision"] += 1
            state["updated_at"] = now()
            atomic_json(directory / "state.json", state)

    def create(self, name, mode=None, score_max=100, passing_score=60):
        if not str(name).strip():
            raise DataError("请提供课程名称")
        if mode not in (None, "high", "pass"):
            raise DataError("模式应为 high 或 pass")
        maximum, target = score_settings(score_max, passing_score)
        course_id = "course_" + uuid.uuid4().hex[:12]
        directory = self.course_dir(course_id)
        directory.mkdir(parents=True, exist_ok=False)
        state = {
            "version": 1, "revision": 1, "created_at": now(), "updated_at": now(),
            "course": {"id": course_id, "name": str(name).strip(), "mode": mode,
                       "score_max": maximum, "passing_score": target},
            "sources": {}, "lectures": {}, "topics": {}, "focus": {}, "candidates": {},
            "questions": {}, "attempts": [], "progress": {}, "notes": [],
            "question_views": {}, "events": [], "next_task": "导入课堂记录或课程资料",
        }
        atomic_json(directory / "state.json", state)
        return state

    def list_courses(self):
        directory = self.root / "courses"
        if not directory.exists():
            return []
        result = []
        for item in sorted(directory.iterdir()):
            if not item.is_dir() or not re.fullmatch(r"course_[0-9a-f]{12}", item.name):
                continue
            try:
                state = self.read(item.name)
                result.append({**state["course"], "revision": state["revision"]})
            except DataError as exc:
                result.append({"id": item.name, "error": str(exc)})
        return result
