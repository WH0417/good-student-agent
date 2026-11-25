"""Durable per-course work with explicit, expiring Agent ownership.

This module does not run a model. A queued task is executable only when an
actual Agent claims it. Reads project expired ownership as waiting_agent so a
stopped host never appears to be generating indefinitely.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
import re
import secrets
import statistics
import time
import uuid

from .storage import DataError, Store, atomic_json, file_lock


ACTIVE_STATUSES = frozenset({"queued", "running", "waiting_agent"})
TERMINAL_STATUSES = frozenset({"completed", "partial", "failed", "cancelled"})
STATUSES = ACTIVE_STATUSES | TERMINAL_STATUSES


class JobManager:
    def __init__(self, service_or_store, lease_seconds=300, clock=None):
        self.store = getattr(service_or_store, "store", service_or_store)
        if not isinstance(self.store, Store):
            raise TypeError("JobManager requires StudentService or Store")
        if (isinstance(lease_seconds, bool) or not isinstance(lease_seconds, (int, float))
                or not math.isfinite(lease_seconds) or lease_seconds <= 0):
            raise ValueError("lease_seconds must be positive and finite")
        self.lease_seconds = float(lease_seconds)
        self.clock = clock or time.time

    def _stamp(self):
        return datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(timespec="milliseconds")

    @staticmethod
    def _copy_json(value, label):
        try:
            return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
        except (ValueError, TypeError, OverflowError) as exc:
            raise DataError(f"{label}必须是可保存的 JSON 内容") from exc

    def _path(self, course_id, job_id):
        if not isinstance(job_id, str) or not re.fullmatch(r"job_[0-9a-f]{12}", job_id):
            raise DataError("无效任务编号")
        return self.store.contained(course_id, f"jobs/{job_id}/job.json")

    def _root(self, course_id):
        self.store.read(course_id)
        return self.store.contained(course_id, "jobs")

    def _lock(self, course_id):
        self.store.read(course_id)
        return file_lock(self.store.contained(course_id, "jobs/.write.lock"))

    def _load(self, course_id, job_id):
        path = self._path(course_id, job_id)
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise DataError("任务不存在或不属于当前课程") from exc
        except (OSError, ValueError) as exc:
            raise DataError("任务记录无法读取，已保留原文件") from exc
        if (not isinstance(job, dict) or job.get("version") != 1
                or job.get("id") != job_id or job.get("course_id") != course_id
                or not isinstance(job.get("status"), str) or job["status"] not in STATUSES
                or not isinstance(job.get("kind"), str)
                or not isinstance(job.get("request"), str)
                or not isinstance(job.get("stage"), str)
                or not isinstance(job.get("created_at"), str)
                or not isinstance(job.get("updated_at"), str)
                or not isinstance(job.get("progress"), dict)
                or not isinstance(job.get("source_ids"), list)
                or not isinstance(job.get("input_sources"), list)
                or not isinstance(job.get("settings"), dict)):
            raise DataError("任务记录损坏或所属课程异常，已保留原文件")
        return job

    def _all(self, course_id):
        root = self._root(course_id)
        if not root.exists():
            return []
        result = []
        for directory in root.iterdir():
            if directory.is_dir() and re.fullmatch(r"job_[0-9a-f]{12}", directory.name):
                result.append(self._load(course_id, directory.name))
        return sorted(result, key=lambda job: (job["created_at"], job["id"]), reverse=True)

    def _expired(self, job):
        if job.get("status") != "running":
            return False
        if (not isinstance(job.get("lease_token"), str) or not job["lease_token"]
                or not isinstance(job.get("worker_id"), str) or not job["worker_id"]):
            return True
        try:
            heartbeat = datetime.fromisoformat(job["heartbeat_at"])
            if heartbeat.tzinfo is None:
                return True
            return self.clock() - heartbeat.timestamp() >= self.lease_seconds
        except (ValueError, TypeError, KeyError, OverflowError):
            return True

    def _public(self, job, include_token=False):
        result = deepcopy(job)
        if self._expired(job):
            result.update(status="waiting_agent", stage="等待 Agent 继续", worker_id=None,
                          heartbeat_at=None, error=job.get("error") or "执行器已失联，可从检查点继续")
            result.pop("lease_token", None)
        elif not include_token:
            result.pop("lease_token", None)
        progress = result.setdefault("progress", {})
        completed, total = self._counts(progress)
        if result["status"] == "completed" and not result.get("error") and not result.get("error_details"):
            percent = 100.0
        elif total is not None:
            percent = round(min(99.0, completed * 100 / total), 1)
        else:
            percent = 0.0 if result["status"] in ("queued", "waiting_agent") else None
        progress["percent"] = percent
        eta = self._estimate_remaining(result)
        progress["eta_seconds"] = result["eta_seconds"] = eta
        finish = (datetime.fromtimestamp(self.clock() + eta, timezone.utc).isoformat(timespec="seconds")
                  if eta is not None else None)
        progress["estimated_completion_at"] = result["estimated_completion_at"] = finish
        result.pop("_progress_clock", None)
        return result

    @staticmethod
    def _counts(progress):
        completed, total = progress.get("completed"), progress.get("total")
        if (isinstance(completed, bool) or isinstance(total, bool)
                or not isinstance(completed, (int, float)) or not isinstance(total, (int, float))
                or not math.isfinite(completed) or not math.isfinite(total)
                or total <= 0 or not 0 <= completed <= total):
            return None, None
        return float(completed), float(total)

    @staticmethod
    def _phase(progress):
        return progress.get("phase", ""), progress.get("unit", "")

    def _estimate_remaining(self, job):
        progress = job["progress"]
        completed, total = self._counts(progress)
        if (job["status"] != "running" or job.get("error") or job.get("error_details")
                or total is None or completed < 1 or completed >= total):
            return None
        durations = []
        samples = progress.get("samples", [])
        if not isinstance(samples, list):
            return None
        for sample in samples[-8:]:
            if isinstance(sample, dict):
                if (sample.get("phase", progress.get("phase", "")),
                        sample.get("unit", progress.get("unit", ""))) != self._phase(progress):
                    continue
                seconds = sample.get("duration_seconds", sample.get("seconds"))
                units = sample.get("units", 1)
            else:
                seconds, units = sample, 1
            if (isinstance(seconds, bool) or isinstance(units, bool)
                    or not isinstance(seconds, (int, float)) or not isinstance(units, (int, float))
                    or not math.isfinite(seconds) or not math.isfinite(units) or seconds <= 0 or units <= 0):
                continue
            durations.append(seconds / units)
        # One completed unit is insufficient evidence that later units cost the
        # same. Require at least two comparable observations with modest spread.
        if len(durations) < 2:
            return None
        average = statistics.mean(durations)
        if statistics.pstdev(durations) / average > 0.35:
            return None
        # If the current unit is already far slower than the observations, the
        # old estimate is no longer defensible; stop showing a fake countdown.
        checkpoint = job.get("_progress_clock") or {}
        elapsed = max(0, self.clock() - checkpoint.get("at", self.clock()))
        if elapsed > average * 2:
            return None
        return max(1, math.ceil((total - completed) * average - min(elapsed, average)))

    def _sample_progress(self, job, original):
        """Derive timing samples from real checkpoint intervals, excluding pauses."""
        progress, previous = job["progress"], original.get("progress", {})
        completed, total = self._counts(progress)
        if total is None:
            job.pop("_progress_clock", None)
            return
        before_completed, before_total = self._counts(previous)
        same_phase = self._phase(progress) == self._phase(previous) and total == before_total
        samples = progress.get("samples")
        if samples is None:
            samples = deepcopy(previous.get("samples", [])) if same_phase else []
        if not isinstance(samples, list):
            raise DataError("进度样本必须为列表")
        clock = original.get("_progress_clock") or {}
        compatible = (clock.get("phase") == list(self._phase(progress)) and clock.get("total") == total
                      and isinstance(clock.get("completed"), (int, float))
                      and isinstance(clock.get("at"), (int, float)))
        if compatible and completed > clock["completed"] and self.clock() > clock["at"]:
            # Sampling must be backed by saved units, not by a percentage supplied
            # from a UI. Keep only recent samples to adapt after a speed change.
            samples = [*samples, (self.clock() - clock["at"]) / (completed - clock["completed"])][-8:]
        if samples:
            progress["samples"] = samples
        elif "samples" in progress:
            progress["samples"] = []
        if not compatible or completed != clock["completed"]:
            job["_progress_clock"] = {"completed": completed, "total": total,
                                       "phase": list(self._phase(progress)), "at": self.clock()}
        if same_phase and "stage_started_at" not in progress and previous.get("stage_started_at"):
            progress["stage_started_at"] = previous["stage_started_at"]
        if not same_phase and "stage_started_at" not in progress:
            progress["stage_started_at"] = self._stamp()

    def _error_details(self, details, job):
        if details is None:
            return None
        if not isinstance(details, dict) or not isinstance(details.get("message"), str) or not details["message"].strip():
            raise DataError("错误详情需要提供 message 文字")
        allowed = {"stage", "source_id", "filename", "locator", "message"}
        if set(details) - allowed:
            raise DataError("错误详情包含不支持的字段")
        for key in ("stage", "source_id", "filename"):
            if key in details and details[key] is not None and not isinstance(details[key], str):
                raise DataError(f"错误详情 {key} 必须为文字")
        if details.get("source_id") is not None and details["source_id"] not in job["source_ids"]:
            raise DataError("错误详情引用了任务范围以外的资料")
        if details.get("locator") is not None and not isinstance(details["locator"], dict):
            raise DataError("错误详情定位必须为对象")
        return self._copy_json(details, "错误详情")

    def _save(self, job):
        atomic_json(self._path(job["course_id"], job["id"]), job)

    def create(self, course_id, source_ids=None, request="", kind="build", settings=None):
        if not isinstance(kind, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", kind):
            raise DataError("任务类型无效")
        if not isinstance(request, str):
            raise DataError("任务要求必须为文字")
        if settings is None:
            settings = {}
        if not isinstance(settings, dict):
            raise DataError("任务设置必须为对象")
        frozen_settings = self._copy_json(settings, "任务设置")
        with self._lock(course_id):
            state = self.store.read(course_id)
            course = state["course"]
            if course.get("mode") not in ("pass", "high"):
                raise DataError("请先选择高分或速通模式")
            if source_ids is None:
                ids = list(state["sources"])
            elif (not isinstance(source_ids, (list, tuple))
                  or any(not isinstance(sid, str) for sid in source_ids)):
                raise DataError("资料列表无效")
            else:
                ids = list(dict.fromkeys(source_ids))
            if any(sid not in state["sources"] for sid in ids):
                raise DataError("任务资料不存在或不属于当前课程")
            if kind == "build":
                for existing in self._all(course_id):
                    if existing["kind"] == "build" and existing["status"] in ACTIVE_STATUSES:
                        return {**self._public(existing), "reused": True}
            stamp = self._stamp()
            job = {
                "version": 1, "id": "job_" + uuid.uuid4().hex[:12], "course_id": course_id,
                "kind": kind, "request": request, "settings": frozen_settings,
                "source_ids": ids,
                "input_sources": [
                    {"id": sid, "sha256": state["sources"][sid].get("sha256"),
                     "filename": state["sources"][sid].get("filename"),
                     "status": state["sources"][sid].get("status")}
                    for sid in ids
                ],
                "input_revision": state["revision"],
                "mode": course["mode"], "score_max": course["score_max"],
                "passing_score": course["passing_score"], "status": "queued",
                "stage": "等待 Agent 接收", "progress": {}, "package_id": None,
                "created_at": stamp, "updated_at": stamp,
                "worker_id": None, "lease_token": None, "heartbeat_at": None, "error": None, "error_details": None,
            }
            self._save(job)
            return self._public(job)

    def list(self, course_id):
        return [self._public(job) for job in self._all(course_id)]

    def read(self, course_id, job_id):
        self.store.read(course_id)
        return self._public(self._load(course_id, job_id))

    def resume(self, course_id, job_id):
        """Explicitly reopen a partial/failed task without losing its package.

        A cancelled task stays cancelled: resuming it requires a new user task.
        Input scope and mode remain the original snapshot, even if the current
        course settings changed while this task was stopped.
        """
        with self._lock(course_id):
            job = self._load(course_id, job_id)
            if job["status"] not in ("partial", "failed"):
                raise DataError("只能恢复部分完成或失败的任务")
            if job["kind"] == "build":
                for other in self._all(course_id):
                    if (other["id"] != job_id and other["kind"] == "build"
                            and other["status"] in ACTIVE_STATUSES):
                        raise DataError("该课程已有待处理生成任务，请先处理现有任务")
            stamp = self._stamp()
            job.update(status="waiting_agent", stage="等待 Agent 继续", updated_at=stamp,
                       resumed_at=stamp, worker_id=None, lease_token=None,
                       heartbeat_at=None, error=None, error_details=None)
            self._save(job)
            return self._public(job)

    def claim(self, course_id, job_id, worker_id):
        if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 160:
            raise DataError("执行器编号无效")
        with self._lock(course_id):
            job = self._load(course_id, job_id)
            if job["status"] not in ACTIVE_STATUSES:
                raise DataError("任务已经结束，不能再次领取")
            if job["status"] == "running" and not self._expired(job):
                raise DataError("任务已由执行器占用，请继续现有任务或等待租约到期")
            stamp = self._stamp()
            job.update(status="running", worker_id=worker_id, lease_token="lease_" + secrets.token_urlsafe(32),
                       heartbeat_at=stamp, updated_at=stamp, error=None, error_details=None)
            completed, total = self._counts(job["progress"])
            if total is not None:
                job["_progress_clock"] = {"completed": completed, "total": total,
                                           "phase": list(self._phase(job["progress"])), "at": self.clock()}
            if job["stage"] in ("等待 Agent 接收", "等待 Agent 继续"):
                job["stage"] = "Agent 已接收任务"
            self._save(job)
            return self._public(job, include_token=True)

    def _owned(self, course_id, job_id, lease_token):
        job = self._load(course_id, job_id)
        saved = job.get("lease_token")
        if (job["status"] != "running" or not isinstance(lease_token, str)
                or not isinstance(saved, str)
                or not secrets.compare_digest(saved.encode("utf-8"), lease_token.encode("utf-8"))):
            raise DataError("任务执行凭据无效，不能写入其他执行器的任务")
        if self._expired(job):
            raise DataError("执行器租约已过期，请重新领取任务后继续")
        return job

    @contextmanager
    def owned_commit(self, course_id, job_id, lease_token):
        """Fence artifact writes and job changes against concurrent takeover.

        Only use this context for committing already-prepared local results,
        never for model generation. It holds the course's task lock from the
        ownership check through successful artifact writing and job saving.
        Do not call another locking JobManager operation inside the context.

        The yielded job is mutable but internal (it includes the lease token).
        On an exception its edits are discarded; already-atomic artifact writes
        may remain as recoverable checkpoints. On success refresh the heartbeat
        and clear ownership when the caller ended or released the task.
        """
        with self._lock(course_id):
            job = self._owned(course_id, job_id, lease_token)
            original = deepcopy(job)
            yield job
            immutable = ("version", "id", "course_id", "kind", "request", "settings",
                         "source_ids", "input_sources", "input_revision", "mode",
                         "score_max", "passing_score", "created_at", "worker_id", "lease_token")
            if any(job.get(key) != original.get(key) for key in immutable):
                raise DataError("提交结果不能更改任务身份、执行凭据或冻结输入")
            if (not isinstance(job.get("status"), str)
                    or job["status"] not in STATUSES - {"queued"}):
                raise DataError("任务状态无效")
            if not isinstance(job.get("stage"), str) or not job["stage"].strip():
                raise DataError("任务阶段必须为非空文字")
            if not isinstance(job.get("progress"), dict):
                raise DataError("任务进度必须为对象")
            if job.get("error") is not None and not isinstance(job["error"], str):
                raise DataError("任务错误说明必须为文字")
            job["error_details"] = self._error_details(job.get("error_details"), job)
            if job["error_details"] is not None and not job.get("error"):
                job["error"] = job["error_details"]["message"]
            package_id = job.get("package_id")
            if package_id is not None and (not isinstance(package_id, str)
                                          or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", package_id)):
                raise DataError("复习包编号无效")
            if job["kind"] == "build" and job["status"] in ("completed", "partial") and not package_id:
                raise DataError("结束生成任务前必须记录复习包")
            self._sample_progress(job, original)
            self._copy_json(job, "任务记录")
            stamp = self._stamp()
            job.update(updated_at=stamp, heartbeat_at=stamp)
            if job["status"] != "running":
                job.update(worker_id=None, lease_token=None, heartbeat_at=None)
            self._save(job)

    def heartbeat(self, course_id, job_id, lease_token):
        with self._lock(course_id):
            job = self._owned(course_id, job_id, lease_token)
            stamp = self._stamp()
            job.update(heartbeat_at=stamp, updated_at=stamp)
            self._save(job)
            return self._public(job)

    def update(self, course_id, job_id, lease_token, stage=None, progress=None,
               package_id=None, status=None, error=None, error_details=None):
        if stage is not None and (not isinstance(stage, str) or not stage.strip()):
            raise DataError("任务阶段必须为非空文字")
        if progress is not None and not isinstance(progress, dict):
            raise DataError("任务进度必须为对象")
        frozen_progress = self._copy_json(progress, "任务进度") if progress is not None else None
        if package_id is not None and (not isinstance(package_id, str)
                                      or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", package_id)):
            raise DataError("复习包编号无效")
        if status is not None and (not isinstance(status, str) or status not in STATUSES - {"queued"}):
            raise DataError("任务状态无效")
        if error is not None and not isinstance(error, str):
            raise DataError("任务错误说明必须为文字")
        with self._lock(course_id):
            job = self._owned(course_id, job_id, lease_token)
            original = deepcopy(job)
            if (job["kind"] == "build" and status in ("completed", "partial")
                    and not (package_id or job.get("package_id"))):
                raise DataError("结束生成任务前必须记录复习包")
            if stage is not None:
                job["stage"] = stage
            if frozen_progress is not None:
                job["progress"] = frozen_progress
            if package_id is not None:
                job["package_id"] = package_id
            if status is not None:
                job["status"] = status
            if error is not None:
                job["error"] = error
            if error_details is not None:
                job["error_details"] = self._error_details(error_details, job)
                if error is None:
                    job["error"] = job["error_details"]["message"]
            self._sample_progress(job, original)
            stamp = self._stamp()
            job.update(updated_at=stamp, heartbeat_at=stamp)
            if job["status"] != "running":
                job.update(worker_id=None, lease_token=None, heartbeat_at=None)
            self._save(job)
            return self._public(job)

    def release(self, course_id, job_id, lease_token, error=None):
        return self.update(course_id, job_id, lease_token, status="waiting_agent",
                           stage="等待 Agent 继续", error=error)

    def complete(self, course_id, job_id, lease_token, package_id, partial=False):
        return self.update(course_id, job_id, lease_token, package_id=package_id,
                           status="partial" if partial else "completed",
                           stage="部分完成，请查看资料说明" if partial else "复习资料已完成")
