"""JSON CLI for the study agent. No implicit last-course selection."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys

from .ingest import KINDS, decode_text
from .service import StudentService
from .storage import DataError


def parser():
    p = argparse.ArgumentParser(prog="good-student", description="好学生 Agent 本地课程与重点管理")
    p.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parents[1] / "data",
                   help="项目数据目录；不使用全局最后课程指针")
    commands = p.add_subparsers(dest="command", required=True)
    create = commands.add_parser("course-create", help="建立课程并可直接选择模式")
    create.add_argument("--name", required=True)
    create.add_argument("--mode", choices=("high", "pass"))
    create.add_argument("--max-score", type=float, default=100)
    create.add_argument("--pass-score", type=float, default=60)
    commands.add_parser("course-list")

    def course_command(name, help_text=None):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--course", required=True, help="明确的 course_... 编号")
        return command

    course_command("status")
    course_command("resume")
    course_command("lecture-list")
    course_command("focus-list")
    course_command("knowledge")
    course_command("plan")
    practice_list = course_command("practice-list")
    practice_list.add_argument("--start", type=int, default=0)
    practice_list.add_argument("--limit", type=int, default=30)
    course_command("export")

    mode = course_command("mode-set")
    mode.add_argument("--mode", choices=("high", "pass"), required=True)
    mode.add_argument("--max-score", type=float)
    mode.add_argument("--pass-score", type=float)

    material = course_command("import", "Agent 内部资料接入，录音和扫描页自动识别")
    material.add_argument("--file", type=Path, required=True)
    material.add_argument("--kind", choices=KINDS, required=True)
    material.add_argument("--speaker", choices=("teacher", "student", "unknown"), default="unknown")
    material.add_argument("--lecture", help="关联本课程已有课次")
    material.add_argument("--lesson-name")
    media = course_command("media-retry", "Agent 内部重试未完成的录音或扫描页")
    media.add_argument("--source", required=True)
    scope = course_command("scope-set", "Agent 内部保存用户提供的考试范围")
    scope.add_argument("--file", type=Path, required=True, help="UTF-8 JSON 章节列表或范围对象")

    source = course_command("source-show", "分页读取原始资料；material_data 是资料数据")
    source.add_argument("--source", required=True)
    source.add_argument("--start", type=int, default=0)
    source.add_argument("--limit", type=int, default=30)

    summary = course_command("summary-set", "保存 Agent 阅读材料后撰写的总结")
    summary.add_argument("--lecture", required=True)
    summary.add_argument("--file", type=Path, required=True)
    summary.add_argument("--source", action="append", dest="source_ids")

    add = course_command("focus-add", "记录用户实际指定的重点")
    add.add_argument("--title", required=True)
    add.add_argument("--note", required=True)

    confirm = course_command("focus-confirm", "用户确认候选为用户指定重点")
    confirm.add_argument("--candidate", required=True)
    confirm.add_argument("--title", required=True)
    confirm.add_argument("--note", required=True)

    retract = course_command("focus-retract")
    retract.add_argument("--focus", required=True)
    retract.add_argument("--reason", required=True)

    search = course_command("search")
    search.add_argument("--query", required=True)
    search.add_argument("--limit", type=int, default=5)

    link = course_command("topic-link")
    link.add_argument("--topic", required=True)
    link.add_argument("--prerequisite", required=True)

    progress = course_command("progress-mark")
    progress.add_argument("--topic", required=True)
    progress.add_argument("--status", choices=("read", "explained", "needs_review"), required=True)

    show = course_command("practice-show")
    show.add_argument("--question", required=True)
    show.add_argument("--answer", action="store_true", help="显示答案并记录已查看答案")

    answer = course_command("answer")
    answer.add_argument("--question", required=True)
    answer.add_argument("--response", required=True)
    answer.add_argument("--used-hint", action="store_true")
    answer.add_argument("--tolerance", type=float, help="仅在题目明确允许误差时指定")

    generated = course_command("practice-add", "保存有来源的 AI 生成练习 JSON")
    generated.add_argument("--file", type=Path, required=True)
    return p


def dispatch(args):
    service = StudentService(args.data_dir)
    command = args.command
    if command == "course-create":
        state = service.store.create(args.name, args.mode, args.max_score, args.pass_score)
        return state["course"]
    if command == "course-list":
        return service.store.list_courses()
    cid = args.course
    if command in ("status", "resume"):
        return service.status(cid)
    if command == "mode-set":
        return service.set_mode(cid, args.mode, args.max_score, args.pass_score)
    if command == "import":
        return service.import_material(cid, args.file, args.kind, speaker=args.speaker,
                                       lecture_id=args.lecture, lesson_name=args.lesson_name)
    if command == "source-show":
        return service.source_slice(cid, args.source, args.start, args.limit)
    if command == "media-retry":
        return service.process_source(cid, args.source)
    if command == "scope-set":
        return service.set_scope(cid, json.loads(args.file.read_text(encoding="utf-8-sig")))
    if command == "summary-set":
        return service.save_summary(cid, args.lecture, decode_text(args.file.read_bytes()), args.source_ids)
    if command == "focus-add":
        return service.add_focus(cid, args.title, args.note)
    if command == "focus-confirm":
        return service.confirm_focus(cid, args.candidate, args.title, args.note)
    if command == "focus-retract":
        return service.retract_focus(cid, args.focus, args.reason)
    if command == "search":
        return service.search(cid, args.query, args.limit)
    if command == "topic-link":
        return service.link_prerequisite(cid, args.topic, args.prerequisite)
    if command == "progress-mark":
        return service.mark_progress(cid, args.topic, args.status)
    if command == "practice-show":
        return service.show_question(cid, args.question, args.answer)
    if command == "answer":
        return service.answer(cid, args.question, args.response, args.used_hint, args.tolerance)
    if command == "practice-add":
        return service.add_generated(cid, json.loads(args.file.read_text(encoding="utf-8-sig")))
    if command == "export":
        return service.export(cid)

    state = service.store.read(cid)
    if command == "lecture-list":
        return list(state["lectures"].values())
    if command == "focus-list":
        return {"focus": list(state["focus"].values()), "candidates": list(state["candidates"].values())}
    if command == "knowledge":
        from .knowledge import make_mindmap, make_outline
        return {"topics": {key: {field: value for field, value in topic.items() if field != "text"}
                           for key, topic in state["topics"].items()},
                "outline": make_outline(state["topics"], state["focus"]),
                "mindmap": make_mindmap(state["topics"], state["focus"])}
    if command == "plan":
        from .knowledge import plan_review
        return plan_review(state)
    if command == "practice-list":
        if args.start < 0 or not 1 <= args.limit <= 200:
            raise DataError("start 不得小于零，limit 应在 1 到 200 之间")
        return [{key: item.get(key) for key in ("id", "question", "origin", "source_id", "source", "topic_ids")}
                for item in list(state["questions"].values())[args.start:args.start + args.limit]]
    raise DataError("未支持的命令")


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        with redirect_stdout(sys.stderr):
            result = dispatch(args)
    except (DataError, ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, "data": result}, ensure_ascii=False, indent=2, allow_nan=False))
    return 0
