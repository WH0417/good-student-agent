"""Build a self-contained skill bundle without installing anything globally."""
from pathlib import Path
import hashlib
import json
import re
import shutil
import sys
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def build(destination=None):
    base = Path(destination).resolve() if destination else ROOT / "dist"
    base.mkdir(parents=True, exist_ok=True)
    target = base / ("good-student-" + uuid.uuid4().hex[:8])
    shutil.copytree(ROOT / "skills" / "good-student", target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(ROOT / "good_student", target / "scripts" / "good_student",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(ROOT / "web", target / "scripts" / "web")
    shutil.copyfile(ROOT / "README.md", target / "使用说明.md")
    version = re.search(r'__version__\s*=\s*"([\d.]+)"', (ROOT / "good_student/__init__.py").read_text(encoding="utf-8")).group(1)
    archive = base / ("good-student-v" + version + ".zip")
    temporary = archive.with_suffix(".zip.tmp")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as stream:
        for path in sorted(target.rglob("*")):
            if path.is_file():
                stream.write(path, Path("good-student") / path.relative_to(target))
    temporary.replace(archive)
    (base / "latest.json").write_text(json.dumps({
        "skill_directory": str(target), "archive": str(archive),
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return target, archive


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    folder, archive = build()
    print(folder)
    print(archive)
