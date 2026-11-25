"""Exercise the local Agent subprocess boundary without invoking any model."""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from good_student.jobs import JobManager
from good_student.runner import AutoRunner
from good_student.service import StudentService
from good_student.storage import atomic_json


class _InputPipe:
    def __init__(self, callback):
        self.callback = callback
        self.value = b""
        self.closed = False

    def write(self, value):
        self.value += value
        if self.callback:
            self.callback(value.decode("utf-8"))
        return len(value)

    def close(self):
        self.closed = True


class _Child:
    def __init__(self, callback=None, code=0):
        self.stdin = _InputPipe(callback)
        self.code = code
        self.terminated = False

    def poll(self):
        return self.code

    def wait(self, timeout=None):
        return self.code

    def terminate(self):
        self.terminated = True
        self.code = -15


class AutoRunnerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = StudentService(self.root / "data")
        self.cid = self.service.store.create("后台执行器测试", "pass")["course"]["id"]
        source = self.root / "自写材料.md"
        source.write_text("# 栈\n这是独立测试材料，栈遵循后进先出原则。", encoding="utf-8")
        self.sid = self.service.import_material(self.cid, source, "material")["source"]["id"]
        self.runner = AutoRunner(self.service, executable="codex-test.exe", poll_seconds=0.01)
        self.time = 1_800_000_000.0
        self.runner.jobs = JobManager(self.service, clock=lambda: self.time)

    def tearDown(self):
        self.runner.stop()
        self.temp.cleanup()

    def job(self, kind="question", authorize=True):
        job = self.runner.jobs.create(self.cid, [self.sid], kind=kind, request="用本课程资料完成本次任务")
        if authorize:
            from good_student.consents import ConsentManager
            ConsentManager(self.service).grant(self.cid, job["id"])
        return job

    def marker(self, jid):
        return self.service.store.contained(self.cid, f"jobs/{jid}/runner_attempt.json")

    @staticmethod
    def token_from(prompt):
        match = re.search(r"直接使用租约\s+(\S+)，", prompt)
        if not match:
            raise AssertionError("child prompt did not receive its owned lease")
        return match[1]

    def test_missing_cli_never_starts_an_agent(self):
        with patch("good_student.runner.shutil.which", return_value=None), patch("good_student.runner.subprocess.run") as run:
            runner = AutoRunner(self.service)
            self.assertFalse(runner.probe())
        run.assert_not_called()
        self.assertFalse(runner.snapshot()["available"])
        self.assertEqual(runner.snapshot()["status"], "unavailable")
        self.assertIn("未找到", runner.snapshot()["message"])

    def test_not_logged_in_reports_unavailable_without_changing_credentials(self):
        output = subprocess.CompletedProcess([], 1, stdout="", stderr="Not logged in")
        with patch("good_student.runner.subprocess.run", return_value=output) as run:
            self.assertFalse(self.runner.probe())
        command = run.call_args.args[0]
        self.assertEqual(command, ["codex-test.exe", "login", "status"])
        self.assertEqual(run.call_args.kwargs["timeout"], 15)
        self.assertFalse(self.runner.snapshot()["available"])
        self.assertIn("Not logged in", self.runner.snapshot()["message"])

    def test_available_cli_is_only_probed_with_login_status(self):
        output = subprocess.CompletedProcess([], 0, stdout="Logged in", stderr="")
        with patch("good_student.runner.subprocess.run", return_value=output) as run:
            self.assertTrue(self.runner.probe())
        self.assertEqual(run.call_args.args[0][1:], ["login", "status"])
        self.assertTrue(self.runner.snapshot()["available"])
        self.assertEqual(self.runner.snapshot()["status"], "idle")

    def test_probe_timeout_is_a_connection_gap_not_a_running_job(self):
        with patch("good_student.runner.subprocess.run", side_effect=subprocess.TimeoutExpired(["codex", "login", "status"], 15)):
            self.assertFalse(self.runner.probe())
        self.assertEqual(self.runner.snapshot()["status"], "unavailable")
        self.assertIsNone(self.runner.snapshot()["active_job_id"])

    def test_loop_automatically_claims_a_queued_job_and_saves_child_completion(self):
        job = self.job()
        self.runner.state.update(available=True, status="idle")
        captured = {}
        def finish(prompt):
            captured["prompt"] = prompt
            token = self.token_from(prompt)
            response = self.service.store.contained(self.cid, f"jobs/{job['id']}/response.json")
            atomic_json(response, {"text": "这是假子进程保存的测试回答。"})
            self.runner.jobs.update(self.cid, job["id"], token, status="completed", stage="答疑已保存")
            self.runner.stop_event.set()
        child = _Child(finish)
        deadline = threading.Timer(2, self.runner.stop_event.set)
        deadline.start()
        try:
            with patch("good_student.runner.subprocess.Popen", return_value=child) as popen:
                self.runner._loop()
        finally:
            deadline.cancel()
        self.assertEqual(popen.call_count, 1)
        self.assertEqual(self.runner.jobs.read(self.cid, job["id"])["status"], "completed")
        self.assertEqual(json.loads(self.marker(job["id"]).read_text(encoding="utf-8"))["status"], "completed")
        self.assertIn("不能等待逐章确认", captured["prompt"])
        self.assertEqual(self.runner.snapshot()["status"], "idle")
        self.assertIsNone(self.runner.snapshot()["active_job_id"])
        self.assertIsNone(self.runner.child)
        self.assertTrue(child.stdin.closed)

    def test_waiting_job_is_not_retried_without_an_explicit_change(self):
        job = self.job()
        claim = self.runner.jobs.claim(self.cid, job["id"], "previous-child")
        waiting = self.runner.jobs.release(self.cid, job["id"], claim["lease_token"], "需要核对资料")
        atomic_json(self.marker(job["id"]), {"job_updated_at": waiting["updated_at"], "status": "waiting_agent"})
        self.assertIsNone(self.runner._pending())
        self.time += 60
        self.assertIsNone(self.runner._pending())
        self.assertEqual(self.runner.jobs.read(self.cid, job["id"])["status"], "waiting_agent")

    def test_failed_job_is_retried_after_explicit_resume_updates_its_checkpoint(self):
        job = self.job(kind="build")
        child = _Child(code=1)
        with patch("good_student.runner.subprocess.Popen", return_value=child):
            self.runner.run_job(job)
        failed = self.runner.jobs.read(self.cid, job["id"])
        self.assertEqual(failed["status"], "failed")
        self.assertIsNone(self.runner._pending())
        before = json.loads(self.marker(job["id"]).read_text(encoding="utf-8"))["job_updated_at"]
        self.time += 1
        resumed = self.runner.jobs.resume(self.cid, job["id"])
        self.assertNotEqual(before, resumed["updated_at"])
        self.assertEqual(self.runner._pending()["id"], job["id"])

    def test_exit_zero_without_publishing_never_becomes_complete(self):
        job = self.job(kind="build")
        with patch("good_student.runner.subprocess.Popen", return_value=_Child(code=0)):
            self.runner.run_job(job)
        final = self.runner.jobs.read(self.cid, job["id"])
        self.assertEqual(final["status"], "failed")
        self.assertIn("尚未发布", final["error"])
        self.assertIsNone(final["package_id"])
        self.assertEqual(json.loads(self.marker(job["id"]).read_text(encoding="utf-8"))["job_updated_at"], final["updated_at"])

    def test_cli_json_failure_is_saved_with_a_readable_reason_and_no_lease_in_ui_error(self):
        job = self.job()
        captured = {}
        def start(command, **kwargs):
            captured["command"] = command
            def emit_error(prompt):
                token = self.token_from(prompt)
                captured["token"] = token
                event = {"type": "turn.failed", "error": {"message": "模型暂不可用；执行凭据=" + token}}
                kwargs["stdout"].write((json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8"))
            return _Child(emit_error, code=1)
        with patch("good_student.runner.subprocess.Popen", side_effect=start):
            self.runner.run_job(job)
        final = self.runner.jobs.read(self.cid, job["id"])
        self.assertEqual(final["status"], "failed")
        self.assertIn("模型暂不可用", final["error"])
        self.assertIn("[执行凭据]", final["error"])
        self.assertNotIn(captured["token"], json.dumps(final, ensure_ascii=False))
        self.assertNotIn(captured["token"], json.dumps(self.runner.snapshot(), ensure_ascii=False))
        self.assertNotIn("lease_token", final)
        self.assertEqual(final["error_details"]["filename"], "runner.log")

    def test_child_uses_workspace_write_without_configuration_or_credential_bypass_flags(self):
        job = self.job()
        with patch("good_student.runner.subprocess.Popen", return_value=_Child(code=1)) as popen:
            self.runner.run_job(job)
        command = popen.call_args.args[0]
        self.assertEqual(command[command.index("--sandbox") + 1], "workspace-write")
        forbidden = {"--dangerously-bypass-approvals-and-sandbox", "--yolo", "--config", "-c", "--profile",
                     "--full-auto", "danger-full-access"}
        self.assertFalse(forbidden.intersection(command))
        self.assertNotIn("login", command)
        self.assertEqual(command[-1], "-")
        self.assertEqual(popen.call_args.kwargs["stdin"], subprocess.PIPE)

    def test_runner_working_directory_contains_the_selected_data_workspace(self):
        job = self.job()
        with patch("good_student.runner.subprocess.Popen", return_value=_Child(code=1)) as popen:
            self.runner.run_job(job)
        command = popen.call_args.args[0]
        cwd = Path(command[command.index("--cd") + 1]).resolve()
        course = self.service.store.course_dir(self.cid).resolve()
        self.assertEqual(cwd, self.service.store.root.parent)
        self.assertTrue(course.is_relative_to(cwd), "workspace-write must include the course data even when the skill is installed elsewhere")

    def test_unapproved_materials_never_start_a_child_or_enter_the_automatic_queue(self):
        job = self.job(authorize=False)
        self.assertIsNone(self.runner._pending())
        with patch("good_student.runner.subprocess.Popen") as popen:
            self.runner.run_job(job)
        popen.assert_not_called()
        self.assertEqual(self.runner.jobs.read(self.cid, job["id"])["status"], "queued")
        self.assertEqual(self.runner.snapshot()["status"], "awaiting_authorization")

    def test_consent_is_limited_to_this_course_and_the_selected_source_versions(self):
        self.job()
        other = self.service.store.create("另一门课", "pass")["course"]["id"]
        original_path = self.root / "自写材料.md"
        same_bytes = self.service.import_material(other, original_path, "material")["source"]["id"]
        other_job = self.runner.jobs.create(other, [same_bytes], kind="question", request="不能沿用另一科的授权")
        self.assertFalse(self.runner.is_authorized(other_job))
        added = self.root / "新资料.md"
        added.write_text("# 队列\n这是一份授权之后新增的不同材料。", encoding="utf-8")
        new_sid = self.service.import_material(self.cid, added, "material")["source"]["id"]
        new_job = self.runner.jobs.create(self.cid, [self.sid, new_sid], kind="question", request="不能沿用旧资料授权")
        self.assertFalse(self.runner.is_authorized(new_job))
        with patch("good_student.runner.subprocess.Popen") as popen:
            self.runner.run_job(new_job)
        popen.assert_not_called()

    def test_prompt_requires_read_only_materials_untrusted_content_and_complete_course_output(self):
        prompt = self.runner._prompt(self.job(kind="build"), "placeholder-lease")
        for requirement in ("原始资料只读", "不得修改项目源码", "宿主配置", "凭据", "不得创建嵌套执行器",
                            "不可信的学习内容", "只处理下面冻结任务范围", "读完所有选入资料", "不能只写目录",
                            "每章准备2基础1应用", "chapter-save", "overview-save", "最后publish",
                            "不可伪造complete", "不能静默跳过材料"):
            self.assertIn(requirement, prompt)


if __name__ == "__main__":
    unittest.main()
