"""Portable Markdown course snapshots made only from persisted course data."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import html
import json
from pathlib import Path
import re

from .knowledge import active_focus, compute_coverage, make_mindmap, make_outline, plan_review


def _text(value) -> str:
    text = html.escape(str(value or ""), quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+!|])", r"\\\1", text)


def _quote(value) -> list[str]:
    lines = str(value or "").splitlines() or [""]
    return ["> " + _text(line) for line in lines]


def _location(locator: dict) -> str:
    fields = []
    for key, pattern in (("page", "第 %s 页"), ("slide", "第 %s 张幻灯片"),
                         ("text_block", "文本块 %s"), ("paragraph", "第 %s 段"),
                         ("timestamp", "%s"), ("time_start", "%s")):
        if locator.get(key) is not None:
            fields.append(pattern % locator[key])
    return "，".join(fields) or "未提供细分位置"


def _source(state, source_id, locator=None) -> str:
    if not source_id:
        return "用户直接指定，无课堂录音出处"
    source = (state.get("sources") or {}).get(source_id)
    if source is None:
        return "来源记录缺失：%s" % _text(source_id)
    kinds = {"transcript": "课堂转写", "summary": "压缩纪要", "material": "课程资料",
             "exam": "真题", "exercise": "自测题", "solution": "答案资料", "audio": "录音"}
    kind = kinds.get(source.get("kind"), source.get("kind") or "资料")
    locator = dict(locator or {})
    # Question extractors retain a generic page number internally. Present it
    # according to the imported source type rather than inventing physical pages.
    location_type = source.get("location_type", "page")
    if "page" in locator and location_type in ("slide", "text_block"):
        locator[location_type] = locator.pop("page")
    return "%s；%s；%s" % (
        _text(source.get("filename") or source_id), _text(kind),
        _text(_location(locator)),
    )


def _incomplete(state) -> list[dict]:
    result = []
    for source_id, source in (state.get("sources") or {}).items():
        if source.get("status", "complete") != "complete":
            result.append({"type": "source", "id": source_id, "filename": source.get("filename"),
                           "status": source.get("status"), "error": source.get("error"),
                           "missing_ranges": source.get("missing_ranges", [])})
    for lecture_id, lecture in (state.get("lectures") or {}).items():
        if lecture.get("status", "complete") != "complete":
            result.append({"type": "lecture", "id": lecture_id, "name": lecture.get("name"),
                           "status": lecture.get("status"), "missing_ranges": lecture.get("missing_ranges", [])})
    return result


def _focus_document(state, exported_at, incomplete) -> str:
    course = state.get("course") or {}
    effective = active_focus(state.get("focus") or {})
    lines = ["# %s 全科累计重点" % _text(course.get("name") or "课程"), "",
             "导出时间：%s" % exported_at, "",
             "有效重点 %d 条；纳入资料 %d 份。重点按来源记录，不代表对未来试题的预测。" % (
                 len(effective), len(state.get("sources") or {})), ""]
    if not effective:
        lines += ["当前没有有效正式重点。未识别到明确表述不等于课堂没有重点。", ""]
    claim_labels = {"exam_assured": "明确会考", "priority": "明确重点", "user_priority": "用户优先复习"}
    for item in effective:
        lines += ["## %s" % _text(item.get("topic_title") or item.get("topic_id")), "",
                  "来源类别：%s；声明类别：%s。" % (
                      "用户指定" if item.get("source_type") == "user_designated" else "教师明确表述",
                      claim_labels.get(item.get("claim_type"), _text(item.get("claim_type")))), ""]
        evidence = item.get("evidence") or []
        if not evidence:
            lines += ["未附证据文本；请检查重点记录。", ""]
        for record in evidence:
            lines += _quote(record.get("quote") or record.get("text") or "")
            lines += ["", "出处：%s" % _source(state, record.get("source_id"), record.get("locator")), ""]
        progress = (state.get("progress") or {}).get(item.get("topic_id")) or {}
        lines += ["学习状态：%s。" % _text(progress.get("status") or "未开始"), ""]
    pending = [item for item in (state.get("candidates") or {}).values() if item.get("status", "pending") == "pending"]
    lines += ["## 待确认内容", "", "以下未计入有效正式重点。", ""]
    if not pending:
        lines += ["暂无待确认项。", ""]
    for item in pending:
        lines += _quote(item.get("quote"))
        lines += ["", "原因：%s；出处：%s" % (_text(item.get("reason")),
                    _source(state, item.get("source_id"), item.get("locator"))), ""]
    lines += ["## 资料完整性", ""]
    coverage = compute_coverage(state)
    lines += [coverage["summary"], "", coverage["recording_note"], ""]
    if not incomplete:
        lines += ["所有已记录资料均标记为处理完成；该状态不说明尚未上传的课堂内容已覆盖。", ""]
    for record in incomplete:
        lines += ["- %s：%s；状态 %s；未处理区间 %s" % (
            _text(record.get("filename") or record.get("name") or record["id"]),
            _text(record["id"]), _text(record.get("status")),
            _text(record.get("missing_ranges") or "详见处理记录"))]
        if record.get("error"):
            lines.append("  - 说明：%s" % _text(record["error"]))
    inactive = [f for f in (state.get("focus") or {}).values() if f.get("status", "active") != "active"]
    lines += ["", "## 已撤回或存在冲突的记录", "", "以下不计入本次有效重点。", ""]
    if not inactive:
        lines.append("暂无。")
    for item in inactive:
        lines += ["### %s" % _text(item.get("topic_title")), "",
                  "记录状态：%s。" % _text(item.get("status")), ""]
        if item.get("retraction_reason"):
            lines += ["撤回备注：%s" % _text(item["retraction_reason"]), ""]
        for change in item.get("scope_changes") or []:
            action = {"retract": "撤回范围", "conflict": "范围冲突"}.get(change.get("action"), "范围变更")
            lines += ["变更依据：%s。" % action, ""]
            if change.get("topic_title"):
                lines += ["适用知识点：%s。" % _text(change["topic_title"]), ""]
            lines += _quote(change.get("quote"))
            lines += ["", "出处：%s" % _source(state, change.get("source_id"), change.get("locator")), ""]
    return "\n".join(lines).rstrip() + "\n"


def _review_document(state) -> str:
    topics = state.get("topics") or {}
    focus = state.get("focus") or {}
    lines = ["# 课程复习资料", ""]
    try:
        plan = plan_review(state)
    except ValueError as error:
        lines += [str(error), "", "尚未生成教学计划；下列资料目录与课次记录仍可查看。", ""]
    else:
        target = plan["target"]
        lines += ["当前模式：%s。" % plan["mode_label"], ""]
        if plan["mode"] == "pass":
            lines += ["卷面目标：%s / %s 分。" % (target["passing_score"], target["score_max"]), ""]
        coverage = plan.get("coverage") or compute_coverage(state)
        lines += [coverage["summary"], "", coverage["recording_note"], "", "## 后续学习任务", ""]
        progress_labels = {"todo": "未开始", "read": "已阅读", "explained": "已讲解",
                           "needs_review": "需复习", "practice_passed": "练习通过"}
        for index, item in enumerate(plan["items"], 1):
            lines.append("%d. %s；依据：%s；当前状态：%s。" % (
                index, _text(item["title"]), _text(item["reason"]),
                _text(progress_labels.get(item["progress"], item["progress"]))))
        if not plan["items"]:
            lines.append("暂无可安排的知识节点，请先导入课程资料。")
        lines += ["", "## 尚未纳入当前计划的节点", ""]
        for identifier in plan["uncovered_topic_ids"]:
            lines.append("- %s" % _text((topics.get(identifier) or {}).get("title") or identifier))
        if not plan["uncovered_topic_ids"]:
            lines.append("当前已记录节点均已纳入计划；不代表尚未上传的课程已覆盖。")
        if plan["dependency_warnings"]:
            lines += ["", "## 知识关系待核对", ""] + [_text(s) for s in plan["dependency_warnings"]]
        lines += ["", plan["score_note"], ""]
    lines += [make_outline(topics, focus).replace("# 知识脉络", "## 知识脉络", 1),
              "## 思维导图", "", "```mermaid", make_mindmap(topics, focus).rstrip(), "```", "",
              "不支持 Mermaid 的阅读器可使用上面的文字知识脉络。", "", "## 逐课摘要", ""]
    for lecture in (state.get("lectures") or {}).values():
        lines += ["### %s" % _text(lecture.get("name") or lecture.get("id")), "",
                  "处理状态：%s。" % _text(lecture.get("status") or "未记录"), ""]
        summary = lecture.get("summary")
        if isinstance(summary, dict):
            summary = summary.get("text") or summary.get("content")
        if summary:
            lines += _quote(summary)
        else:
            lines.append("尚未保存该课摘要。")
        for source_id in lecture.get("source_ids", []):
            lines += ["", "资料：%s" % _source(state, source_id)]
        lines.append("")
    if not state.get("lectures"):
        lines += ["暂无课堂记录，可以继续按课件和题目复习。", ""]
    return "\n".join(lines).rstrip() + "\n"


def _practice_document(state) -> str:
    questions = state.get("questions") or {}
    lines = ["# 课程练习", "", "先完成题目区，再查看后面的参考答案与解析。", "", "## 题目区", ""]
    for number, (identifier, question) in enumerate(questions.items(), 1):
        origin = question.get("origin")
        label = "AI 生成练习" if origin in ("ai_generated", "generated") else "原题" if origin == "original" else "来源类型未标注"
        lines += ["### 第 %d 题" % number, "", "类型：%s。" % label, ""]
        lines += _quote(question.get("question"))
        for option in question.get("options") or []:
            lines += _quote(option)
        if question.get("points") is not None:
            lines += ["", "本次练习分值：%s。" % _text(question["points"])]
        source_ref = question.get("source") or {}
        source_id = question.get("source_id") or source_ref.get("source_id")
        if source_id:
            locator = source_ref.get("locator") or ({"page": source_ref["page"]} if source_ref.get("page") is not None else {})
            lines += ["", "资料：%s" % _source(state, source_id, locator)]
        if question.get("reference_ids"):
            lines += ["", "根据已有题目生成的变式练习。"]
        lines.append("")
    if not questions:
        lines += ["尚无题目。", ""]
    lines += ["## 参考答案与解析", ""]
    for number, (identifier, question) in enumerate(questions.items(), 1):
        lines += ["### 第 %d 题" % number, ""]
        answer = question.get("answer")
        lines += _quote(answer if answer is not None else "未提供参考答案")
        lines += ["", "解析：", ""] + _quote(question.get("explanation") or "未提供解析") + [""]
        if question.get("origin") in ("ai_generated", "generated"):
            lines += ["本题及解析为 AI 生成内容。", ""]
        elif not question.get("answer_source_id"):
            lines += ["答案来源未单独登记，请结合原资料核对。", ""]
    return "\n".join(lines).rstrip() + "\n"


def export_course(state: dict, destination: Path) -> dict:
    """Write one independent export snapshot and return its manifest.

    Only fixed output filenames are written. Source filenames and stored paths
    are displayed as text and never used as export filesystem paths.
    """
    destination = Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError("导出目录已有内容，请使用新的快照目录。")
    destination.mkdir(parents=True, exist_ok=True)
    exported_at = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
    incomplete = _incomplete(state)
    focus = state.get("focus") or {}
    topics = state.get("topics") or {}
    outputs = {
        "exam_focus.md": _focus_document(state, exported_at, incomplete),
        "review_guide.md": _review_document(state),
        "practice.md": _practice_document(state),
        "mindmap.mmd": make_mindmap(topics, focus),
    }
    course = state.get("course") or {}
    manifest = {
        "version": 1, "course_id": course.get("id"), "course_name": course.get("name"),
        "mode": course.get("mode"),
        "goal": {"score_max": course.get("score_max", 100), "passing_score": course.get("passing_score", 60)},
        "exported_at": exported_at, "updated_at": state.get("updated_at"),
        "counts": {
            "sources": len(state.get("sources") or {}), "lectures": len(state.get("lectures") or {}),
            "topics": len(topics), "active_focus": len(active_focus(focus)),
            "pending_candidates": sum(c.get("status", "pending") == "pending" for c in (state.get("candidates") or {}).values()),
            "questions": len(state.get("questions") or {}), "attempts": len(state.get("attempts") or []),
        },
        "sources": [{"id": identifier, "filename": source.get("filename"),
                     "status": source.get("status"), "sha256": source.get("sha256")}
                    for identifier, source in (state.get("sources") or {}).items()],
        "incomplete_records": incomplete,
        "coverage": compute_coverage(state),
        "files": [*outputs, "export_manifest.json"],
    }
    for filename, content in outputs.items():
        temporary = destination / (filename + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(destination / filename)
    # The manifest is the completion marker and is written after every output.
    temporary = destination / "export_manifest.json.tmp"
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(destination / "export_manifest.json")
    return manifest
