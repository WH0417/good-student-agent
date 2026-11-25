import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CliTest(unittest.TestCase):
    def test_fresh_processes_recover_the_explicit_course(self):
        with tempfile.TemporaryDirectory() as directory:
            def run(*args):
                result = subprocess.run([sys.executable, "-B", "-m", "good_student", "--data-dir", directory, *args],
                                        cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(result.stdout)["data"]
            course = run("course-create", "--name", "高数", "--mode", "pass")
            run("focus-add", "--course", course["id"], "--title", "极限", "--note", "用户指定极限")
            recovered = run("resume", "--course", course["id"])
            self.assertEqual(recovered["active_focus"], 1)
            self.assertEqual(recovered["course"]["passing_score"], 60)
            run("export", "--course", course["id"])

    def test_skill_wrapper_requires_explicit_data_directory(self):
        result = subprocess.run([sys.executable, "-B", str(ROOT / "skills/good-student/scripts/student.py"), "course-list"],
                                cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--data-dir", result.stderr)


if __name__ == "__main__":
    unittest.main()
