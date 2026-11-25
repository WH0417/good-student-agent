"""Exercise the product against explicitly synthetic course materials."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from good_student.service import StudentService  # noqa: E402


def run(output=None):
    data = Path(output) if output else ROOT / "audit" / ("product-demo-" + uuid.uuid4().hex[:8])
    service = StudentService(data)
    cid = service.store.create("数据结构演示课程", "pass")["course"]["id"]
    materials = ROOT / "examples" / "materials"
    first = service.import_material(cid, materials / "课堂逐字稿.txt", "transcript")
    second = service.import_material(cid, materials / "豆包纪要.md", "summary")
    service.import_material(cid, materials / "课程知识.md", "material")
    service.import_material(cid, materials / "练习题.txt", "exercise")
    service.add_focus(cid, "链表", "演示用户实际指定：链表为自己的复习重点")
    service.save_summary(cid, first["lecture_id"],
        "本课介绍栈、队列和二叉树。栈为后进先出，队列为先进先出。"
        "教师明确强调队列，并说明二叉树的遍历会考。学生对图论的判断不作为教师重点。")
    state = service.store.read(cid)
    original = next(q for q in state["questions"].values() if q.get("answer") and "两个不同元素" in q["question"])
    wrong = service.answer(cid, original["id"], "2.4")
    assert wrong["result"] == "wrong"
    variant = service.add_generated(cid, {
        "question": "空集合依次插入三个不同元素，共有几个元素？", "answer": "3",
        "explanation": "三个元素互不相同，所以集合有三个元素。",
        "reference_ids": [original["id"]], "topic_ids": [], "source_ids": [],
    })
    service.answer(cid, variant["id"], "3")
    pass_export = service.export(cid)
    service.set_mode(cid, "high")
    resumed = StudentService(data).status(cid)
    high_export = service.export(cid)
    result = {"synthetic_demo": True, "data_directory": str(data.resolve()), "course_id": cid,
              "first_import_focus": first["new_focus"], "summary_auto_teacher_focus": second["new_focus"],
              "resume_status": resumed, "pass_export": pass_export, "high_export": high_export}
    output_path = ROOT / "audit" / "product-demo-result.json"
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(run(), ensure_ascii=False, indent=2))
