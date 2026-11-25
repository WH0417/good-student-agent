"""Skill entry point; supports both the repository and a self-contained bundle."""
from pathlib import Path
import os
import subprocess
import sys

HERE = Path(__file__).resolve().parent
PACKAGE_ROOT = HERE if (HERE / "good_student").is_dir() else HERE.parents[2]
if not (PACKAGE_ROOT / "good_student").is_dir():
    raise SystemExit("缺少 good_student 运行包，请使用项目源码版或完整打包版技能")
sys.path.insert(0, str(PACKAGE_ROOT))

if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    directory = next((arg.split("=", 1)[1] for arg in sys.argv[1:] if arg.startswith("--data-dir=")), None)
    if directory is None and "--data-dir" in sys.argv:
        index = sys.argv.index("--data-dir")
        directory = sys.argv[index + 1] if index + 1 < len(sys.argv) else None
    if directory is None:
        if "--help" not in sys.argv and "-h" not in sys.argv:
            raise SystemExit("技能入口需要显式 --data-dir，防止将学习数据写入错误项目")
    else:
        workspace = Path(directory).expanduser().resolve().parent
        candidates = [workspace / name for name in (".runtime", ".good-student-runtime")]
        if not (HERE / "good_student").is_dir():
            candidates.append(PACKAGE_ROOT / ".runtime")
        for runtime in candidates:
            interpreter = runtime / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
            if interpreter.is_file():
                os.environ.setdefault("GOOD_STUDENT_MODELS_DIR", str(runtime / "models" / "asr"))
                os.environ.setdefault("GOOD_STUDENT_OCR_MODELS_DIR", str(runtime / "models" / "ocr"))
                same = os.path.normcase(os.path.abspath(sys.executable)) == os.path.normcase(str(interpreter.absolute()))
                if not same and not os.environ.get("_GOOD_STUDENT_REEXEC"):
                    environment = {**os.environ, "_GOOD_STUDENT_REEXEC": "1"}
                    raise SystemExit(subprocess.run([str(interpreter), "-X", "utf8", "-B", str(Path(__file__).resolve()),
                                                    *sys.argv[1:]], env=environment).returncode)
                break
    if "--workbench-entry" in sys.argv:
        sys.argv.remove("--workbench-entry")
        from good_student.workbench_cli import main
    else:
        from good_student.cli import main
    raise SystemExit(main())
