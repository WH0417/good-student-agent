"""Durable jobs must never fake Agent activity or lose their input boundary."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from good_student.jobs import JobManager
from good_student.service import StudentService
from good_student.storage import DataError, resolved_path


class JobManagerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = StudentService(self.root / "data")
        self.cid = self.service.store.create("数据库", "pass")["course"]["id"]
        self.other = self.service.store.create("高数", "high")["course"]["id"]
        path = self.root / "课件.md"
        path.write_text("# 数据库\n关系由元组构成。", encoding="utf-8")
        self.sid = self.service.import_material(self.cid, path, "material")["source"]["id"]
        self.time = 1_800_000_000.0
        self.jobs = JobManager(self.service, lease_seconds=10, clock=lambda: self.time)

    def tearDown(self):
        self.temp.cleanup()

    def test_snapshot_survives_reopen_and_course_changes(self):
        settings = {"chapters": ["数据库"], "practice": {"basic": 2}}
        job = self.jobs.create(self.cid, settings=settings)
        settings["chapters"].append("should not appear")
        settings["practice"]["basic"] = 99
        old_hash = job["input_sources"][0]["sha256"]
        self.service.set_mode(self.cid, "high")
        with self.service.store.transaction(self.cid) as state:
            state["sources"][self.sid]["sha256"] = "changed"
        recovered = JobManager(self.service.store).read(self.cid, job["id"])
        self.assertEqual(recovered["mode"], "pass")
        self.assertEqual(recovered["settings"]["chapters"], ["数据库"])
        self.assertEqual(recovered["settings"]["practice"]["basic"], 2)
        self.assertEqual(recovered["input_sources"][0]["sha256"], old_hash)
        self.assertNotIn("lease_token", recovered)

    def test_concurrent_claim_only_one_worker_wins(self):
        job = self.jobs.create(self.cid)
        def take(worker):
            try:
                return self.jobs.claim(self.cid, job["id"], worker)
            except DataError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(take, ("worker-one", "worker-two")))
        successful = [claim for claim in claims if claim]
        self.assertEqual(len(successful), 1)
        self.assertTrue(successful[0]["lease_token"])
        self.assertEqual(self.jobs.read(self.cid, job["id"])["status"], "running")

    def test_concurrent_build_creation_preserves_single_task(self):
        with ThreadPoolExecutor(max_workers=3) as pool:
            jobs = list(pool.map(lambda i: self.jobs.create(self.cid, request=f"request-{i}"), range(3)))
        self.assertEqual(len({job["id"] for job in jobs}), 1)
        self.assertEqual(len(self.jobs.list(self.cid)), 1)
        self.assertEqual(sum(bool(job.get("reused")) for job in jobs), 2)

    def test_expired_worker_is_waiting_and_cannot_write_after_reclaim(self):
        job = self.jobs.create(self.cid)
        first = self.jobs.claim(self.cid, job["id"], "first")
        self.jobs.update(self.cid, job["id"], first["lease_token"],
                         stage="第三章", progress={"completed": 2, "total": 4})
        self.time += 11
        projected = self.jobs.read(self.cid, job["id"])
        self.assertEqual(projected["status"], "waiting_agent")
        self.assertEqual(self.jobs.list(self.cid)[0]["status"], "waiting_agent")
        self.assertEqual(projected["progress"]["completed"], 2)
        with self.assertRaises(DataError):
            self.jobs.heartbeat(self.cid, job["id"], first["lease_token"])
        second = self.jobs.claim(self.cid, job["id"], "second")
        self.assertNotEqual(first["lease_token"], second["lease_token"])
        with self.assertRaises(DataError):
            self.jobs.update(self.cid, job["id"], first["lease_token"], stage="stale overwrite")
        self.jobs.complete(self.cid, job["id"], second["lease_token"], "package_123456789abc")
        self.assertEqual(self.jobs.read(self.cid, job["id"])["status"], "completed")

    def test_heartbeat_extends_ownership_and_read_does_not_expose_token(self):
        job = self.jobs.create(self.cid)
        claimed = self.jobs.claim(self.cid, job["id"], "first")
        self.time += 9
        result = self.jobs.heartbeat(self.cid, job["id"], claimed["lease_token"])
        self.assertNotIn("lease_token", result)
        self.time += 9
        self.assertEqual(self.jobs.read(self.cid, job["id"])["status"], "running")
        with self.assertRaises(DataError):
            self.jobs.claim(self.cid, job["id"], "second")

    def test_duplicate_build_reuses_frozen_task_but_followup_can_queue(self):
        first = self.jobs.create(self.cid, request="生成整包")
        reused = self.jobs.create(self.cid, source_ids=[], request="另一个要求")
        self.assertEqual(first["id"], reused["id"])
        self.assertTrue(reused["reused"])
        self.assertEqual(reused["request"], "生成整包")
        self.assertEqual(reused["source_ids"], [self.sid])
        question = self.jobs.create(self.cid, kind="question", request="解释这个概念")
        self.assertNotEqual(first["id"], question["id"])
        self.assertEqual(len(self.jobs.list(self.cid)), 2)

    def test_release_and_partial_completion_preserve_checkpoint(self):
        job = self.jobs.create(self.cid)
        claimed = self.jobs.claim(self.cid, job["id"], "first")
        self.jobs.update(self.cid, job["id"], claimed["lease_token"],
                         progress={"completed": 3, "total": 4}, package_id="pkg_123456789abc")
        released = self.jobs.release(self.cid, job["id"], claimed["lease_token"], error="宿主暂停")
        self.assertEqual(released["status"], "waiting_agent")
        self.assertEqual(released["progress"]["completed"], 3)
        with self.assertRaises(DataError):
            self.jobs.update(self.cid, job["id"], claimed["lease_token"], stage="late write")
        resumed = self.jobs.claim(self.cid, job["id"], "second")
        partial = self.jobs.complete(self.cid, job["id"], resumed["lease_token"],
                                     "pkg_123456789abc", partial=True)
        self.assertEqual(partial["status"], "partial")
        self.assertIsNone(partial["worker_id"])
        self.assertNotIn("lease_token", partial)
        new_job = self.jobs.create(self.cid)
        self.assertNotEqual(job["id"], new_job["id"])

    def test_question_and_review_can_complete_without_package(self):
        for kind in ("question", "review"):
            job = self.jobs.create(self.cid, kind=kind, request="针对性答疑")
            claimed = self.jobs.claim(self.cid, job["id"], "answer-worker")
            result = self.jobs.update(self.cid, job["id"], claimed["lease_token"],
                                      status="completed", stage="答疑已保存")
            self.assertEqual(result["status"], "completed")
            self.assertIsNone(result["package_id"])
            self.assertIsNone(result["worker_id"])
        build = self.jobs.create(self.cid)
        claimed = self.jobs.claim(self.cid, build["id"], "builder")
        with self.assertRaisesRegex(DataError, "记录复习包"):
            self.jobs.update(self.cid, build["id"], claimed["lease_token"], status="completed")

    def test_resume_keeps_partial_package_frozen_scope_and_chapter_progress(self):
        job = self.jobs.create(self.cid, settings={"chapters": ["数据库"]})
        owned = self.jobs.claim(self.cid, job["id"], "first")
        self.jobs.update(self.cid, job["id"], owned["lease_token"],
                         progress={"completed": 2, "total": 3})
        self.jobs.complete(self.cid, job["id"], owned["lease_token"], "pkg_123456789abc", partial=True)
        self.service.set_mode(self.cid, "high")
        resumed = self.jobs.resume(self.cid, job["id"])
        self.assertEqual(resumed["status"], "waiting_agent")
        self.assertEqual(resumed["package_id"], "pkg_123456789abc")
        self.assertEqual({k: resumed["progress"][k] for k in ("completed", "total")}, {"completed": 2, "total": 3})
        self.assertEqual(resumed["source_ids"], [self.sid])
        self.assertEqual(resumed["input_sources"], job["input_sources"])
        self.assertEqual(resumed["settings"], {"chapters": ["数据库"]})
        self.assertEqual(resumed["mode"], "pass")
        self.assertNotIn("lease_token", resumed)
        next_owner = self.jobs.claim(self.cid, job["id"], "second")
        self.assertNotEqual(owned["lease_token"], next_owner["lease_token"])

    def test_resume_rejects_active_build_conflict_and_explicit_cancellation(self):
        first = self.jobs.create(self.cid)
        owned = self.jobs.claim(self.cid, first["id"], "first")
        self.jobs.complete(self.cid, first["id"], owned["lease_token"], "pkg_123456789abc", partial=True)
        second = self.jobs.create(self.cid)
        with self.assertRaisesRegex(DataError, "已有"):
            self.jobs.resume(self.cid, first["id"])
        self.assertEqual(self.jobs.read(self.cid, first["id"])["status"], "partial")
        next_owner = self.jobs.claim(self.cid, second["id"], "second")
        self.jobs.update(self.cid, second["id"], next_owner["lease_token"], status="cancelled")
        with self.assertRaisesRegex(DataError, "只能恢复"):
            self.jobs.resume(self.cid, second["id"])
        resumed = self.jobs.resume(self.cid, first["id"])
        self.assertEqual(resumed["status"], "waiting_agent")

    def test_failed_task_can_resume_without_fabricating_package(self):
        job = self.jobs.create(self.cid)
        owned = self.jobs.claim(self.cid, job["id"], "first")
        self.jobs.update(self.cid, job["id"], owned["lease_token"],
                         status="failed", error="尚未生成章节")
        resumed = self.jobs.resume(self.cid, job["id"])
        self.assertEqual(resumed["status"], "waiting_agent")
        self.assertIsNone(resumed["package_id"])
        self.assertIsNone(resumed["error"])

    def test_owned_commit_prevents_takeover_during_artifact_write(self):
        job = self.jobs.create(self.cid)
        first = self.jobs.claim(self.cid, job["id"], "first")
        attempted = threading.Event()
        def takeover():
            attempted.set()
            try:
                return self.jobs.claim(self.cid, job["id"], "second")
            except DataError as exc:
                return str(exc)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.jobs.owned_commit(self.cid, job["id"], first["lease_token"]) as current:
                # Even when this write crosses the heartbeat deadline, its lock
                # fences the artifact and refreshed job commit as one operation.
                self.time += 11
                future = pool.submit(takeover)
                self.assertTrue(attempted.wait(timeout=1))
                with self.assertRaises(TimeoutError):
                    future.result(timeout=0.15)
                current["progress"] = {"completed": 1, "total": 2}
                current["stage"] = "第一章已写入"
            rejected = future.result(timeout=2)
        self.assertIsInstance(rejected, str)
        self.assertIn("占用", rejected)
        saved = self.jobs.read(self.cid, job["id"])
        self.assertEqual(saved["worker_id"], "first")
        self.assertEqual({k: saved["progress"][k] for k in ("completed", "total")}, {"completed": 1, "total": 2})

    def test_owned_commit_rejects_stale_owner_before_any_artifact_write(self):
        job = self.jobs.create(self.cid)
        first = self.jobs.claim(self.cid, job["id"], "first")
        self.time += 11
        second = self.jobs.claim(self.cid, job["id"], "second")
        artifact = self.root / "should-not-exist.md"
        with self.assertRaises(DataError):
            with self.jobs.owned_commit(self.cid, job["id"], first["lease_token"]) as current:
                artifact.write_text("stale worker result", encoding="utf-8")
                current["stage"] = "incorrect commit"
        self.assertFalse(artifact.exists())
        with self.jobs.owned_commit(self.cid, job["id"], second["lease_token"]) as current:
            current.update(status="completed", package_id="pkg_123456789abc", stage="资料已保存")
        saved = self.jobs.read(self.cid, job["id"])
        self.assertEqual(saved["status"], "completed")
        self.assertIsNone(saved["worker_id"])
        self.assertNotIn("lease_token", saved)

    def test_failed_commit_does_not_save_job_but_keeps_atomic_checkpoint(self):
        job = self.jobs.create(self.cid)
        owned = self.jobs.claim(self.cid, job["id"], "first")
        before = self.jobs.read(self.cid, job["id"])
        checkpoint = self.root / "completed-chapter.md"
        with self.assertRaisesRegex(RuntimeError, "publish failed"):
            with self.jobs.owned_commit(self.cid, job["id"], owned["lease_token"]) as current:
                checkpoint.write_text("completed artifact", encoding="utf-8")
                current["stage"] = "not committed"
                current["progress"] = {"completed": 1}
                raise RuntimeError("publish failed")
        self.assertEqual(self.jobs.read(self.cid, job["id"]), before)
        self.assertEqual(checkpoint.read_text(encoding="utf-8"), "completed artifact")

    def test_owned_commit_cannot_modify_frozen_input(self):
        job = self.jobs.create(self.cid)
        owned = self.jobs.claim(self.cid, job["id"], "first")
        with self.assertRaisesRegex(DataError, "冻结输入"):
            with self.jobs.owned_commit(self.cid, job["id"], owned["lease_token"]) as current:
                current["source_ids"] = []
        self.assertEqual(self.jobs.read(self.cid, job["id"])["source_ids"], [self.sid])

    @unittest.skipUnless(os.name == "nt", "Windows extended-length spellings")
    def test_windows_device_prefix_normalization_preserves_boundary_check(self):
        base = Path(r"C:\study\courses\course_123456789abc")
        prefixed_inside = Path(r"\\?\C:\study\courses\course_123456789abc\jobs\.write.lock")
        with patch.object(self.service.store, "course_dir", return_value=base):
            with patch("pathlib.Path.resolve", return_value=prefixed_inside):
                result = self.service.store.contained(self.cid, "jobs/.write.lock")
                self.assertEqual(result, base / "jobs/.write.lock")
            with patch("pathlib.Path.resolve", return_value=Path(r"\\?\C:\outside\secret.txt")):
                with self.assertRaisesRegex(DataError, "超出"):
                    self.service.store.contained(self.cid, "jobs/.write.lock")

    @unittest.skipUnless(os.name == "nt", "Windows UNC spellings")
    def test_windows_unc_device_prefix_is_normalized_after_resolution(self):
        expanded = Path(r"\\?\UNC\server\share\course\jobs\.write.lock")
        with patch("pathlib.Path.resolve", return_value=expanded) as resolve:
            normal = resolved_path(r"\\server\share\course\jobs\.write.lock")
        self.assertEqual(normal, Path(r"\\server\share\course\jobs\.write.lock"))
        resolve.assert_called_once()

    def test_paths_sources_and_tokens_cannot_cross_courses(self):
        with self.assertRaises(DataError):
            self.jobs.create(self.other, source_ids=[self.sid])
        job = self.jobs.create(self.cid)
        with self.assertRaises(DataError):
            self.jobs.read(self.other, job["id"])
        for bad in ("../../outside", "job_../bad", "job_123456789abc/../x"):
            with self.assertRaises(DataError):
                self.jobs.read(self.cid, bad)
        claimed = self.jobs.claim(self.cid, job["id"], "first")
        with self.assertRaises(DataError):
            self.jobs.update(self.cid, job["id"], "wrong token", status="completed",
                             package_id="pkg_123456789abc")
        with self.assertRaises(DataError):
            self.jobs.heartbeat(self.cid, job["id"], "非执行凭据")
        self.assertEqual(self.jobs.read(self.cid, job["id"])["status"], "running")
        with self.assertRaises(DataError):
            self.jobs.complete(self.cid, job["id"], claimed["lease_token"], "../../outside")

    def test_corrupt_job_is_not_silently_replaced(self):
        job = self.jobs.create(self.cid)
        path = self.service.store.contained(self.cid, f"jobs/{job['id']}/job.json")
        path.write_text('{"broken":', encoding="utf-8")
        with self.assertRaises(DataError):
            self.jobs.read(self.cid, job["id"])
        self.assertEqual(path.read_text(encoding="utf-8"), '{"broken":')

    def test_non_json_settings_and_nonfinite_progress_are_rejected(self):
        with self.assertRaises(DataError):
            self.jobs.create(self.cid, settings={"bad": float("nan")})
        job = self.jobs.create(self.cid)
        claimed = self.jobs.claim(self.cid, job["id"], "first")
        with self.assertRaises(DataError):
            self.jobs.update(self.cid, job["id"], claimed["lease_token"],
                             progress={"completed": float("inf")})
        self.assertNotIn("completed", self.jobs.read(self.cid, job["id"])["progress"])

    def test_progress_percent_is_real_and_partial_never_looks_complete(self):
        job = self.jobs.create(self.cid)
        self.assertEqual(job["progress"]["percent"], 0)
        self.assertIsNone(job["eta_seconds"])
        owner = self.jobs.claim(self.cid, job["id"], "progress-worker")
        token = owner["lease_token"]
        checkpoint = self.jobs.update(self.cid, job["id"], token,
                                      progress={"completed": 1, "total": 4, "unit": "章节", "phase": "讲解"})
        self.assertEqual(checkpoint["progress"]["percent"], 25)
        self.assertIsNone(checkpoint["progress"]["eta_seconds"])
        paused = self.jobs.release(self.cid, job["id"], token)
        self.assertEqual(paused["progress"]["percent"], 25)
        self.assertIsNone(paused["estimated_completion_at"])
        new_owner = self.jobs.claim(self.cid, job["id"], "next-worker")
        self.jobs.update(self.cid, job["id"], new_owner["lease_token"], progress={"completed": 4, "total": 4})
        partial = self.jobs.complete(self.cid, job["id"], new_owner["lease_token"], "pkg_123456789abc", partial=True)
        self.assertLess(partial["progress"]["percent"], 100)
        self.assertIsNone(partial["eta_seconds"])
        self.jobs.resume(self.cid, job["id"])
        final_owner = self.jobs.claim(self.cid, job["id"], "final-worker")
        complete = self.jobs.complete(self.cid, job["id"], final_owner["lease_token"], "pkg_123456789abc")
        self.assertEqual(complete["progress"]["percent"], 100)

    def test_eta_requires_real_stable_completed_unit_timings_and_stops_on_pause(self):
        self.jobs = JobManager(self.service, lease_seconds=1000, clock=lambda: self.time)
        job = self.jobs.create(self.cid)
        token = self.jobs.claim(self.cid, job["id"], "worker")["lease_token"]
        self.jobs.update(self.cid, job["id"], token, progress={"completed": 0, "total": 4, "unit": "章", "phase": "整理"})
        self.time += 10
        first = self.jobs.update(self.cid, job["id"], token, progress={"completed": 1, "total": 4, "unit": "章", "phase": "整理"})
        self.assertIsNone(first["eta_seconds"])
        self.time += 12
        second = self.jobs.update(self.cid, job["id"], token, progress={"completed": 2, "total": 4, "unit": "章", "phase": "整理"})
        self.assertEqual(second["eta_seconds"], 22)
        self.assertEqual(second["progress"]["percent"], 50)
        self.assertTrue(second["estimated_completion_at"])
        self.assertNotIn("_progress_clock", second)
        self.assertNotIn("lease_token", second)
        paused = self.jobs.release(self.cid, job["id"], token)
        self.assertIsNone(paused["eta_seconds"])
        self.time += 100
        new_owner = self.jobs.claim(self.cid, job["id"], "resumed")
        self.assertEqual(new_owner["eta_seconds"], 22)
        self.assertNotIn(100, new_owner["progress"].get("samples", []))
        self.time += 30
        self.assertIsNone(self.jobs.read(self.cid, job["id"])["eta_seconds"])

    def test_eta_discards_unstable_or_different_phase_samples(self):
        job = self.jobs.create(self.cid)
        token = self.jobs.claim(self.cid, job["id"], "worker")["lease_token"]
        unstable = self.jobs.update(self.cid, job["id"], token,
                                    progress={"completed": 2, "total": 5, "phase": "OCR", "samples": [1, 100]})
        self.assertIsNone(unstable["eta_seconds"])
        changed = self.jobs.update(self.cid, job["id"], token,
                                  progress={"completed": 1, "total": 5, "phase": "讲解", "samples": [
                                      {"seconds": 10, "phase": "OCR"}, {"seconds": 12, "phase": "OCR"}]})
        self.assertIsNone(changed["eta_seconds"])

    def test_structured_errors_survive_update_and_commit_without_leaking_credentials(self):
        job = self.jobs.create(self.cid)
        token = self.jobs.claim(self.cid, job["id"], "worker")["lease_token"]
        details = {"stage": "OCR", "source_id": self.sid, "filename": "课件.md",
                   "locator": {"page": 3}, "message": "第3页无法识别，其他页已保存"}
        failed = self.jobs.update(self.cid, job["id"], token, progress={"completed": 2, "total": 3},
                                  status="failed", error_details=details)
        self.assertEqual(failed["error"], details["message"])
        self.assertEqual(failed["error_details"], details)
        self.assertLess(failed["progress"]["percent"], 100)
        self.assertNotIn(token, json.dumps(failed))
        self.jobs.resume(self.cid, job["id"])
        next_owner = self.jobs.claim(self.cid, job["id"], "next")
        with self.jobs.owned_commit(self.cid, job["id"], next_owner["lease_token"]) as current:
            current.update(status="failed", error_details={"stage": "核对", "message": "参考答案需要核对"})
        saved = self.jobs.read(self.cid, job["id"])
        self.assertEqual(saved["error"], "参考答案需要核对")
        self.assertEqual(saved["error_details"]["stage"], "核对")

    def test_error_details_reject_foreign_sources_and_secret_fields(self):
        job = self.jobs.create(self.cid)
        token = self.jobs.claim(self.cid, job["id"], "worker")["lease_token"]
        for details in ({"message": "wrong course", "source_id": "foreign"},
                        {"message": "invalid", "lease_token": token},
                        {"message": "invalid", "locator": [1, 2]}, {"stage": "missing message"}):
            with self.assertRaises(DataError):
                self.jobs.update(self.cid, job["id"], token, error_details=details)
        self.assertIsNone(self.jobs.read(self.cid, job["id"])["error_details"])


if __name__ == "__main__":
    unittest.main()
