"""Source-backed knowledge structure and explicit-mode review planning.

These functions build a structural index, not an invented semantic summary.
The agent may supply better explanations later, with source references retained.
"""
from __future__ import annotations

import copy
import html
from pathlib import Path
import re

from .focus import topic_id


_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)(?:\s+#+)?\s*$")
_CHAPTER = re.compile(r"^(?:第.{1,8}[章节讲]|chapter\s+\d+|lecture\s+\d+).*", re.I)
_LABELS = {
    "exam_assured": "教师明确会考",
    "priority": "教师明确重点",
    "user_priority": "用户重点",
}


def active_focus(focus: dict) -> list[dict]:
    return [f for f in focus.values() if f.get("status", "active") == "active"]


def focus_labels(focus: dict, identifier: str) -> list[str]:
    labels = []
    for item in active_focus(focus):
        if item.get("topic_id") != identifier:
            continue
        label = ("用户重点" if item.get("source_type") == "user_designated"
                 else _LABELS.get(item.get("claim_type"), "教师明确重点"))
        if label not in labels:
            labels.append(label)
    return labels


def _plain(value) -> str:
    """Single-line visible text, without accepting a source as Markdown structure."""
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    text = html.escape(text, quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+!|])", r"\\\1", text)


def _add_topic(topics: dict, title: str, text: str, reference: dict | None = None) -> dict:
    title = title.strip() or "未命名知识节点"
    identifier = topic_id(title)
    item = topics.setdefault(identifier, {
        "id": identifier,
        "title": title,
        "text": "",
        "source_refs": [],
        "prerequisite_ids": [],
        "content_type": "source_excerpt",
    })
    text = text.strip()
    if text and text not in item["text"].split("\n\n"):
        item["text"] = "\n\n".join(t for t in (item["text"], text) if t)
    if reference and reference not in item["source_refs"]:
        item["source_refs"].append(copy.deepcopy(reference))
    return item


def _chunks(text: str, filename: str) -> list[tuple[str, str]]:
    """Split only on explicit structure; a slide's first line is its page title."""
    lines = text.splitlines()
    nonempty = [line.strip() for line in lines if line.strip()]
    if not nonempty:
        return []
    fallback = Path(filename).stem or filename or "未命名资料"
    headings = []
    for index, line in enumerate(lines):
        match = _HEADING.match(line)
        if match:
            headings.append((index, match.group(1).strip()))
        elif len(line.strip()) <= 80 and not re.search(r"[。；;]", line) and _CHAPTER.match(line.strip()):
            headings.append((index, line.strip()))
    if not headings:
        is_slide = Path(filename).suffix.lower() in (".ppt", ".pptx")
        title = nonempty[0] if is_slide and len(nonempty[0]) <= 120 else fallback
        return [(title, text)]
    result = []
    if any(line.strip() for line in lines[:headings[0][0]]):
        result.append((fallback, "\n".join(lines[:headings[0][0]])))
    for n, (start, title) in enumerate(headings):
        end = headings[n + 1][0] if n + 1 < len(headings) else len(lines)
        result.append((title, "\n".join(lines[start:end])))
    return result


def build_knowledge(sources: dict, focus: dict) -> dict:
    """Return stable topic nodes containing actual source excerpts and references."""
    topics = {}
    for source_id, source in sorted(sources.items()):
        if source.get("status") in ("pending", "failed"):
            continue
        filename = source.get("filename") or source_id
        for page in source.get("pages", []):
            text = str(page.get("text") or "")
            locator = {}
            if isinstance(page.get("number"), int) and page["number"] > 0:
                location_type = source.get("location_type", "page")
                location_key = {"page": "page", "slide": "slide", "text_block": "text_block"}.get(location_type)
                if location_key:
                    locator[location_key] = page["number"]
            reference = {"source_id": source_id, "filename": filename, "locator": locator}
            for title, excerpt in _chunks(text, filename):
                _add_topic(topics, title, excerpt, reference)
    # A teacher/user explicitly identified topic is a valid node, even when the
    # corresponding chapter material has not been uploaded. Never invent its text.
    for item in active_focus(focus):
        title = item.get("topic_title", "").strip()
        if not title:
            continue
        identifier = item.get("topic_id") or topic_id(title)
        node = topics.get(identifier)
        if node is None:
            node = {
                "id": identifier, "title": title, "text": "", "source_refs": [],
                "prerequisite_ids": [], "content_type": "focus_evidence_only",
            }
            topics[identifier] = node
        for evidence in item.get("evidence", []):
            source_id = evidence.get("source_id")
            if not source_id or source_id not in sources:
                continue
            reference = {
                "source_id": source_id,
                "filename": sources[source_id].get("filename") or source_id,
                "locator": copy.deepcopy(evidence.get("locator") or {}),
            }
            if reference not in node["source_refs"]:
                node["source_refs"].append(reference)
    return topics


