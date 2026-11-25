"""Conservative, source-bound extraction of explicit classroom priorities.

This is a deterministic first pass, not a prediction of exam content. Ambiguous
statements remain candidates for the agent/user to review. Non-classroom files
cannot create teacher evidence, and a summary cannot become a verbatim quote.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
import unicodedata


def _hash(*parts: object) -> str:
    encoded = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:20]


def _title(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", text).strip(" \t\r\n，,。.;；:：!！?？\"'“”‘’")


def topic_id(title: str) -> str:
    """Return an import-order-independent identifier for a non-empty title."""
    if not isinstance(title, str) or not _title(title):
        raise ValueError("知识点标题不能为空")
    return "t_" + _hash(_title(title).casefold())


_EXAM = re.compile(
    r"必考|一定(?:会|要)?考|肯定(?:会|要)?考|"
    r"(?:期末|考试)(?:一定|肯定)?(?:会|要)考"
)
_SIGNAL = re.compile(_EXAM.pattern + r"|重点")
_SUMMARY_SIGNAL = re.compile(r"重点|必考|一定(?:会|要)?考|强调|重要|关键|需要掌握|必须掌握")
_NEGATIVE = re.compile(
    r"不一定|不见得|未必|不(?:会|要|用|必)?考|没(?:有)?(?:说|讲|要求).{0,10}(?:考|重点)|"
    r"(?:不是|并非|非|不属于).{0,10}重点|"
    r"不(?:用|必|需要).{0,8}重点|取消.{0,10}(?:考|重点)|不列入.{0,10}(?:考|重点)|"
    r"(?:不能|无法|不敢|不予|没有|未)(?:保证|确定|确认|断言|承诺|明确).{0,15}(?:考|重点)|"
    r"(?:不代表|不意味着|不等于).{0,15}(?:会考|必考)"
)
_CONDITIONAL = re.compile(
    r"如果|假如|要是|若(?:是|要|考|今年|有)|可能|也许|或许|视情况|取决于|"
    r"有机会考|尚未确定|还没确定|是否|会不会|是不是"
)
_HISTORY = re.compile(r"去年|往年|前年|上一届|上届|上次|曾经|以前|过去(?:考|的考试)")
_REPORTED = re.compile(
    r"(?:有人|听说|据说|转述|引用|传言)|不是说|并不是说|"
    r"(?:同学|学生|教材|课本|网上|资料|文章).{0,8}(?:说|写|标|认为)|"
    r"(?:比如|例如|举例).{0,40}(?:重点|必考)|(?:这种|那种|这类).{0,4}(?:说法|表述)"
)
_QUESTION = re.compile(
    r"(?:必考|会考|重点)(?:的)?(?:吗|么|呢|吧|什么|哪些|哪一|哪里)|"
    r"(?:什么|哪些|哪里|哪一).{0,12}(?:必考|会考|重点)"
)
_META = re.compile(r"(?:重点|必考|一定会考)(?:这个词|一词|二字|两个字|的含义|的意思)")
_CLAUSE_SPLIT = re.compile(r"[。！？!?；;，,\n]")
_STUDENT_LABEL = re.compile(
    r"(?:^|[。！？!?；;，,:：\n])\s*(?:学生|同学|[\u4e00-\u9fff]{1,6}同学|student)(?:说)?\s*[:：]", re.I
)
_UNMAPPED_LABEL = re.compile(r"(?:^|[。！？!?；;，,\n])\s*(?:说话人|发言人|讲者|speaker)\s*[\d一二三四五六七八九十]+\s*[:：]", re.I)
_SPEAKER_PREFIX = re.compile(
    r"^\s*(?:\[\d{1,2}:\d{2}(?::\d{2})?\]\s*)?"
    r"(?:(?:老师|教师|teacher|professor)(?:说)?\s*[:：]\s*)", re.I
)
_LEAD = re.compile(
    r"^(?:(?:大家|同学们?)(?:请)?(?:注意|记住|听好)|请(?:大家)?(?:注意|记住)|"
    r"注意|记住|关于|对于|今天我们讲的|我们讲的|今天讲的|我们要讲的)\s*[，,:：]?\s*"
)
_VAGUE = re.compile(
    r"^(?:(?:这|那|该|上述|前面|这里|那里|本)(?:一|些|个|道|部分|章|节)?"
    r"(?:内容|知识点|题目|题|证明题|证明|公式|概念|方法|部分|一点)?|"
    r"它|这一点|那一点|本题|重点|考试|期末|老师|大家|是|为|的|要|会|考|吗|吧|啊|嘛|呀)$"
)


def _clean_topic(text: str) -> str:
    value = _title(text)
    for _ in range(3):
        reduced = _LEAD.sub("", value)
        if reduced == value:
            break
        value = reduced
    value = re.sub(r"(?:，|,)?(?:期末|考试|今年|这次)$", "", value)
    value = re.sub(r"(?:是|属于|作为|列为)(?:(?:本章|本节|考试|期末考试)(?:的)?)?$", "", value)
    value = _title(value)
    # A dangling connective is not evidence of the missing object.
    if not value or _VAGUE.fullmatch(value):
        return ""
    if len(value) > 100:
        return ""
    return value


def _guess_topics(text: str, summary=False) -> list[str]:
    body = _SPEAKER_PREFIX.sub("", text).strip()
    # Limit extraction to the clause carrying the signal, while keeping the
    # complete segment as evidence and checking all of it for qualifying words.
    clauses = _CLAUSE_SPLIT.split(body)
    patterns = (
        r"(?:重点|必考(?:内容|知识点)?)(?:是|为|在于|包括|[:：])\s*(.+)$",
        r"重点(?:掌握|复习|学习|理解)的(?:内容|知识点)(?:是|为|[:：])\s*(.+)$",
        r"(?:重点掌握|重点复习|重点学习|重点理解)\s*(.+)$",
        r"(?:一定(?:会|要)?考|肯定(?:会|要)?考|(?:期末|考试)(?:一定|肯定)?(?:会|要)考)\s*(.+)$",
        r"^(.+?)(?:是|属于|作为|列为)(?:(?:一个|主要|本章|本节|考试|期末考试)(?:的)?)?(?:重点|必考)",
        r"^(.+?)(?:期末|考试)?(?:一定(?:会|要)?考|肯定(?:会|要)?考|必考)",
        r"^(.+?)\s*[，,:：]\s*(?:(?:本章|本节|考试|期末考试)(?:的)?)?重点(?:内容)?$",
    )
    titles = []
    for clause in clauses:
        if not (_SUMMARY_SIGNAL if summary else _SIGNAL).search(clause):
            continue
        for pattern in patterns:
            match = re.search(pattern, clause)
            if match:
                title = _clean_topic(match.group(1))
                if title:
                    if title not in titles:
                        titles.append(title)
                    break
        else:
            if summary:
                match = re.search(r"(?:老师|教师)(?:说|强调|指出|提醒)\s*[:：]?\s*(.+?)(?:很重要|重要|是关键)", clause)
                if match:
                    title = _clean_topic(match.group(1))
                    if title and title not in titles:
                        titles.append(title)
    return titles


def _reason(text: str, kind: str, role: str, title: str) -> str | None:
    # Check the entire segment, not just the signal-bearing clause. A later
    # "但不一定" must not leave the earlier phrase certified as exam content.
    if _STUDENT_LABEL.search(text):
        return "包含学生发言标签，整段不能继承教师身份"
    if _UNMAPPED_LABEL.search(text):
        return "包含尚未对应教师身份的说话人标签"
    if _NEGATIVE.search(text):
        return "包含否定或取消表述，不能认定为正向明确重点"
    if _CONDITIONAL.search(text):
        return "包含条件或不确定表述"
    if "?" in text or "？" in text or _QUESTION.search(text):
        return "疑问表述需要核对上下文"
    if _HISTORY.search(text):
        return "往年或历史表述不能作为本次考试承诺"
    if _REPORTED.search(text):
        return "引用或转述需要核对原始教师表述"
    if _META.search(text):
        return "讨论措辞本身不能作为对知识点的重点指定"
    if kind == "summary":
        return "纪要缺少可核对的教师逐字证据"
    if role != "teacher":
        return "学生发言不能作为教师重点" if role == "student" else "教师身份尚未确认"
    if not title:
        return "指代不明或缺少具体知识点"
    return None


def extract_focus(
    source_id: str,
    segments: list[dict],
    kind: str,
    declared_speaker: str = "unknown",
) -> tuple[list[dict], list[dict]]:
    """Extract formal teacher evidence and reviewable candidates.

    ``kind=summary`` always yields candidates only. ``material``, ``exam``,
    ``exercise`` and ``solution`` never create classroom candidates or evidence.
    Explicit segment roles take precedence over the source-level declaration.
    No time, speaker label or missing knowledge-point name is fabricated.
    """
    if not isinstance(source_id, str) or not source_id.strip():
        raise ValueError("来源编号不能为空")
    if kind not in {"transcript", "summary"}:
        return [], []
    formal: dict[str, dict] = {}
    candidates: dict[str, dict] = {}
    for segment in segments:
        text = segment.get("text", "")
        if not isinstance(text, str):
            raise ValueError("课堂片段正文必须是文本")
        if not (_SUMMARY_SIGNAL if kind == "summary" else _SIGNAL).search(text):
            continue
        locator = deepcopy(segment.get("locator") or {})
        role = segment.get("speaker", "unknown")
        if role not in {"teacher", "student"}:
            role = declared_speaker if declared_speaker in {"teacher", "student"} else "unknown"
        titles = _guess_topics(text, summary=kind == "summary")
        title = titles[0] if titles else ""
        claim = "exam_assured" if _EXAM.search(text) else "priority"
        reason = _reason(text, kind, role, title)
        if reason:
            candidate = {
                "id": "c_" + _hash(source_id, locator, text),
                "source_id": source_id,
                "locator": locator,
                "quote": text,
                "topic_guess": title,
                "claim_type": claim,
                "reason": reason,
                "status": "pending",
            }
            candidates[candidate["id"]] = candidate
            continue
        evidence = {"source_id": source_id, "locator": locator, "quote": text}
        for title in titles:
            tid = topic_id(title)
            # An exam assertion about a separate clause must not upgrade this
            # topic's explicit priority to an assured exam claim.
            topic_claim = claim
            matching_clauses = [c for c in _CLAUSE_SPLIT.split(text) if title in _title(c)]
            if matching_clauses and not any(_EXAM.search(c) for c in matching_clauses):
                topic_claim = "priority"
            fid = "f_" + _hash("teacher_explicit", tid, topic_claim)
            if fid not in formal:
                formal[fid] = {
                    "id": fid,
                    "topic_id": tid,
                    "topic_title": title,
                    "source_type": "teacher_explicit",
                    "claim_type": topic_claim,
                    "evidence": [],
                    "status": "active",
                }
            if evidence not in formal[fid]["evidence"]:
                formal[fid]["evidence"].append(deepcopy(evidence))
    return list(formal.values()), list(candidates.values())


_SCOPE_UNCERTAIN = re.compile(
    r"不一定|未必|不见得|不能保证|无法保证|不代表|不是不|并非不|不能说不|不是说.{0,10}不考|"
    r"(?:不考|取消)(?:了|的)?(?:吗|么|吧|呢)"
)
_SCOPE_EXCLUSION = re.compile(
    r"^(.+?)\s*(?:(?:本次|这次|本学期|今年)?(?:期末|考试)(?:中)?|本学期|今年)?"
    r"(?:不考|不再考|不会考|不列入考试范围|不在考试范围内|不纳入考试范围)(?:了)?\s*$"
)
_SCOPE_CANCEL_PREFIX = re.compile(
    r"^(?:取消|撤回)\s*(?:之前|原先|原来)?\s*(.+?)(?:的)?"
    r"(?:必考|一定(?:会|要)?考|会考)(?:(?:的|这一)?(?:说法|要求|安排))?\s*$"
)
_SCOPE_CANCEL_SUFFIX = re.compile(
    r"^(?:(?:之前|原先|原来)(?:说|讲)?的?)?\s*(.+?)"
    r"(?:之前|原先|原来)?(?:必考|一定(?:会|要)?考|会考)"
    r"(?:(?:的|这一)?(?:说法|要求|安排))?(?:已|现已)?(?:取消|撤回)(?:了)?\s*$"
)


def extract_scope_updates(
    source_id: str,
    segments: list[dict],
    kind: str,
    declared_speaker: str = "unknown",
) -> list[dict]:
    """Return concrete, current teacher cancellations for service reconciliation.

    This deliberately matches exact knowledge-point titles only. It does not
    resolve pronouns, infer that an entire chapter excludes all child topics,
    or retract a user's own study priorities. The service owns reconciliation
    against existing teacher focus and preservation of this evidence.
    """
    if not isinstance(source_id, str) or not source_id.strip():
        raise ValueError("来源编号不能为空")
    if kind != "transcript":
        return []
    results = {}
    for segment in segments:
        text = segment.get("text", "")
        if not isinstance(text, str):
            raise ValueError("课堂片段正文必须是文本")
        role = segment.get("speaker", "unknown")
        if role not in {"teacher", "student"}:
            role = declared_speaker if declared_speaker in {"teacher", "student"} else "unknown"
        if role != "teacher" or _STUDENT_LABEL.search(text) or _UNMAPPED_LABEL.search(text):
            continue
        if (_CONDITIONAL.search(text) or _HISTORY.search(text) or _REPORTED.search(text)
                or _META.search(text) or _SCOPE_UNCERTAIN.search(text)
                or "?" in text or "？" in text):
            continue
        body = _SPEAKER_PREFIX.sub("", text).strip()
        for clause in _CLAUSE_SPLIT.split(body):
            clause = clause.strip()
            for pattern in (_SCOPE_EXCLUSION, _SCOPE_CANCEL_PREFIX, _SCOPE_CANCEL_SUFFIX):
                match = pattern.fullmatch(clause)
                if not match:
                    continue
                title = _clean_topic(match.group(1))
                # Do not turn an unresolved old statement into an apparent topic.
                if (not title or title in {"一定", "肯定", "会", "要", "必", "说的", "讲的"}
                        or re.search(r"^(?:之前|原先|原来)(?:说|讲)?的?$", title)):
                    continue
                locator = deepcopy(segment.get("locator") or {})
                result = {
                    "topic_id": topic_id(title),
                    "topic_title": title,
                    "source_id": source_id,
                    "locator": locator,
                    "quote": text,
                    "action": "retract",
                }
                results[_hash(source_id, locator, text, title)] = result
                break
    return list(results.values())


def make_user_focus(title: str, note: str, candidate: dict | None = None) -> dict:
    """Create a user-designated priority without relabelling it as teacher speech.

    ``note`` is the user's actual designation. A confirmed candidate's original
    location is retained as supporting context, with the original source type
    explicitly distinguished from the user designation.
    """
    if not isinstance(title, str) or not _title(title):
        raise ValueError("知识点标题不能为空")
    if not isinstance(note, str) or not note.strip():
        raise ValueError("请提供用户指定重点的实际说明")
    title = _title(title)
    tid = topic_id(title)
    result = {
        "id": "f_" + _hash("user_designated", tid),
        "topic_id": tid,
        "topic_title": title,
        "source_type": "user_designated",
        "claim_type": "user_priority",
        "evidence": [{"source_id": None, "locator": {}, "quote": note}],
        "status": "active",
    }
    if candidate:
        result["confirmed_candidate_id"] = candidate["id"]
        result["candidate_context"] = {
            "source_id": candidate.get("source_id"),
            "locator": deepcopy(candidate.get("locator") or {}),
            "quote": candidate.get("quote", ""),
            "reason": candidate.get("reason", ""),
        }
    return result
