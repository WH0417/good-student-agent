"""Source-backed questions and deterministic grading.

Question extraction is adapted through the pinned MIT-licensed coach module.
Product identity and scoring rules live here rather than in the upstream CLI.
"""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import hashlib
import json
import math
import re
import unicodedata

from .vendor.coach import chapters as chapter_tools
from .vendor.coach import extract as extract_tools
from .vendor.coach import questions as question_tools


def _canonical(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


def _identifier(prefix: str, *parts) -> str:
    encoded = json.dumps(parts, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return prefix + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def build_bank(sources: dict[str, dict]) -> dict[str, dict]:
    """Extract original questions with stable source-bound IDs.

    Source IDs must describe immutable material revisions. Replacing a file with
    changed content therefore creates new source/question identities, while
    adding any other file never renumbers existing questions.
    """
    extracted = []
    records_by_rel = {}
    upstream_kinds = {
        "exam": "exam", "exercise": "homework", "solution": "solution",
        "material": "lecture", "transcript": "notes", "summary": "notes",
    }
    for source_id, record in sorted(sources.items()):
        if record.get("status") not in ("complete", "partial"):
            continue
        kind = upstream_kinds.get(record.get("kind"))
        if kind is None:
            continue
        filename = record.get("filename") or source_id
        rel = source_id + "/" + filename
        source = extract_tools.Source(record.get("stored_path", ""), rel, kind)
        source.pages = [
            extract_tools.Page(int(page.get("number", 1)), page.get("text", ""))
            for page in record.get("pages", [])
            if isinstance(page.get("text"), str)
        ]
        if not source.pages:
            continue
        records_by_rel[rel] = (source_id, record)
        extracted.append(source)

    # Upstream chooses the first solution with a matching basename. If several
    # revisions are present, that would silently attach an arbitrary answer.
    # Keep such solutions out of automatic pairing until the user resolves it.
    solution_counts = {}
    for source in extracted:
        if source.kind == "solution":
            key = question_tools.pair_key(source.rel)
            solution_counts[key] = solution_counts.get(key, 0) + 1
    for source in extracted:
        if source.kind == "solution" and solution_counts[question_tools.pair_key(source.rel)] > 1:
            source.kind = "ambiguous_solution"

    chapter_list = chapter_tools.build_chapters(extracted)
    items = question_tools.extract_questions(extracted, chapter_list)
    bank = {}
    for item in items:
        # A solution alone is not a complete original exercise statement.
        if item["source"]["kind"] == "solution":
            continue
        source_id, record = records_by_rel[item["source"]["file"]]
        if record.get("kind") not in ("exam", "exercise"):
            continue
        question = deepcopy(item)
        question["source_id"] = source_id
        question["source"]["file"] = record["filename"]
        answer_ref = question.get("answer_source")
        question["answer_source_id"] = None
        if answer_ref:
            answer_source_id, answer_record = records_by_rel[answer_ref["file"]]
            answer_ref["file"] = answer_record["filename"]
            question["answer_source_id"] = answer_source_id
        question["id"] = _identifier(
            "q_", source_id, str(item.get("label", "")),
            _canonical(question["question"]), [_canonical(o) for o in question.get("options", [])],
        )
        question.update(origin="original", topic_ids=[], reference_ids=[])
        question["source_ids"] = list(dict.fromkeys(
            sid for sid in (source_id, question["answer_source_id"]) if sid
        ))
        bank[question["id"]] = question
    return bank


_NUMBER = re.compile(
    r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)(?:\s*/\s*[-+]?(?:\d+(?:\.\d+)?|\.\d+))?\s*%?"
)
_REJECT_NUMBER_CONTEXT = re.compile(r"不是|不对|不能|或|\b(?:not|wrong|or)\b", re.I)


def _number(text: str, *, strict: bool) -> Fraction | None:
    text = unicodedata.normalize("NFKC", text)
    if _REJECT_NUMBER_CONTEXT.search(text):
        return None
    matches = list(_NUMBER.finditer(text))
    if len(matches) != 1:
        return None
    token = matches[0].group().replace(" ", "").strip()
    remainder = _NUMBER.sub("", text)
    if strict and remainder.strip(" =≈约是为:："):
        return None
    percent = token.endswith("%")
    token = token.rstrip("%")
    try:
        if "/" in token:
            numerator, denominator = token.split("/", 1)
            value = Fraction(numerator) / Fraction(denominator)
        else:
            value = Fraction(token)
        return value / 100 if percent else value
    except (ValueError, ZeroDivisionError):
        return None


def _validated_tolerance(value) -> Fraction | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("数值容差必须是非负有限数值")
    try:
        if not math.isfinite(float(value)):
            raise ValueError
        tolerance = Fraction(str(value))
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        raise ValueError("数值容差必须是非负有限数值") from None
    if tolerance < 0:
        raise ValueError("数值容差不能为负数")
    return tolerance


def grade_answer(question: dict, answer: str, tolerance=None) -> dict:
    """Grade supported objective answers; leave open answers for Agent review.

    Numbers are exact by default (``1/2``, ``0.5`` and ``50%`` are equal).
    ``tolerance`` is an explicitly configured absolute numerical tolerance.
    """
    tolerance = _validated_tolerance(tolerance)
    if not isinstance(answer, str) or not answer.strip():
        return {"result": "unsure", "reference_answer": question.get("answer"), "reason": "尚未提供有效作答"}
    reference = question.get("answer")
    if reference is not None and not isinstance(reference, str):
        reference = str(reference)
    normalized_question = dict(question, answer=reference)
    key = question_tools.answer_key(normalized_question)
    if key is None:
        return {"result": "unsure", "reference_answer": reference, "reason": "缺少参考答案或需要根据过程进行人工或 Agent 评阅"}
    if key[0] == "number":
        expected = _number(question_tools._first_line(reference), strict=True)
        actual = _number(answer, strict=False)
        if expected is None or actual is None:
            return {"result": "unsure", "reference_answer": reference, "reason": "无法确定唯一数值作答"}
        distance = abs(expected - actual)
        right = distance == 0 if tolerance is None else distance <= tolerance
        return {
            "result": "right" if right else "wrong",
            "reference_answer": reference,
            "reason": "按精确数值比较" if tolerance is None else "按显式指定的绝对容差比较",
        }
    result, _ = question_tools.grade_answer(normalized_question, answer)
    return {
        "result": result, "reference_answer": reference,
        "reason": "依据材料中的客观参考答案比较" if result != "unsure" else "作答无法可靠自动判定，需要进一步评阅",
    }


def _string_list(question: dict, key: str) -> list[str]:
    value = question.get(key, [])
    if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
        raise ValueError(f"{key} 必须是非空字符串组成的列表")
    return list(dict.fromkeys(v.strip() for v in value))


def _source_ids(record: dict) -> set[str]:
    result = set()
    if record.get("source_id"):
        result.add(record["source_id"])
    for source_id in record.get("source_ids", []):
        if isinstance(source_id, str):
            result.add(source_id)
    for reference in record.get("source_refs", []):
        if isinstance(reference, dict) and reference.get("source_id"):
            result.add(reference["source_id"])
    return result


def validate_generated(question: dict, state: dict) -> dict:
    """Validate Agent-authored practice without asserting semantic correctness.

    Caller must supply an existing reference question or topic and evidence from
    this course. The returned origin is always ``ai_generated``.
    """
    if not isinstance(question, dict):
        raise ValueError("生成练习必须是一个 JSON 对象")
    values = {}
    for field in ("question", "answer", "explanation"):
        value = question.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"生成练习必须提供非空 {field}")
        values[field] = value.strip()
    references = _string_list(question, "reference_ids")
    topics = _string_list(question, "topic_ids")
    source_ids = _string_list(question, "source_ids")
    if not references and not topics:
        raise ValueError("生成练习必须关联已有参考题或知识点")
    if any(qid not in state.get("questions", {}) for qid in references):
        raise ValueError("参考题不属于当前课程")
    if any(tid not in state.get("topics", {}) for tid in topics):
        raise ValueError("知识点不属于当前课程")
    anchor_sources = set()
    for qid in references:
        anchor_sources.update(_source_ids(state["questions"][qid]))
    for tid in topics:
        anchor_sources.update(_source_ids(state["topics"][tid]))
    if not source_ids:
        source_ids = sorted(anchor_sources)
    if not source_ids:
        raise ValueError("生成练习必须有可核对的资料来源")
    if any(sid not in state.get("sources", {}) for sid in source_ids):
        raise ValueError("资料来源不属于当前课程")
    if any(state["sources"][sid].get("status") == "pending" for sid in source_ids):
        raise ValueError("尚未处理的资料不能作为生成练习依据")
    if anchor_sources and not anchor_sources.intersection(source_ids):
        raise ValueError("指定资料与参考题或知识点的来源不一致")
    raw_options = _string_list(question, "options")
    if raw_options and not 2 <= len(raw_options) <= 8:
        raise ValueError("选择题应提供 2 至 8 个选项")
    options = []
    for index, option in enumerate(raw_options):
        label = chr(ord("A") + index)
        option_text = re.sub(r"^[A-H][.、．]\s*", "", option).strip()
        if not option_text:
            raise ValueError("选项内容不能为空")
        options.append(f"{label}. {option_text}")
    result = dict(values)
    result.update(
        origin="ai_generated", reference_ids=references, topic_ids=topics,
        source_ids=source_ids, source_id=source_ids[0], source=None,
        answer_source_id=None, answer_source=None, options=options,
        type=question_tools._qtype(values["question"], options),
    )
    if options:
        key = question_tools.answer_key(result)
        labels = {chr(ord("A") + i) for i in range(len(options))}
        if key is None or key[0] != "choice" or not key[1] or not key[1].issubset(labels):
            raise ValueError("选择题参考答案必须明确指向有效选项字母")
    points = question.get("points")
    if points is not None:
        if isinstance(points, bool) or not isinstance(points, (int, float)) or not math.isfinite(points) or points <= 0:
            raise ValueError("练习分值必须是正的有限数值")
    result["points"] = points
    if "tolerance" in question:
        _validated_tolerance(question["tolerance"])
        result["tolerance"] = question["tolerance"]
    result["id"] = _identifier(
        "qg_", sorted(source_ids), sorted(references), sorted(topics),
        _canonical(values["question"]), options, _canonical(values["answer"]),
    )
    return result