def _locator_text(locator: dict) -> str:
    pieces = []
    if locator.get("page") is not None:
        pieces.append("第 %s 页" % locator["page"])
    if locator.get("slide") is not None:
        pieces.append("第 %s 张幻灯片" % locator["slide"])
    if locator.get("text_block") is not None:
        pieces.append("文本块 %s" % locator["text_block"])
    if locator.get("paragraph") is not None:
        pieces.append("第 %s 段" % locator["paragraph"])
    for key in ("timestamp", "time_start"):
        if locator.get(key) is not None:
            pieces.append(str(locator[key]))
    return "，".join(pieces) or "未提供细分位置"


def make_outline(topics: dict, focus: dict) -> str:
    lines = ["# 知识脉络", "", "以下按资料中的明确结构组织，正文摘录不等同于完整讲解。", ""]
    if not topics:
        lines.append("尚无可组织的知识节点。")
    for identifier, item in topics.items():
        labels = focus_labels(focus, identifier)
        suffix = "（%s）" % "、".join(labels) if labels else ""
        lines.append("- **%s**%s" % (_plain(item.get("title") or identifier), suffix))
        refs = item.get("source_refs", [])
        if not refs:
            lines.append("  - 尚无已上传的章节讲解材料；保留该知识点的重点记录。")
        for reference in refs:
            lines.append("  - 来源：%s；%s" % (
                _plain(reference.get("filename") or reference.get("source_id")),
                _plain(_locator_text(reference.get("locator") or {})),
            ))
        prerequisites = item.get("prerequisite_ids", [])
        if prerequisites:
            names = [topics[p].get("title", p) if p in topics else p for p in prerequisites]
            lines.append("  - 已记录前置知识：%s" % "、".join(_plain(n) for n in names))
    return "\n".join(lines) + "\n"


def _mermaid_label(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value)).strip()[:180]
    # User text must stay inside one label. Entity-encode syntax-bearing symbols
    # as Mermaid supports numeric entities in labels; never reuse it as an ID.
    dangerous = set('&<>"\'[]{}()\\`%')
    return "".join("&#%d;" % ord(c) if c in dangerous or ord(c) < 32 else c for c in text)


def make_mindmap(topics: dict, focus: dict) -> str:
    lines = ["mindmap", '  root["课程知识脉络"]']
    for number, (identifier, item) in enumerate(topics.items(), 1):
        labels = focus_labels(focus, identifier)
        text = item.get("title") or identifier
        if labels:
            text += " — " + "、".join(labels)
        lines.append('    n%d["%s"]' % (number, _mermaid_label(text)))
    if not topics:
        lines.append('    empty["尚无知识节点"]')
    return "\n".join(lines) + "\n"


_CLASSROOM_KINDS = {"audio", "transcript", "summary"}
_MATERIAL_KINDS = {"material", "textbook", "syllabus", "scope", "exam_scope"}
_QUESTION_KINDS = {"exam", "exercise", "solution"}


def compute_coverage(state: dict) -> dict:
    """Describe supplied evidence, not imagined coverage of a whole semester.

    File counts are not lecture counts: the same lesson can have audio, a
    transcript and a summary. No recording percentage can be inferred from them.
    """
    sources = state.get("sources") or {}
    counts = {
        "audio": 0, "transcript": 0, "summary": 0,
        "material": 0, "questions": 0, "incomplete": 0,
    }
    usable_materials = 0
    usable_questions = 0
    usable_classroom = 0
    for source in sources.values():
        kind = source.get("kind")
        usable = (source.get("status", "complete") not in {"pending", "failed"}
                  and bool(source.get("pages") or source.get("segments")))
        if kind in _CLASSROOM_KINDS:
            counts[kind] += 1
            usable_classroom += int(usable)
        elif kind in _MATERIAL_KINDS:
            counts["material"] += 1
            usable_materials += int(usable)
        elif kind in _QUESTION_KINDS:
            counts["questions"] += 1
            usable_questions += int(usable)
        if source.get("status", "complete") != "complete":
            counts["incomplete"] += 1
    has_classroom = any(counts[key] for key in ("audio", "transcript", "summary"))
    if usable_materials:
        basis = "course_materials"
        summary = "按课件、教材或考试范围复习，现有课堂记录用于补充；没有录音的章节也照常复习。"
    elif usable_classroom:
        basis = "classroom_only"
        summary = "先复习已提供的课堂内容；补充课件、教材或考试范围，有助于建立完整课程主线。"
    elif usable_questions:
        basis = "questions_only"
        summary = "可先按已有题目练习；课件、教材或考试范围能帮助补充课程主线。"
    else:
        basis = "no_usable_materials"
        summary = ("课堂记录尚未处理完成，可先提供课件、题目或已有转写文本开始复习。" if has_classroom else
                   "提供课件、教材、题目或已有课堂记录中的任一种，即可开始整理。")
    return {
        "basis": basis,
        "counts": counts,
        "has_course_materials": bool(usable_materials),
        "has_classroom_records": has_classroom,
        "has_audio": bool(counts["audio"]),
        "recording_coverage": "unknown" if has_classroom else "not_provided",
        "recordings_required": False,
        "summary": summary,
        "recording_note": ("课堂记录不能代表全学期范围；没有录到不表示不考。" if has_classroom else
                           "没有录音也可以复习，不需要补齐全学期录音。"),
    }


