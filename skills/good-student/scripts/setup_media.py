"""Agent-only one-time setup, confined to the user's chosen project."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import venv

PACKAGES = ["faster-whisper>=1.2,<1.3", "av>=11,<16", "rapidocr>=3,<4", "onnxruntime>=1.20,<2", "pypdfium2>=5,<6", "Pillow>=10", "olefile>=0.47"]


def runtime_python(directory):
    return directory / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def main():
    parser = argparse.ArgumentParser(description="Agent 内部录音与 OCR 运行环境准备")
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    workspace = args.workspace.expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    existing = workspace / ".runtime"
    runtime = existing if runtime_python(existing).is_file() else workspace / ".good-student-runtime"
    if not runtime.resolve().is_relative_to(workspace):
        raise SystemExit("运行环境必须位于用户指定项目内")
    if not runtime_python(runtime).is_file():
        venv.EnvBuilder(with_pip=True).create(runtime)
    command = [str(runtime_python(runtime)), "-m", "pip", "install", "--disable-pip-version-check", *PACKAGES]
    code = subprocess.run(command).returncode
    if code:
        return code
    print(json.dumps({"ok": True, "python": str(runtime_python(runtime)), "workspace": str(workspace)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
