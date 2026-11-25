import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools.build_skill import build


class BundleTest(unittest.TestCase):
    def test_standalone_bundle_runs_without_reference_repositories(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder, archive = build(root / "distribution")
            self.assertTrue(archive.is_file())
            self.assertTrue((folder / "scripts/good_student/vendor/coach/LICENSE").exists())
            self.assertTrue((folder / "scripts/web/app.js").is_file())
            self.assertTrue((folder / "scripts/web/vendor/mermaid.min.js").is_file())
            self.assertTrue((folder / "scripts/web/vendor/MERMAID-LICENSE").is_file())
            script = folder / "scripts/student.py"
            data = root / "independent-data"

            def run(*args):
                result = subprocess.run([sys.executable, "-B", str(script), "--data-dir", str(data), *args],
                                        cwd=root, capture_output=True, text=True, encoding="utf-8")
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(result.stdout)["data"]

            course = run("course-create", "--name", "独立技能测试", "--mode", "pass")
            lecture = root / "lecture.txt"
            lecture.write_text("老师：队列是考试重点。", encoding="utf-8")
            imported = run("import", "--course", course["id"], "--file", str(lecture), "--kind", "transcript")
            self.assertEqual(imported["new_focus"], 1)
            exported = run("export", "--course", course["id"])
            self.assertTrue(Path(exported["file_paths"]["exam_focus.md"]).is_file())
            self.assertIn("队列", Path(exported["file_paths"]["exam_focus.md"]).read_text(encoding="utf-8"))
            workbench = folder / "scripts/workbench.py"
            result = subprocess.run([sys.executable, "-B", str(workbench), "--data-dir", str(data), "jobs", "--course", course["id"]],
                                    cwd=root, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["data"]["pending"], [])


if __name__ == "__main__":
    unittest.main()
