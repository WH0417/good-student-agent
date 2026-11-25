"""Exercise the local workbench through HTTP, including course isolation."""
from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from fastapi.testclient import TestClient

from good_student.storage import atomic_json
from good_student.filepicker import PickerError
from good_student.workbench import create_app


class WorkbenchTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.web = self.root / "web"
        self.web.mkdir()
        (self.web / "index.html").write_text("<!doctype html><title>好学生</title>", encoding="utf-8")
        self.app = create_app(self.data, "test-secret", web_dir=self.web, test_mode=True,
                              defaults={"mode": "pass"})
        self.client = self.make_client()
        self.cid = self.post("/api/courses", {"name": "数据库", "mode": "pass"})["id"]
        self.other = self.post("/api/courses", {"name": "高数", "mode": "high"})["id"]
        self.materials = self.root / "资料"
        self.materials.mkdir()
        self.source = self.materials / "栈.md"
        self.private_marker = "PRIVATE_FULL_SOURCE_7cdbf4"
        self.source.write_text("# 栈\n栈采用后进先出原则，最后进入的元素最先取出。\n" +
                               self.private_marker * 100, encoding="utf-8")
        (self.materials / "ignore.exe").write_bytes(b"not a material")

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def make_client(self):
        return TestClient(self.app, base_url="http://127.0.0.1:8765",
                          headers={"x-session-token": "test-secret"})

    def post(self, url, payload):
        response = self.client.post(url, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertTrue(data["ok"], data)
        return data["data"]

    def get(self, url, **params):
        response = self.client.get(url, params=params)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["ok"], response.text)
        return response.json()["data"]

    def queue(self):
        return self.post("/api/jobs", {"course_id": self.cid, "files": [str(self.source)],
                                       "request": "生成全科复习包"})

    def prepare_package(self, original=False):
        service, manager, jobs = self.app.state.service, self.app.state.packages, self.app.state.jobs
        job = self.queue()
        sid = job["source_ids"][0]
        questions = [
            {"question": "栈采用什么原则？", "answer": "A",
             "options": ["A. 后进先出", "B. 先进先出"],
             "explanation": "只在栈顶进行操作，最后进入的元素最先取出。"},
            {"question": "解释栈为何采用后进先出原则。", "answer": "栈只允许在栈顶插入和删除，后插入者先删除。",
             "explanation": "插入使新元素成为栈顶；删除从同一个栈顶执行，因而后进先出。"},
        ]
        if original:
            with service.store.transaction(self.cid) as state:
                state["questions"]["q_original_fixture"] = {
                    "id": "q_original_fixture", "question": "下列哪项是栈的操作原则？",
                    "origin": "exercise", "source_id": sid, "source_ids": [sid],
                    "options": ["A. 后进先出", "B. 先进先出"], "answer": None,
                    "explanation": None, "topic_ids": [], "type": "choice",
                }
            questions = [{"question_id": "q_original_fixture", "answer": "A",
                          "explanation": "最后进入栈顶的元素最先离开。"}]
        plan = [{"id": "stack", "title": "栈", "source_ids": [sid], "question_count": len(questions)}]
        manifest = manager.create(self.cid, job, plan)
        pid = manifest["id"]
        chapter = manager.write_chapter(self.cid, pid, "stack", {
            "summary": "栈是一种在同一端进行插入和删除的线性结构。",
            "explanation": "栈只在栈顶进行操作，最后进入的元素最先取出。学习时先掌握后进先出原则，再理解入栈和出栈步骤。",
            "mindmap": "flowchart TD\n A[栈] --> B[后进先出]\n A --> C[栈顶操作]",
            "examples": [{"title": "连续入栈", "question": "依次将A、B入栈，再出栈得到谁？",
                          "solution": "B最后进入，位于栈顶，因此先取出B；A仍留在栈内。"}],
            "pitfalls": ["空栈不能继续出栈。"], "warnings": [],
            "source_refs": [{"source_id": sid, "locator": {"text_block": 1}}],
            "questions": questions,
        })
        manager.write_overview(self.cid, pid, "先理解栈的后进先出原则，再学习基本操作并完成典型练习。",
                                "flowchart TD\n A[课程] --> B[栈]")
        manager.publish(self.cid, pid)
        owned = jobs.claim(self.cid, job["id"], "test-builder")
        jobs.complete(self.cid, job["id"], owned["lease_token"], pid)
        return pid, chapter["questions"]

    def test_health_is_public_but_api_requires_session_token(self):
        unauthenticated = TestClient(self.app, base_url="http://127.0.0.1:8765")
        try:
            health = unauthenticated.get("/api/health")
            self.assertEqual(health.status_code, 200)
            self.assertEqual(health.json()["data"]["service"], "good-student-workbench")
            self.assertNotIn("test-secret", health.text)
            self.assertEqual(unauthenticated.get("/api/courses").status_code, 401)
            self.assertEqual(unauthenticated.post("/api/scan", json={"path": str(self.materials)}).status_code, 401)
            self.assertEqual(unauthenticated.get("/api/courses", headers={"x-session-token": "wrong"}).status_code, 401)
        finally:
            unauthenticated.close()

    def test_host_origin_request_size_and_static_security_headers(self):
        self.assertEqual(self.client.get("/api/courses", headers={"host": "evil.example"}).status_code, 403)
        self.assertEqual(self.client.get("/api/courses", headers={"origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.client.get("/api/courses", headers={"origin": "http://127.0.0.1:8765"}).status_code, 200)
        self.assertEqual(self.client.post("/api/scan", content=b"{}",
                                         headers={"content-length": "1000001", "content-type": "application/json"}).status_code, 413)
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.headers["x-content-type-options"], "nosniff")
        self.assertEqual(page.headers["referrer-policy"], "no-referrer")
        self.assertIn("frame-ancestors 'none'", page.headers["content-security-policy"])
        self.assertNotIn("test-secret", page.text)

    def test_scan_import_and_frozen_build_resume_without_model_calls(self):
        scanned = self.post("/api/scan", {"path": str(self.materials)})
        self.assertEqual([file["name"] for file in scanned], ["栈.md"])
        with patch.object(self.app.state.service, "process_source", side_effect=AssertionError("must not run media processing in HTTP request")):
            job = self.queue()
        self.assertEqual(job["status"], "queued")
        self.assertEqual(len(job["source_ids"]), 1)
        self.assertNotIn("lease_token", job)
        old_count = len(self.app.state.service.store.read(self.cid)["sources"])
        new_source = self.materials / "新增.md"
        new_source.write_text("# 队列\n先进先出。", encoding="utf-8")
        reused = self.post("/api/jobs", {"course_id": self.cid, "files": [str(new_source)]})
        self.assertEqual(reused["id"], job["id"])
        self.assertTrue(reused["reused"])
        self.assertEqual(len(self.app.state.service.store.read(self.cid)["sources"]), old_count)
        resumed = create_app(self.data, "new-secret", web_dir=self.web, test_mode=True)
        self.assertEqual(resumed.state.jobs.list(self.cid)[0]["id"], job["id"])

    def test_pending_recording_is_queued_without_running_asr(self):
        audio = self.materials / "课堂.wav"
        audio.write_bytes(b"RIFFnot-a-real-wave")
        job = self.post("/api/jobs", {"course_id": self.cid, "files": [str(self.source), str(audio)]})
        state = self.app.state.service.store.read(self.cid)
        self.assertEqual(len(job["source_ids"]), 2)
        recordings = [source for source in state["sources"].values() if source["kind"] == "audio"]
        self.assertEqual(len(recordings), 1)
        self.assertEqual(recordings[0]["status"], "pending")

    def test_native_picker_success_and_cancellation_are_distinct_from_failure(self):
        selected = [str(self.source)]
        with patch("good_student.filepicker.choose", return_value=selected) as choose:
            result = self.post("/api/pick", {"kind": "files"})
            self.assertEqual(result["paths"], selected)
            choose.assert_called_once_with("files")
        with patch("good_student.filepicker.choose", return_value=[]) as choose:
            cancelled = self.post("/api/pick", {"kind": "directory"})
            self.assertEqual(cancelled["paths"], [])
            choose.assert_called_once_with("directory")
        with patch("good_student.filepicker.choose") as choose:
            self.assertEqual(self.client.post("/api/pick", json={"kind": "execute"}).status_code, 400)
            choose.assert_not_called()

    def test_native_picker_backend_failure_preserves_structured_cause(self):
        failure = PickerError("系统文件对话框初始化失败", backend="windows-forms",
                              detail="System.InvalidOperationException: actual backend cause")
        with patch("good_student.filepicker.choose", side_effect=failure):
            response = self.client.post("/api/pick", json={"kind": "audio"})
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], str(failure))
        self.assertEqual(body["error_details"], failure.details)

    def test_explicit_file_kind_is_applied_and_invalid_kind_is_rejected(self):
        invalid = self.client.post("/api/jobs", json={
            "course_id": self.other, "files": [str(self.source)],
            "settings": {"file_kinds": {str(self.source): "teacher_prediction"}},
        })
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("资料类型", invalid.json()["error"])
        self.assertFalse(self.app.state.service.store.read(self.other)["sources"])
        self.assertFalse(self.app.state.jobs.list(self.other))
        job = self.post("/api/jobs", {
            "course_id": self.cid, "files": [str(self.source)],
            "settings": {"file_kinds": {str(self.source): "exercise"}},
        })
        state = self.app.state.service.store.read(self.cid)
        imported = state["sources"][job["source_ids"][0]]
        self.assertEqual(imported["filename"], "栈.md")
        self.assertEqual(imported["kind"], "exercise")
        self.assertEqual(job["settings"]["file_kinds"][str(self.source)], "exercise")

    def test_explicit_source_selection_keeps_pending_textbook_excluded(self):
        service = self.app.state.service
        selected = service.import_material(self.cid, self.source, "material")["source"]["id"]
        textbook = self.materials / "教材.md"
        textbook.write_text("# 补充教材\n该教材正在等待资料处理。", encoding="utf-8")
        excluded = service.import_material(self.cid, textbook, "material")["source"]["id"]
        with service.store.transaction(self.cid) as state:
            state["sources"][excluded]["status"] = "pending"
            state["sources"][excluded]["pages"] = []
            state["sources"][excluded]["segments"] = []
        # An explicit empty selection must not silently mean every archived file.
        empty = self.client.post("/api/jobs", json={"course_id": self.cid, "files": [], "source_ids": []})
        self.assertEqual(empty.status_code, 400)
        self.assertIn("选择课程资料", empty.json()["error"])
        self.assertFalse(self.app.state.jobs.list(self.cid))
        job = self.post("/api/jobs", {"course_id": self.cid, "files": [], "source_ids": [selected]})
        self.assertEqual(job["source_ids"], [selected])
        self.assertEqual([item["id"] for item in job["input_sources"]], [selected])
        self.assertNotIn(excluded, job["source_ids"])
        archived = service.store.read(self.cid)["sources"]
        self.assertIn(excluded, archived)
        self.assertEqual(archived[excluded]["status"], "pending")

    def test_job_and_course_responses_do_not_disclose_leases_or_raw_source(self):
        job = self.queue()
        claimed = self.app.state.jobs.claim(self.cid, job["id"], "worker")
        listed = self.get("/api/jobs", course_id=self.cid)
        self.assertNotIn("lease_token", listed[0])
        course = self.get("/api/course", course_id=self.cid)
        serialized = json.dumps(course, ensure_ascii=False)
        self.assertNotIn(claimed["lease_token"], serialized)
        self.assertNotIn(self.private_marker, serialized)
        self.assertTrue(course["sources"])
        self.assertTrue(all("pages" not in item and "segments" not in item for item in course["sources"]))
        self.assertTrue(all("text" not in item for item in course["topics"]))

    def test_course_and_job_input_boundaries(self):
        job = self.queue()
        wrong_source = self.client.post("/api/jobs", json={"course_id": self.other,
                                                          "source_ids": job["source_ids"]})
        self.assertEqual(wrong_source.status_code, 400)
        self.assertEqual(self.client.get("/api/course", params={"course_id": "../../outside"}).status_code, 400)
        self.assertEqual(self.client.post("/api/jobs", json={"course_id": self.other, "files": []}).status_code, 400)
        self.assertEqual(self.client.post("/api/jobs", json={"course_id": self.other,
                                                            "files": [str(self.materials / "ignore.exe")]}).status_code, 400)
        self.assertEqual(self.client.post("/api/scan", json={"path": "relative/path"}).status_code, 400)
        self.assertFalse(self.app.state.jobs.list(self.other))

    def test_published_package_search_and_reread_are_local_and_do_not_reveal_answers(self):
        pid, questions = self.prepare_package()
        with patch.object(self.app.state.service, "process_source", side_effect=AssertionError("no reprocessing")):
            for _ in range(2):
                package = self.get("/api/package", course_id=self.cid)
                self.assertEqual(package["manifest"]["id"], pid)
                self.assertEqual(len(package["practice"]), 2)
                results = self.get("/api/search", course_id=self.cid, q="后进先出")
                self.assertEqual(results[0]["chapter_id"], "stack")
        state = self.app.state.service.store.read(self.cid)
        self.assertFalse(state["question_views"])
        self.assertFalse(state["attempts"])
        self.assertEqual(self.client.get("/api/package", params={"course_id": self.other, "package_id": pid}).status_code, 400)

    def test_reading_progress_is_user_report_and_survives_reopen(self):
        pid, _ = self.prepare_package()
        wb = self.post("/api/progress", {"course_id": self.cid, "package_id": pid,
                                         "chapter_id": "stack", "status": "self_assessed"})
        self.assertEqual(wb["reading"]["stack"]["basis"], "user_report")
        self.assertEqual(wb["reading"]["stack"]["status"], "self_assessed")
        self.assertEqual(wb["last_chapter"], "stack")
        for payload in ({"course_id": self.cid, "chapter_id": "unknown", "status": "read"},
                        {"course_id": self.other, "package_id": pid, "chapter_id": "stack", "status": "read"},
                        {"course_id": self.cid, "chapter_id": "stack", "status": "practice_passed"}):
            self.assertEqual(self.client.post("/api/progress", json=payload).status_code, 400)
        recovered = create_app(self.data, "fresh-secret", web_dir=self.web, test_mode=True)
        state = recovered.state.service.store.read(self.cid)
        self.assertEqual(state["workbench"]["last_chapter"], "stack")
        self.assertFalse(state["attempts"])
        self.assertFalse(state["progress"])

    def test_objective_answer_and_reveal_keep_independent_evidence(self):
        pid, questions = self.prepare_package()
        qid = questions[0]["id"]
        right = self.post("/api/answer", {"course_id": self.cid, "package_id": pid,
                                          "question_id": qid, "response": "A"})
        self.assertEqual(right["result"], "right")
        self.assertTrue(right["independent"])
        reference = self.post("/api/reveal", {"course_id": self.cid, "package_id": pid, "question_id": qid})
        self.assertEqual(reference["answer"], "A")
        self.assertTrue(reference["explanation"])
        repeated = self.post("/api/answer", {"course_id": self.cid, "package_id": pid,
                                             "question_id": qid, "response": "A"})
        self.assertFalse(repeated["independent"])
        self.assertTrue(repeated["answer_viewed"])
        self.assertEqual(self.client.post("/api/reveal", json={"course_id": self.other,
                                                              "package_id": pid, "question_id": qid}).status_code, 400)
        self.assertEqual(len(self.app.state.service.store.read(self.cid)["attempts"]), 2)

    def test_original_without_answer_uses_checked_package_reference(self):
        pid, questions = self.prepare_package(original=True)
        qid = questions[0]["id"]
        revealed = self.post("/api/reveal", {"course_id": self.cid, "package_id": pid, "question_id": qid})
        self.assertEqual(revealed["answer"], "A")
        self.assertEqual(revealed["answer_origin"], "agent_generated")
        attempt = self.post("/api/answer", {"course_id": self.cid, "package_id": pid,
                                            "question_id": qid, "response": "A"})
        self.assertEqual(attempt["result"], "right")
        self.assertFalse(attempt["independent"])
        self.assertIsNone(self.app.state.service.store.read(self.cid)["questions"][qid]["answer"])

    def test_original_question_can_request_answer_challenge_without_fake_resolution(self):
        pid, questions = self.prepare_package(original=True)
        qid = questions[0]["id"]
        payload = {"course_id": self.cid, "package_id": pid, "question_id": qid,
                   "reason": "请重新核对选项和课件中的操作原则是否一致。"}
        job = self.post("/api/challenge", payload)
        self.assertEqual(job["kind"], "challenge")
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["settings"]["package_id"], pid)
        self.assertEqual(job["settings"]["question_id"], qid)
        self.assertEqual(job["settings"]["reason"], payload["reason"])
        self.assertTrue(job["settings"]["reference_version"])
        self.assertNotIn("lease_token", job)
        duplicate = self.post("/api/challenge", payload)
        self.assertEqual(duplicate["id"], job["id"])
        self.assertTrue(duplicate["reused"])
        wrong_course = self.client.post("/api/challenge", json={**payload, "course_id": self.other})
        self.assertEqual(wrong_course.status_code, 400)
        state = self.app.state.service.store.read(self.cid)
        self.assertFalse(state["attempts"])
        self.assertIsNone(state["questions"][qid]["answer"])

    def test_open_answer_creates_real_pending_review_job(self):
        pid, questions = self.prepare_package()
        attempt = self.post("/api/answer", {"course_id": self.cid, "package_id": pid,
                                            "question_id": questions[1]["id"], "response": "我认为和只在栈顶操作有关。"})
        self.assertEqual(attempt["result"], "unsure")
        self.assertEqual(attempt["review_job"]["kind"], "review")
        self.assertEqual(attempt["review_job"]["status"], "queued")
        self.assertEqual(attempt["review_job"]["settings"]["attempt_id"], attempt["id"])

    def test_followup_response_is_saved_and_visible_without_model(self):
        self.queue()
        job = self.post("/api/jobs", {"course_id": self.cid, "kind": "question", "request": "举一个例子"})
        owned = self.app.state.jobs.claim(self.cid, job["id"], "answer-worker")
        response = {"job_id": job["id"], "course_id": self.cid,
                    "answer": "依次把A、B入栈时，第一次出栈得到B。"}
        atomic_json(self.app.state.service.store.contained(self.cid, f"jobs/{job['id']}/response.json"), response)
        self.app.state.jobs.update(self.cid, job["id"], owned["lease_token"],
                                   status="completed", stage="答疑已保存")
        course = self.get("/api/course", course_id=self.cid)
        self.assertEqual(course["responses"], [response])
        self.assertEqual(self.app.state.jobs.read(self.cid, job["id"])["status"], "completed")

    def test_focus_and_mode_changes_preserve_old_package_snapshot(self):
        pid, _ = self.prepare_package()
        focus = self.post("/api/focus", {"course_id": self.cid, "title": "栈", "note": "我指定栈为重点"})
        self.assertEqual(focus["source_type"], "user_designated")
        self.post("/api/mode", {"course_id": self.cid, "mode": "high"})
        self.assertEqual(self.get("/api/course", course_id=self.cid)["course"]["mode"], "high")
        self.assertEqual(self.get("/api/package", course_id=self.cid, package_id=pid)["manifest"]["mode"], "pass")

    def test_partial_build_resume_from_ui_preserves_package_and_completed_chapter(self):
        old_pid, _ = self.prepare_package()
        manager, jobs = self.app.state.packages, self.app.state.jobs
        old_chapter = manager.read(self.cid, old_pid)["chapters"][0]
        job = self.post("/api/jobs", {"course_id": self.cid})
        sid = job["source_ids"][0]
        manifest = manager.create(self.cid, job, [
            {"id": "stack", "title": "栈", "source_ids": [sid], "question_count": 2},
            {"id": "next", "title": "待补充章节", "source_ids": [sid], "question_count": 1},
        ])
        pid = manifest["id"]
        manager.write_chapter(self.cid, pid, "stack", old_chapter)
        manager.publish(self.cid, pid, partial=True)
        owned = jobs.claim(self.cid, job["id"], "partial-builder")
        jobs.update(self.cid, job["id"], owned["lease_token"], progress={"completed": 1, "total": 2})
        jobs.complete(self.cid, job["id"], owned["lease_token"], pid, partial=True)
        resumed = self.post("/api/jobs", {"course_id": self.cid,
                                         "settings": {"resume_job_id": job["id"]}})
        self.assertEqual(resumed["id"], job["id"])
        self.assertEqual(resumed["status"], "waiting_agent")
        self.assertEqual(resumed["package_id"], pid)
        self.assertEqual(resumed["source_ids"], job["source_ids"])
        self.assertEqual({k: resumed["progress"][k] for k in ("completed", "total")}, {"completed": 1, "total": 2})
        self.assertEqual(resumed["progress"]["percent"], 50)
        self.assertIsNone(resumed["progress"]["eta_seconds"])
        preview = self.get("/api/package", course_id=self.cid, package_id=pid, preview=True)
        self.assertEqual([chapter["id"] for chapter in preview["chapters"]], ["stack"])
        self.assertEqual(self.get("/api/package", course_id=self.cid)["manifest"]["id"], old_pid)

    def test_export_download_and_cross_course_file_boundary(self):
        pid, _ = self.prepare_package()
        exported = self.post("/api/export", {"course_id": self.cid, "package_id": pid})
        download = self.client.get(exported["download_url"])
        self.assertEqual(download.status_code, 200)
        with zipfile.ZipFile(BytesIO(download.content)) as archive:
            self.assertIn("overview.md", archive.namelist())
            self.assertIn("chapters/stack.md", archive.namelist())
            self.assertIn("practice.md", archive.namelist())
        relative = Path(exported["directory"]).relative_to(self.app.state.service.store.course_dir(self.cid)).as_posix()
        denied = [
            {"course_id": self.cid, "path": "state.json"},
            {"course_id": self.cid, "path": "exports/../../outside.txt"},
            {"course_id": self.other, "path": f"{relative}/复习资料.zip"},
            {"course_id": self.cid, "path": str(self.source)},
        ]
        for params in denied:
            self.assertEqual(self.client.get("/api/download", params=params).status_code, 400)

    def test_browser_file_selection_archives_only_to_local_workspace(self):
        raw = '这是浏览器选择后保存在本地的课程资料。'.encode('utf-8')
        response = self.client.post('/api/upload', params={'course_id': self.cid, 'filename': '选择的资料.md'},
                                    content=raw, headers={'content-type': 'application/octet-stream'})
        self.assertEqual(response.status_code, 200, response.text)
        saved = response.json()['data']
        self.assertEqual(Path(saved['path']).read_bytes(), raw)
        self.assertTrue(Path(saved['path']).resolve().is_relative_to(self.data.resolve()))
        self.assertFalse(self.app.state.service.store.read(self.cid)['sources'])
        unsupported = self.client.post('/api/upload', params={'course_id': self.cid, 'filename': 'bad.exe'}, content=b'x')
        self.assertEqual(unsupported.status_code, 400)
        empty = self.client.post('/api/upload', params={'course_id': self.cid, 'filename': 'empty.md'}, content=b'')
        self.assertEqual(empty.status_code, 400)


if __name__ == "__main__":
    unittest.main()
