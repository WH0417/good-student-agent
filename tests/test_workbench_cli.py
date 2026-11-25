"""Run the Skill's real process entry point across a resumable generation job."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from good_student.jobs import JobManager
from good_student.packages import PackageManager
from good_student.service import StudentService


PROJECT = Path(__file__).resolve().parents[1]
ENTRY = PROJECT / "skills/good-student/scripts/workbench.py"


class WorkbenchBridgeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.service = StudentService(self.data)
        self.cid = self.service.store.create("数据结构", "pass")["course"]["id"]
        self.source = self.root / "完整课件.md"
        self.source.write_text("# 栈\n栈在同一端插入和删除，后进先出。\n# 队列\n队列在一端插入，另一端删除，先进先出。", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, *args, expect_ok=True):
        result = subprocess.run([sys.executable, "-X", "utf8", "-B", str(ENTRY),
                                 "--data-dir", str(self.data), *map(str, args)],
                                cwd=PROJECT, capture_output=True, text=True,
                                encoding="utf-8", timeout=25)
        output = result.stdout if expect_ok else result.stderr
        self.assertEqual(result.returncode, 0 if expect_ok else 2,
                         f"{args!r}\nstdout: {result.stdout}\nstderr: {result.stderr}")
        item = json.loads(output)
        self.assertEqual(item["ok"], expect_ok)
        return item["data"] if expect_ok else item["error"]

    def save_json(self, name, content):
        path = self.root / name
        path.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def owned(self, command, job, token, *args, **kwargs):
        return self.run_cli(command, "--course", self.cid, "--job", job,
                            "--lease", token, *args, **kwargs)

    def chapter(self, sid, title, principle):
        return {
            "summary": f"{title}是一种线性结构，其基本操作满足{principle}原则。",
            "explanation": f"理解{title}时先看插入与删除的位置，再据此判断元素的取出次序。该结构的典型次序为{principle}，可以用两个连续进入的元素检验。",
            "mindmap": f"flowchart TD\n A[{title}] --> B[{principle}]",
            "pitfalls": ["取出元素前应先检查该结构是否为空。"], "warnings": [],
            "examples": [{"title": "元素次序", "question": f"先后放入A、B，如何判断{title}中的取出顺序？",
                          "solution": f"先定位插入和删除端，再按{principle}逐个取出，不仅凭放入的时间猜测。"}],
            "source_refs": [{"source_id": sid, "locator": {"text_block": 1}}],
            "questions": [{"question": f"{title}采用哪种次序原则？", "answer": "A",
                           "options": [f"A. {principle}", "B. 随机取出"],
                           "explanation": f"{title}的操作端限制决定了{principle}，并不是随机取出。"}],
        }

    def test_ui_shaped_job_to_full_package_survives_release_and_reclaim(self):
        job = self.run_cli("job-create", "--course", self.cid, "--file", self.source,
                           "--request", "完整生成栈与队列的复习资料")
        jid, sid = job["id"], job["source_ids"][0]
        self.assertEqual(job["status"], "queued")
        waiting = self.run_cli("wait", "--course", self.cid, "--timeout", 0)
        self.assertEqual(waiting["pending"][0]["id"], jid)
        claim = self.run_cli("claim", "--course", self.cid, "--job", jid, "--worker", "first-session")
        first_token = claim["lease_token"]
        context = self.run_cli("context", "--course", self.cid, "--job", jid)
        self.assertEqual(context["job"]["source_ids"], [sid])
        self.assertNotIn("lease_token", context["job"])
        source_context = self.run_cli("context", "--course", self.cid, "--job", jid,
                                      "--source", sid, "--start", 0, "--limit", 20)
        self.assertTrue(source_context["material_data"])
        plan = self.save_json("章节计划.json", {"chapters": [
            {"id": "stack", "title": "栈", "source_ids": [sid], "question_count": 1},
            {"id": "queue", "title": "队列", "source_ids": [sid], "question_count": 1},
        ]})
        package = self.owned("package-create", jid, first_token, "--file", plan)
        pid = package["id"]
        first_chapter = self.save_json("栈.json", self.chapter(sid, "栈", "后进先出"))
        self.owned("chapter-save", jid, first_token, "--chapter", "stack", "--file", first_chapter)
        released = self.owned("release", jid, first_token, "--error", "模拟宿主会话结束")
        self.assertEqual(released["status"], "waiting_agent")
        self.assertEqual({k: released["progress"][k] for k in ("completed", "total")}, {"completed": 1, "total": 2})
        self.assertEqual(released["progress"]["percent"], 50)
        self.assertIsNone(released["progress"]["eta_seconds"])
        self.assertEqual(released["package_id"], pid)

        # A new CLI process restores a readable preview but not a false complete package.
        restored = self.run_cli("context", "--course", self.cid, "--job", jid)
        self.assertEqual(restored["package"]["manifest"]["id"], pid)
        self.assertEqual(len(restored["package"]["chapters"]), 1)
        self.assertIsNone(PackageManager(self.service).read(self.cid))
        second_claim = self.run_cli("claim", "--course", self.cid, "--job", jid, "--worker", "second-session")
        second_token = second_claim["lease_token"]
        self.assertNotEqual(first_token, second_token)
        reused = self.owned("package-create", jid, second_token, "--file", plan)
        self.assertEqual(reused["id"], pid)
        queue_file = self.save_json("队列.json", self.chapter(sid, "队列", "先进先出"))
        self.owned("chapter-save", jid, first_token, "--chapter", "queue", "--file", queue_file,
                    expect_ok=False)
        self.assertEqual(len(PackageManager(self.service).read(self.cid, pid)["chapters"]), 1)
        self.owned("chapter-save", jid, second_token, "--chapter", "queue", "--file", queue_file)
        overview = self.save_json("概览.json", {
            "overview": "本课程比较栈与队列的操作端和元素次序。先掌握各自的操作规则，再完成例题和独立练习。",
            "mindmap": "flowchart TD\n A[线性结构] --> B[栈]\n A --> C[队列]",
        })
        self.owned("overview-save", jid, second_token, "--file", overview)
        published = self.owned("publish", jid, second_token)
        self.assertEqual(published["status"], "complete")
        package = PackageManager(StudentService(self.data)).read(self.cid)
        self.assertEqual(package["manifest"]["id"], pid)
        self.assertEqual(len(package["chapters"]), 2)
        self.assertEqual(len(package["practice"]), 2)
        self.assertTrue(all(q["answer"] and q["explanation"] for q in package["practice"]))
        completed = JobManager(self.service).read(self.cid, jid)
        self.assertEqual(completed["status"], "completed")
        self.assertNotIn("lease_token", completed)
        self.assertFalse(self.service.store.read(self.cid)["attempts"])

    def test_question_response_is_persisted_and_does_not_need_package(self):
        job = self.run_cli("job-create", "--course", self.cid, "--kind", "question",
                           "--request", "为什么栈是后进先出？")
        jid = job["id"]
        owned = self.run_cli("claim", "--course", self.cid, "--job", jid, "--worker", "answer-session")
        response = self.root / "答疑.md"
        response.write_text("栈在同一端插入和删除，后进入的元素位于顶端，因此先被取出。", encoding="utf-8")
        self.owned("respond", jid, "incorrect-lease", "--file", response, expect_ok=False)
        response_path = self.service.store.contained(self.cid, f"jobs/{jid}/response.json")
        self.assertFalse(response_path.exists())
        saved = self.owned("respond", jid, owned["lease_token"], "--file", response)
        self.assertEqual(saved["text"], response.read_text(encoding="utf-8"))
        self.assertEqual(json.loads(response_path.read_text(encoding="utf-8"))["job_id"], jid)
        durable_job = JobManager(StudentService(self.data)).read(self.cid, jid)
        self.assertEqual(durable_job["status"], "completed")
        self.assertIsNone(durable_job["package_id"])

    def test_partial_publication_can_resume_same_job_and_package(self):
        self.source.write_text("# 栈\n栈在同一端插入和删除，后进先出。", encoding="utf-8")
        job = self.run_cli("job-create", "--course", self.cid, "--file", self.source)
        jid, sid = job["id"], job["source_ids"][0]
        owner = self.run_cli("claim", "--course", self.cid, "--job", jid, "--worker", "first")
        token = owner["lease_token"]
        plan = self.save_json("单章计划.json", [{"id": "stack", "title": "栈", "source_ids": [sid], "question_count": 1}])
        package = self.owned("package-create", jid, token, "--file", plan)
        pid = package["id"]
        chapter = self.save_json("完整章节.json", self.chapter(sid, "栈", "后进先出"))
        self.owned("chapter-save", jid, token, "--chapter", "stack", "--file", chapter)
        partial = self.owned("publish", jid, token, "--partial")
        self.assertEqual(partial["status"], "partial")
        self.assertEqual(JobManager(self.service).read(self.cid, jid)["status"], "partial")
        original_question_count = len(self.service.store.read(self.cid)["questions"])
        settings = self.save_json("恢复任务.json", {"resume_job_id": jid})
        resumed = self.run_cli("job-create", "--course", self.cid, "--settings", settings)
        self.assertEqual(resumed["id"], jid)
        self.assertEqual(resumed["package_id"], pid)
        self.assertEqual({k: resumed["progress"][k] for k in ("completed", "total")}, {"completed": 1, "total": 1})
        self.assertGreaterEqual(resumed["progress"]["percent"], 0)
        self.assertLess(resumed["progress"]["percent"], 100)
        self.assertIsNone(resumed["progress"]["estimated_completion_at"])
        new_owner = self.run_cli("claim", "--course", self.cid, "--job", jid, "--worker", "second")
        new_token = new_owner["lease_token"]
        reused = self.owned("package-create", jid, new_token, "--file", plan)
        self.assertEqual(reused["id"], pid)
        self.assertEqual(reused["chapters"][0]["status"], "complete")
        overview = self.save_json("补齐概览.json", {"overview": "本课程先理解栈的操作端和后进先出原则，再通过例题核对元素次序，最后完成独立练习。",
                                                    "mindmap": "flowchart TD\n A[课程] --> B[栈]"})
        self.owned("overview-save", jid, new_token, "--file", overview)
        published = self.owned("publish", jid, new_token)
        self.assertEqual(published["status"], "complete")
        self.assertEqual(PackageManager(self.service).read(self.cid)["manifest"]["id"], pid)
        self.assertEqual(len(self.service.store.read(self.cid)["questions"]), original_question_count)

    def test_context_cannot_read_unselected_or_foreign_course_source(self):
        job = self.run_cli("job-create", "--course", self.cid, "--file", self.source)
        extra = self.root / "未选择.md"
        extra.write_text("# 图\n包含顶点与边。", encoding="utf-8")
        sid = self.service.import_material(self.cid, extra, "material")["source"]["id"]
        message = self.run_cli("context", "--course", self.cid, "--job", job["id"], "--source", sid,
                               expect_ok=False)
        self.assertIn("范围", message)
        other = self.service.store.create("高数", "high")["course"]["id"]
        self.run_cli("context", "--course", other, "--job", job["id"], expect_ok=False)


if __name__ == "__main__":
    unittest.main()