def _scope_values(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if isinstance(item, (str, int))]
    return []


def _scope_topics(state: dict, topics: dict) -> tuple[set[str], set[str], list[str]]:
    """Resolve only explicit scope references; do not guess from free prose."""
    sources = state.get("sources") or {}
    scope = (state.get("course") or {}).get("exam_scope")
    requested = []
    excluded = []
    source_ids = []
    warnings = []
    if isinstance(scope, (list, tuple, set)):
        requested = _scope_values(scope)
    elif isinstance(scope, dict):
        for key in ("topic_ids", "topic_titles", "titles", "topics", "include_topic_ids", "include_titles"):
            requested.extend(_scope_values(scope.get(key)))
        for key in ("exclude_topic_ids", "exclude_titles"):
            excluded.extend(_scope_values(scope.get(key)))
        source_ids = _scope_values(scope.get("source_ids"))
    # A plain-language range remains a note for the agent to interpret. Turning
    # arbitrary words into a restrictive filter could silently discard chapters.
    def resolve(values):
        result = set()
        unknown = []
        for value in values:
            identifier = value if value in topics else topic_id(value)
            if identifier in topics:
                result.add(identifier)
            else:
                unknown.append(value)
        return result, unknown

    wanted, missing = resolve(requested)
    excluded_ids, _ = resolve(excluded)
    if missing:
        warnings.append("考试范围中的以下条目尚无对应知识资料：%s。" % "、".join(missing))
    allowed = set(topics)
    if requested:
        allowed &= wanted
    if source_ids:
        allowed &= {
            identifier for identifier, item in topics.items()
            if any(ref.get("source_id") in source_ids for ref in item.get("source_refs", []))
        }
    for identifier, item in topics.items():
        refs = [ref.get("source_id") for ref in item.get("source_refs", []) if ref.get("source_id") in sources]
        if item.get("in_exam_scope") is False or item.get("exam_scope") is False:
            excluded_ids.add(identifier)
        elif refs and all(sources[sid].get("in_exam_scope") is False or sources[sid].get("exam_scope") is False
                          for sid in refs):
            excluded_ids.add(identifier)
    allowed -= excluded_ids
    return allowed, wanted, warnings


