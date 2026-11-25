"""Exercise the actual skill entry point with local Chinese speech and scans."""
from pathlib import Path
import json
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/good-student/scripts/student.py"
DATA = ROOT / "audit" / ("media-agent-" + uuid.uuid4().hex[:8])


def call(*args):
    result = subprocess.run([sys.executable, "-X", "utf8", "-B", str(SCRIPT), "--data-dir", str(DATA), *args],
                            cwd=ROOT, text=True, encoding="utf-8", capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr)
    return json.loads(result.stdout)["data"]


def main():
    course = call("course-create", "--name", "零散录音与扫描件验证", "--mode", "pass")
    cid = course["id"]
    call("import", "--course", cid, "--kind", "material", "--file", str(ROOT / "examples/materials/课程知识.md"))
    image = call("import", "--course", cid, "--kind", "material", "--file", str(ROOT / "audit/media-smoke/chinese-scan.png"))
    pdf = call("import", "--course", cid, "--kind", "material", "--file", str(ROOT / "audit/media-smoke/chinese-scan.pdf"))
    audio = call("import", "--course", cid, "--kind", "audio", "--file", str(ROOT / "audit/media-smoke/asr-example-zh.wav"))
    assert image["source"]["status"] == pdf["source"]["status"] == audio["source"]["status"] == "complete"
    speech = call("source-show", "--course", cid, "--source", audio["source"]["id"])
    assert speech["material_data"]
    call("focus-add", "--course", cid, "--title", "队列", "--note", "演示用户只把队列指定为重点")
    plan = call("plan", "--course", cid)
    assert "栈" in [item["title"] for item in plan["items"]]
    assert not plan["coverage"]["recordings_required"]
    exported = call("export", "--course", cid)
    result = {"data_directory": str(DATA), "course_id": cid,
              "audio_status": audio["source"]["status"], "speech_segments": speech["material_data"],
              "image_status": image["source"]["status"], "pdf_status": pdf["source"]["status"],
              "course_backbone_retained": True, "coverage": plan["coverage"], "export": exported}
    (ROOT / "audit/media-agent-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "export"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