def plan_review(state: dict) -> dict:
    """Plan from explicit mode/focus/dependencies, never from inferred exam odds."""
    course = state.get("course") or {}
    mode = course.get("mode")
    if mode not in ("high", "pass"):
        raise ValueError("开始复习前请选择高分模式 high 或速通模式 pass。")
    score_max = course.get("score_max", 100)
    passing_score = course.get("passing_score", 60)
    if (not isinstance(score_max, (int, float)) or isinstance(score_max, bool) or score_max <= 0
            or (mode == "pass" and (not isinstance(passing_score, (int, float))
                or isinstance(passing_score, bool) or not 0 <= passing_score <= score_max))):
        raise ValueError("卷面满分和及格目标必须是有效数值，且目标不可超过满分。")
    topics = copy.deepcopy(state.get("topics") or {})
    focus = state.get("focus") or {}
    for item in active_focus(focus):
        identifier = item.get("topic_id")
        if identifier and identifier not in topics:
            topics[identifier] = {
                "id": identifier, "title": item.get("topic_title") or identifier,
                "source_refs": [], "prerequisite_ids": [], "text": "",
            }
    allowed, explicitly_scoped, warnings = _scope_topics(state, topics)
    focused = {item["topic_id"] for item in active_focus(focus) if item.get("topic_id") in allowed}
    sources = state.get("sources") or {}
    backbone = {
        identifier for identifier, item in topics.items() if identifier in allowed
        and any((sources.get(ref.get("source_id")) or {}).get("kind") in _MATERIAL_KINDS
                for ref in item.get("source_refs", []))
    }
    backbone |= explicitly_scoped & allowed
    # A small set of recordings cannot define the whole course. When course
    # material exists, pass mode keeps that structural backbone and strengthens
    # actual priorities instead of dropping every unrecorded chapter.
    selected = (set(allowed) if mode == "high" or not focused else set(focused) | backbone)
    coverage_info = compute_coverage(state)
    cursor = list(selected)
    while cursor:
        identifier = cursor.pop()
        for prerequisite in topics[identifier].get("prerequisite_ids", []):
            if prerequisite not in topics:
                warning = "知识点 %s 的前置节点 %s 缺少资料。" % (identifier, prerequisite)
                if warning not in warnings:
                    warnings.append(warning)
            elif prerequisite not in allowed:
                warning = "知识点 %s 的前置节点 %s 在当前考试范围之外；如需补基础可单独学习。" % (identifier, prerequisite)
                if warning not in warnings:
                    warnings.append(warning)
            elif prerequisite not in selected:
                selected.add(prerequisite)
                cursor.append(prerequisite)
    progress = state.get("progress") or {}
    rank = {"needs_review": 0, "仍需复习": 0, "todo": 1, "未开始": 1,
            "read": 2, "已阅读或讲解": 2, "attempted": 3, "已尝试作答": 3,
            "practice_passed": 4, "练习通过": 4}

    def order(identifier):
        status = (progress.get(identifier) or {}).get("status", "todo")
        return rank.get(status, 1), 0 if identifier in focused else 1, topics[identifier].get("title", identifier)

    ordered = []
    done = set()
    visiting = set()

    def visit(identifier):
        if identifier in done:
            return
        if identifier in visiting:
            warning = "前置关系存在循环：%s；请核对知识关系。" % identifier
            if warning not in warnings:
                warnings.append(warning)
            return
        visiting.add(identifier)
        for prerequisite in sorted(topics[identifier].get("prerequisite_ids", []), key=str):
            if prerequisite in selected:
                visit(prerequisite)
        visiting.remove(identifier)
        done.add(identifier)
        ordered.append(identifier)

    for identifier in sorted(selected, key=order):
        visit(identifier)
    items = []
    for identifier in ordered:
        item = topics[identifier]
        labels = focus_labels(focus, identifier)
        if identifier in focused:
            reason = "、".join(labels)
            group = "priority"
        elif identifier in backbone:
            reason = "课程主线补充，不因缺少录音而跳过"
            group = "core_supplement"
        elif mode == "pass" and focused:
            reason = "重点的已记录前置知识"
            group = "prerequisite"
        elif mode == "pass":
            reason = "基础材料补充，未标记为教师重点"
            group = "core_supplement"
        else:
            reason = "考试范围内已提供的课程知识"
            group = "core_supplement"
        items.append({
            "topic_id": identifier, "title": item.get("title") or identifier,
            "reason": reason, "focus_labels": labels, "group": group,
            "source_refs": item.get("source_refs", []),
            "prerequisite_ids": item.get("prerequisite_ids", []),
            "progress": (progress.get(identifier) or {}).get("status", "todo"),
        })
    coverage = ("覆盖考试范围内已提供的知识节点；课堂记录只作补充，缺失资料不视为已覆盖。" if mode == "high" else
                "优先复习正式重点，同时保留课件、教材和考试范围中的课程主线，补充必要前置知识。" if focused and backbone else
                "优先复习已知正式重点及必要前置知识；仅有部分课堂记录时不能据此认定整门课范围。" if focused else
                "尚无有效正式重点，当前从已提供资料组织基础学习，不将补充内容认定为教师重点。")
    return {
        "mode": mode, "mode_label": "高分模式" if mode == "high" else "速通模式",
        "target": {"score_max": score_max, "passing_score": passing_score if mode == "pass" else None},
        "items": items, "coverage_note": coverage,
        "coverage": coverage_info,
        "uncovered_topic_ids": sorted(set(topics) - selected),
        "dependency_warnings": warnings,
        "score_note": "学习安排不预测真实考试分数；练习通过不等于保证考试及格。",
    }
