from __future__ import annotations

import re
from collections import Counter, defaultdict
from statistics import mean, pstdev
from typing import Any, Dict, List, Optional, Tuple

from app.engine.dimensions import DIMENSIONS

_PAGE_MARK_RE = re.compile(r"\[PAGE:(\d+)\]")
_CHAR_LOC_RE = re.compile(r"char:(\d+)(?:-(\d+))?")
DEFAULT_TOTAL_SCORE_SCALE_MAX = 100.0
DEFAULT_DIMENSION_MAX_SCORE = 10.0
EVIDENCE_CONTEXT_RADIUS = 90
MIN_MEANINGFUL_ORIGINAL_TEXT_CHARS = 15
SEMANTIC_EXCERPT_TARGET_CHARS = 60
SEMANTIC_EXCERPT_MAX_CHARS = 220
SEMANTIC_NEIGHBOR_SCAN_STEPS = 4

_OCR_NOISE_RE = re.compile(
    r"\b(?:gray|binary|rgb|localx)\S*\s+score=\d+(?:\.\d+)?",
    re.IGNORECASE,
)
_DOT_LEADER_RE = re.compile(r"[\.。．·•…]{4,}")
_EMPTY_TEXT_RE = re.compile(r"^[\s\-—_=·•|]+$")
_ANTI_BOILERPLATE_PREFIX_RE = re.compile(
    r"^(?:由(?:项目经理|技术负责人|施工员|专业工程师|安全员|项目总工|生产经理)[^，。；]{0,24}[，,；;]\s*)"
)

OPTIMIZATION_SYSTEM_PROMPT = """
你是一位拥有20年经验的资深工程标书总工。你的任务是对不合格的施组段落进行外科手术式的精准改写或原位扩写。
必须紧紧咬住提供的【原文内容】做针对性升华与改写，绝不允许泛化套话、千篇一律的模板化起手句。
如果判断为原句替换，直接输出可覆盖原文的完整专业段落；如果判断为原位补充，直接输出可插入原文的精炼专业内容或表格字段。
诸如“字数对等”“避免排版膨胀”“保持精简”等约束仅用于内部思考，绝不允许出现在最终结果中。
""".strip()

OPTIMIZATION_GENERATION_FORBIDDEN_OUTPUTS = (
    "排版约束",
    "字数对等",
    "避免排版膨胀",
    "保持精简",
    "你的优化和修改目标是",
    "严禁长篇大论",
)

_GENERIC_REWRITE_REPLACEMENTS = {
    "【责任岗位】": "项目经理",
    "【频次】": "每周1次（关键节点加密到每日）",
    "【阈值/参数】": "偏差≤5%，超阈值24小时内闭环",
    "【验收动作】": "报验+现场验收+签认留痕",
}


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _safe_str(v: Any) -> str:
    return str(v or "").strip()


def _normalize_total_score_scale(score_scale_max: object) -> float:
    try:
        numeric = int(float(score_scale_max))
    except (TypeError, ValueError):
        numeric = int(DEFAULT_TOTAL_SCORE_SCALE_MAX)
    return 5.0 if numeric == 5 else 100.0


def _total_score_scale_label(score_scale_max: object) -> str:
    return "5分制" if _normalize_total_score_scale(score_scale_max) == 5.0 else "100分制"


def _format_total_score_text(score: object, score_scale_max: object) -> str:
    value = _safe_float(score)
    scale = _normalize_total_score_scale(score_scale_max)
    return f"{value:.4f} / 5" if scale == 5.0 else f"{value:.2f}分"


def _clean_snippet(text: str, limit: int = 90) -> str:
    if not text:
        return ""
    s = " ".join(str(text).split())
    s = _PAGE_MARK_RE.sub("", s).strip()
    return s[:limit] + ("..." if len(s) > limit else "")


def _strip_extraction_noise(text: str) -> str:
    if not text:
        return ""
    clean = re.sub(r"\[[^\]]+\]", " ", str(text))
    clean = _OCR_NOISE_RE.sub(" ", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def _clean_original_excerpt(text: str, limit: int = SEMANTIC_EXCERPT_MAX_CHARS) -> str:
    clean = _strip_extraction_noise(text)
    if not clean:
        return ""
    clean = clean[:limit].rstrip()
    return clean


def _looks_like_directory_entry(text: str) -> bool:
    clean = _clean_original_excerpt(text, limit=320)
    if not clean:
        return True
    if _DOT_LEADER_RE.search(clean):
        return True
    if re.search(r"[\.。．·•…]{3,}\s*\d+\s*$", clean):
        return True
    if re.fullmatch(r"[第章节目录\s\d一二三四五六七八九十百千\.\-—_（）()]+", clean):
        return True
    return False


def _is_meaningful_original_text(text: str) -> bool:
    clean = _clean_original_excerpt(text, limit=320)
    if not clean:
        return False
    if _EMPTY_TEXT_RE.fullmatch(clean):
        return False
    if _looks_like_directory_entry(clean):
        return False
    dense = re.sub(r"\s+", "", clean)
    if len(dense) < MIN_MEANINGFUL_ORIGINAL_TEXT_CHARS:
        return False
    alnum = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", clean)
    return len(alnum) >= max(8, MIN_MEANINGFUL_ORIGINAL_TEXT_CHARS // 2)


def _build_text_line_rows(text: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not text:
        return rows
    cursor = 0
    page_no = 0
    for chunk in text.splitlines(True) or [text]:
        raw = chunk.rstrip("\r\n")
        start = cursor
        cursor += len(chunk)
        marker = _PAGE_MARK_RE.fullmatch(raw.strip())
        if marker:
            page_no = int(marker.group(1))
            continue
        rows.append(
            {
                "start": start,
                "end": cursor,
                "text": raw,
                "page_no": page_no,
            }
        )
    return rows


def _line_is_blank(row: Dict[str, Any]) -> bool:
    return not _safe_str(row.get("text"))


def _line_is_table_like(row: Dict[str, Any]) -> bool:
    text = _safe_str(row.get("text"))
    if not text:
        return False
    return ("|" in text) or ("\t" in text) or bool(re.search(r"\S\s{2,}\S", text))


def _find_line_index_for_pos(rows: List[Dict[str, Any]], pos: int) -> int:
    if pos < 0:
        return -1
    for idx, row in enumerate(rows):
        start = int(row.get("start") or 0)
        end = int(row.get("end") or start)
        if start <= pos < max(start + 1, end):
            return idx
    return -1


def _excerpt_from_line_range(rows: List[Dict[str, Any]], start_idx: int, end_idx: int) -> str:
    pieces = [
        _clean_original_excerpt(rows[idx].get("text") or "", limit=SEMANTIC_EXCERPT_MAX_CHARS)
        for idx in range(start_idx, end_idx + 1)
        if not _line_is_blank(rows[idx])
    ]
    return _clean_original_excerpt(" ".join(piece for piece in pieces if piece))


def _coalesce_excerpt_around_index(rows: List[Dict[str, Any]], idx: int) -> str:
    row = rows[idx]
    page_no = int(row.get("page_no") or 0)
    if _line_is_table_like(row):
        start_idx = idx
        end_idx = idx
        while start_idx > 0:
            prev = rows[start_idx - 1]
            if (
                int(prev.get("page_no") or 0) != page_no
                or _line_is_blank(prev)
                or not _line_is_table_like(prev)
            ):
                break
            start_idx -= 1
        while end_idx + 1 < len(rows):
            nxt = rows[end_idx + 1]
            if (
                int(nxt.get("page_no") or 0) != page_no
                or _line_is_blank(nxt)
                or not _line_is_table_like(nxt)
            ):
                break
            end_idx += 1
        return _excerpt_from_line_range(rows, start_idx, end_idx)

    start_idx = idx
    end_idx = idx
    while start_idx > 0:
        prev = rows[start_idx - 1]
        if int(prev.get("page_no") or 0) != page_no or _line_is_blank(prev):
            break
        start_idx -= 1
        if len(_excerpt_from_line_range(rows, start_idx, end_idx)) >= SEMANTIC_EXCERPT_TARGET_CHARS:
            break
    while end_idx + 1 < len(rows):
        nxt = rows[end_idx + 1]
        if int(nxt.get("page_no") or 0) != page_no or _line_is_blank(nxt):
            break
        end_idx += 1
        if len(_excerpt_from_line_range(rows, start_idx, end_idx)) >= SEMANTIC_EXCERPT_TARGET_CHARS:
            break
    return _excerpt_from_line_range(rows, start_idx, end_idx)


def _resolve_semantic_excerpt(
    text: str,
    *,
    locator: str,
    snippet: str,
    markers: List[Tuple[int, int]],
) -> Dict[str, Any]:
    rows = _build_text_line_rows(text)
    if not rows:
        clean = _clean_original_excerpt(snippet)
        page_hint = _resolve_page_hint(locator, clean, text, markers) if clean else "页码未知"
        return {
            "excerpt": clean,
            "page_hint": page_hint,
            "before": "（无）",
            "after": "（无）",
        }

    pos = _char_pos_from_locator(locator)
    if pos < 0:
        pos = _find_pos_by_snippet(text, snippet)
    idx = _find_line_index_for_pos(rows, pos)
    if idx < 0:
        idx = 0

    candidate_indexes = [idx]
    for step in range(1, SEMANTIC_NEIGHBOR_SCAN_STEPS + 1):
        candidate_indexes.extend([idx + step, idx - step])
    excerpt = ""
    chosen_idx = idx
    for candidate in candidate_indexes:
        if candidate < 0 or candidate >= len(rows):
            continue
        candidate_excerpt = _coalesce_excerpt_around_index(rows, candidate)
        if _is_meaningful_original_text(candidate_excerpt):
            excerpt = candidate_excerpt
            chosen_idx = candidate
            break
    if not excerpt:
        clean_snippet = _clean_original_excerpt(snippet)
        if _is_meaningful_original_text(clean_snippet):
            excerpt = clean_snippet
        else:
            excerpt = clean_snippet

    page_no = int(rows[chosen_idx].get("page_no") or 0)
    page_hint = (
        _format_page_hint(page_no, exact=True)
        if page_no > 0
        else _resolve_page_hint(
            locator,
            excerpt or snippet,
            text,
            markers,
        )
    )
    before = "（无）"
    after = "（无）"
    for prev_idx in range(chosen_idx - 1, -1, -1):
        if _line_is_blank(rows[prev_idx]):
            break
        prev_text = _clean_original_excerpt(rows[prev_idx].get("text") or "", limit=140)
        if _is_meaningful_original_text(prev_text):
            before = prev_text
            break
    for next_idx in range(chosen_idx + 1, len(rows)):
        if _line_is_blank(rows[next_idx]):
            break
        next_text = _clean_original_excerpt(rows[next_idx].get("text") or "", limit=140)
        if _is_meaningful_original_text(next_text):
            after = next_text
            break

    return {
        "excerpt": excerpt,
        "page_hint": page_hint,
        "before": before,
        "after": after,
    }


def _materialize_template(template: str) -> str:
    out = template or ""
    for src, dst in _GENERIC_REWRITE_REPLACEMENTS.items():
        out = out.replace(src, dst)
    return out


def _dim_name(dim_id: str) -> str:
    d = DIMENSIONS.get(dim_id) or {}
    return str(d.get("name") or dim_id)


def _dim_module(dim_id: str) -> str:
    d = DIMENSIONS.get(dim_id) or {}
    return str(d.get("module") or "")


def _score_awareness_meta(submission: Dict[str, Any]) -> Dict[str, Any]:
    report = submission.get("report") or {}
    meta = report.get("meta") if isinstance(report, dict) else {}
    meta = meta if isinstance(meta, dict) else {}
    awareness = submission.get("score_self_awareness")
    awareness = awareness if isinstance(awareness, dict) else meta.get("score_self_awareness")
    awareness = awareness if isinstance(awareness, dict) else {}
    reasons = awareness.get("reasons") if isinstance(awareness.get("reasons"), list) else []
    confidence_level = _safe_str(
        submission.get("score_confidence_level")
        or meta.get("score_confidence_level")
        or awareness.get("level")
        or ""
    )
    confidence_score = round(_safe_float(awareness.get("score_0_100")), 2)
    if confidence_score <= 0 and confidence_level:
        confidence_score = {
            "high": 82.0,
            "medium": 58.0,
            "low": 26.0,
        }.get(confidence_level, 0.0)
    structured_quality_avg = round(_safe_float(awareness.get("structured_quality_avg")), 4)
    structured_quality_type_rate = round(
        _safe_float(awareness.get("structured_quality_type_rate")),
        4,
    )
    retrieval_file_coverage_rate = round(
        _safe_float(awareness.get("retrieval_file_coverage_rate")),
        4,
    )
    dimension_coverage_rate = round(_safe_float(awareness.get("dimension_coverage_rate")), 4)
    return {
        "score_confidence_level": confidence_level,
        "score_confidence_score": confidence_score,
        "score_confidence_reason": _safe_str(reasons[0]) if reasons else "",
        "structured_quality_avg": structured_quality_avg,
        "structured_quality_type_rate": structured_quality_type_rate,
        "retrieval_file_coverage_rate": retrieval_file_coverage_rate,
        "dimension_coverage_rate": dimension_coverage_rate,
    }


def build_compare_sort_fields(submission: Dict[str, Any]) -> Dict[str, Any]:
    awareness_meta = _score_awareness_meta(submission)
    total_score = round(_safe_float(submission.get("total_score")), 4)
    confidence_signal = max(
        0.0,
        min(1.0, _safe_float(awareness_meta.get("score_confidence_score")) / 100.0),
    )
    structured_quality_avg = max(
        0.0,
        min(1.0, _safe_float(awareness_meta.get("structured_quality_avg"))),
    )
    structured_quality_type_rate = max(
        0.0,
        min(1.0, _safe_float(awareness_meta.get("structured_quality_type_rate"))),
    )
    retrieval_file_coverage_rate = max(
        0.0,
        min(1.0, _safe_float(awareness_meta.get("retrieval_file_coverage_rate"))),
    )
    dimension_coverage_rate = max(
        0.0,
        min(1.0, _safe_float(awareness_meta.get("dimension_coverage_rate"))),
    )
    evidence_bonus = round(
        min(
            0.45,
            confidence_signal * 0.22
            + structured_quality_avg * 0.12
            + structured_quality_type_rate * 0.07
            + retrieval_file_coverage_rate * 0.03
            + dimension_coverage_rate * 0.01,
        ),
        4,
    )
    ranking_sort_score = round(total_score + evidence_bonus, 4)
    return {
        **awareness_meta,
        "ranking_evidence_bonus": evidence_bonus,
        "ranking_sort_score": ranking_sort_score,
    }


def compare_sort_key(submission: Dict[str, Any]) -> Tuple[float, float, float, float, float, str]:
    sort_fields = build_compare_sort_fields(submission)
    return (
        _safe_float(sort_fields.get("ranking_sort_score")),
        _safe_float(submission.get("total_score")),
        _safe_float(sort_fields.get("score_confidence_score")),
        _safe_float(sort_fields.get("structured_quality_avg")),
        _safe_float(sort_fields.get("structured_quality_type_rate")),
        _safe_str(submission.get("filename") or submission.get("id")),
    )


def _build_page_markers(text: str) -> List[Tuple[int, int]]:
    markers: List[Tuple[int, int]] = []
    for m in _PAGE_MARK_RE.finditer(text or ""):
        page_no = int(m.group(1))
        markers.append((m.start(), page_no))
    markers.sort(key=lambda x: x[0])
    return markers


def _format_page_hint(page_no: Optional[int], exact: bool) -> str:
    if not page_no or page_no <= 0:
        return "页码未知"
    return f"第{page_no}页" if exact else f"约第{page_no}页"


def _position_to_page(pos: int, text: str, markers: List[Tuple[int, int]]) -> str:
    if pos < 0:
        return "页码未知"
    if markers:
        page_no = markers[0][1]
        for marker_pos, marker_page in markers:
            if marker_pos <= pos:
                page_no = marker_page
            else:
                break
        return _format_page_hint(page_no, exact=True)
    approx = int(pos / 1800) + 1
    if approx <= 0:
        approx = 1
    if not text:
        return "页码未知"
    return _format_page_hint(approx, exact=False)


def _char_pos_from_locator(locator: str) -> int:
    loc = _safe_str(locator)
    if not loc:
        return -1
    m = _CHAR_LOC_RE.search(loc)
    if not m:
        return -1
    return int(m.group(1))


def _char_span_from_locator(locator: str) -> Tuple[int, int]:
    loc = _safe_str(locator)
    if not loc:
        return -1, -1
    m = _CHAR_LOC_RE.search(loc)
    if not m:
        return -1, -1
    start = int(m.group(1))
    end = int(m.group(2) or (start + 1))
    if end <= start:
        end = start + 1
    return start, end


def _find_pos_by_snippet(text: str, snippet: str) -> int:
    t = _safe_str(text)
    s = _safe_str(snippet)
    if not t or not s:
        return -1
    compact = " ".join(s.split())
    if not compact:
        return -1
    i = t.find(compact)
    if i >= 0:
        return i
    if len(compact) > 16:
        i2 = t.find(compact[:16])
        if i2 >= 0:
            return i2
    return -1


def _resolve_page_hint(
    locator: str, snippet: str, text: str, markers: List[Tuple[int, int]]
) -> str:
    pos = _char_pos_from_locator(locator)
    if pos < 0:
        pos = _find_pos_by_snippet(text, snippet)
    return _position_to_page(pos, text, markers)


def _build_context_window(
    text: str,
    *,
    locator: str,
    snippet: str,
    page_hint: str,
) -> str:
    t = _safe_str(text)
    if not t:
        fallback = _clean_original_excerpt(snippet, limit=180) or "未提取到证据片段。"
        return "\n".join(
            [
                f"页码：{page_hint}",
                "前文：（无）",
                f"命中：{fallback}",
                "后文：（无）",
            ]
        )
    semantic = _resolve_semantic_excerpt(
        t,
        locator=locator,
        snippet=snippet,
        markers=_build_page_markers(t),
    )
    before = semantic.get("before") or "（无）"
    hit = _clean_original_excerpt(semantic.get("excerpt") or snippet, limit=140) or "（无）"
    after = semantic.get("after") or "（无）"

    return "\n".join(
        [
            f"页码：{page_hint}",
            f"前文：{before}",
            f"命中：{hit}",
            f"后文：{after}",
        ]
    )


def _build_evidence_row(
    *,
    snippet: str,
    locator: str,
    text: str,
    markers: List[Tuple[int, int]],
) -> Dict[str, Any]:
    semantic = _resolve_semantic_excerpt(
        text,
        locator=locator,
        snippet=snippet,
        markers=markers,
    )
    clean = _clean_original_excerpt(semantic.get("excerpt") or snippet, limit=220)
    if not clean and text:
        s, e = _char_span_from_locator(locator)
        if s >= 0:
            clean = _clean_original_excerpt(text[s:e], limit=220)
    page_hint = _safe_str(semantic.get("page_hint")) or _resolve_page_hint(
        locator, clean, text, markers
    )
    context_window = _build_context_window(
        text,
        locator=locator,
        snippet=clean,
        page_hint=page_hint,
    )
    return {
        "snippet": clean or _clean_snippet(snippet, limit=220),
        "original_text": clean,
        "locator": locator,
        "page_hint": page_hint,
        "context_window": context_window,
        "synthetic": False,
    }


def _extract_dim_evidence_rows(
    dim_row: Dict[str, Any],
    text: str,
    markers: List[Tuple[int, int]],
    max_items: int = 2,
    dim_id: str = "",
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, str]] = []
    seen = set()

    def _append(evidence_item: Dict[str, Any]) -> None:
        snippet = _safe_str(evidence_item.get("text") or evidence_item.get("text_snippet"))
        locator = _safe_str(evidence_item.get("locator"))
        if not snippet and not locator:
            return
        row = _build_evidence_row(
            snippet=snippet,
            locator=locator,
            text=text,
            markers=markers,
        )
        key = (
            _safe_str(row.get("snippet")),
            _safe_str(row.get("locator")),
            _safe_str(row.get("page_hint")),
        )
        if key in seen:
            return
        seen.add(key)
        rows.append(row)

    for ev in dim_row.get("evidence", []) or []:
        if len(rows) >= max_items:
            break
        _append(ev or {})
    if len(rows) < max_items:
        for sub in dim_row.get("sub_scores", []) or []:
            for ev in sub.get("evidence", []) or []:
                if len(rows) >= max_items:
                    break
                _append(ev or {})
            if len(rows) >= max_items:
                break
    if not rows:
        fb = _fallback_dimension_evidence(
            dim_id=dim_id, text=text, markers=markers, dim_row=dim_row
        )
        if fb.get("snippet"):
            rows.append(fb)
    return rows


def _extract_penalty_evidence_rows(
    penalty: Dict[str, Any],
    text: str,
    markers: List[Tuple[int, int]],
    max_items: int = 2,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    seen = set()
    for ev in penalty.get("evidence_refs", []) or []:
        snippet = _safe_str(ev.get("text_snippet") or ev.get("text"))
        locator = _safe_str(ev.get("locator"))
        if not snippet and not locator:
            continue
        row = _build_evidence_row(
            snippet=snippet,
            locator=locator,
            text=text,
            markers=markers,
        )
        key = (
            _safe_str(row.get("snippet")),
            _safe_str(row.get("locator")),
            _safe_str(row.get("page_hint")),
        )
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
        if len(rows) >= max_items:
            break
    if not rows:
        fb = _fallback_penalty_evidence(penalty=penalty, text=text, markers=markers)
        if fb.get("snippet"):
            rows.append(fb)
    return rows


def _build_dimension_actions(dim_id: str, delta: float) -> List[str]:
    dim = _dim_name(dim_id)
    common = [
        f"补齐「{dim}」中的责任岗位、频次、验收标准三类硬要素（避免空泛表述）。",
        f"将「{dim}」内容改写为“措施-执行-验收-复盘”四段结构。",
    ]
    specialized = {
        "07": "补充危大工程清单、专项方案审批链路、监测预警阈值与应急触发条件。",
        "09": "补充总控/月/周计划联动、关键线路节点、偏差阈值与纠偏责任。",
        "10": "补充专项工艺参数、工序衔接接口、样板先行与技术交底记录要求。",
        "11": "补充岗位配置表、班组能力矩阵、关键岗位持证要求与替补机制。",
        "13": "补充设备物资进退场计划、周转与备份方案、供应风险应对机制。",
        "14": "补充设计深化流程、会审机制、变更闭环、BIM/碰撞检查应用点。",
    }
    action = specialized.get(dim_id)
    if action:
        common.append(action)
    if delta >= 3:
        common.append("该维度分差较大，建议列为本轮优化优先级 P1。")
    return common[:3]


def _build_penalty_actions(code: str) -> List[str]:
    rules = {
        "P-EMPTY-002": [
            "删除空泛承诺，改为“责任人+频次+验收记录”的量化表达。",
            "每项措施至少配置一个可追踪输出（台账、记录表、验收单）。",
        ],
        "P-ACTION-002": [
            "每条措施补齐动作链：谁执行、何时执行、如何验收、偏差如何纠偏。",
            "将“加强/完善/做好”等抽象词替换为可检查动作与阈值。",
        ],
        "P-CONSIST-001": [
            "统一章节术语、时间频次、责任岗位口径，避免前后矛盾。",
            "对关键参数建立“一处定义、多处引用”机制，避免冲突。",
        ],
    }
    return rules.get(
        code,
        [
            "按“问题原因-整改动作-验收标准”重写对应段落。",
            "补充证据化描述（时间、数量、阈值、责任人）以降低再次扣分风险。",
        ],
    )


def _build_dimension_rewrite_plan(
    dim_id: str,
    *,
    delta_to_full: float,
    page_hint: str,
    evidence: str,
) -> str:
    dim = _dim_name(dim_id)
    template = _REWRITE_TEMPLATES.get(
        dim_id,
        "该段应明确责任岗位、执行频次、控制参数和验收动作，形成可复核的闭环表达。",
    )
    actions = _build_dimension_actions(dim_id, delta_to_full)
    lines = [
        f"改写目标：本轮补齐「{dim}」，预计追回 {delta_to_full:.2f} 分。",
        f"定位页码：{page_hint}",
        f"可参考原文：{evidence}",
        f"直接套用模板：{template}",
        "执行步骤：",
    ]
    for idx, action in enumerate(actions[:3], start=1):
        lines.append(f"{idx}. {action}")
    lines.append("4. 将“加强/完善/做好”改为“责任岗位+执行频次+阈值参数+验收动作”。")
    return "\n".join(lines)


def _build_dimension_acceptance_plan(dim_id: str) -> str:
    return "\n".join(
        [
            "核验清单：",
            "1. 是否明确责任岗位（到岗到人）；",
            "2. 是否给出执行频次（日/周/月/节点）；",
            "3. 是否给出阈值参数（数字、比例或范围）；",
            "4. 是否给出验收动作（报验/签认/旁站/隐蔽验收）；",
            f"5. 该维度（{dim_id}）段落是否可被第三方复核复现。",
        ]
    )


def _build_dimension_execution_checklist(dim_id: str, page_hint: str) -> str:
    dim_name = _dim_name(dim_id)
    return "\n".join(
        [
            f"执行清单（{dim_name}）",
            f"1. 打开{page_hint}，定位该维度原句并标注替换范围；",
            "2. 先写“谁负责+何时执行+控制参数+如何验收”四段动作链；",
            "3. 补齐可追踪交付物：记录表、验收单、旁站/签认依据；",
            "4. 同步检查关联章节口径（工期、责任岗位、节点参数）一致；",
            "5. 完成后在目录中标注“已整改维度+页码+复核人”。",
        ]
    )


def _build_penalty_rewrite_plan(
    code: str,
    *,
    points: float,
    page_hint: str,
    reason: str,
    evidence: str,
) -> str:
    actions = _build_penalty_actions(code)
    lines = [
        f"整改目标：消减 {code} 扣分（当前 {points:.2f} 分）。",
        f"定位页码：{page_hint}",
        f"触发原因：{reason}",
        f"可参考原文：{evidence}",
        "执行步骤：",
    ]
    for idx, action in enumerate(actions[:2], start=1):
        lines.append(f"{idx}. {action}")
    lines.append("3. 每条措施补齐动作链：谁执行、何时执行、如何验收、偏差如何纠偏。")
    lines.append("4. 将抽象词替换为可检查动作+量化阈值，保留台账/验收记录。")
    return "\n".join(lines)


def _build_penalty_acceptance_plan(code: str) -> str:
    return "\n".join(
        [
            "核验清单：",
            f"1. 触发 {code} 的原句是否已删除或改写；",
            "2. 是否补齐责任人、频次、参数、验收记录四要素；",
            "3. 是否新增可追踪输出（台账、记录表、验收单）；",
            "4. 复检同页与关联章节，避免同类表达再次触发。",
        ]
    )


def _build_penalty_execution_checklist(code: str, page_hint: str) -> str:
    return "\n".join(
        [
            f"执行清单（{code}）",
            f"1. 打开{page_hint}并定位触发扣分原句；",
            "2. 删除抽象表述，改为“责任人+频次+阈值+验收记录”量化句；",
            "3. 在同页补充可追踪证据载体（台账编号/记录表/验收单）；",
            "4. 回查上下文及其它章节，清除同类触发句，避免重复扣分；",
            "5. 复核通过后记录“整改前后句+页码+复核结论”。",
        ]
    )


_PENALTY_REWRITE_EXAMPLES = {
    "P-EMPTY-002": "本段应明确责任岗位、复核频次、闭环时限和验收记录，例如形成《周检记录表》并在48小时内完成问题闭环，由监理签认结果。",
    "P-ACTION-002": "本段应写清开工前技术交底、施工中巡检、完工后报验签认的完整动作链；若偏差>5%，24小时内完成整改并复验。",
    "P-CONSIST-001": "全篇统一工期口径为180天（开工日期以开工令为准），并在进度计划、节点清单、违约条款中一致引用同一参数。",
}


def _build_dimension_before_after_example(dim_id: str, evidence: str, page_hint: str) -> str:
    before = _safe_str(evidence) or "（请按定位页补提原句）"
    after = _materialize_template(
        _REWRITE_TEMPLATES.get(
            dim_id,
            "该段应明确责任岗位、执行频次、控制参数和验收动作，形成可复核的闭环表达。",
        )
    )
    return "\n".join(
        [
            f"定位：{page_hint}",
            f"改写前（摘录）：{before}",
            f"改写后（示例）：{after}",
        ]
    )


def _build_penalty_before_after_example(
    code: str, evidence: str, page_hint: str, reason: str
) -> str:
    before = _safe_str(evidence) or _safe_str(reason) or "（请按定位页补提触发扣分原句）"
    after = _PENALTY_REWRITE_EXAMPLES.get(
        code,
        "本段应明确责任岗位、执行频次、阈值参数和验收动作，形成台账留痕并闭环复验。",
    )
    return "\n".join(
        [
            f"定位：{page_hint}",
            f"改写前（摘录）：{before}",
            f"改写后（示例）：{after}",
        ]
    )


_DIMENSION_DIRECT_REPLACEMENT_OVERRIDES = {
    "05": "补齐应用场景、实施步骤、控制参数、验收标准和回退措施，并与现场关键工序一一对应。",
    "08": "补齐质量控制点、检查方法、频次、责任人和记录表单，所有质量动作落实到验收签认。",
    "10": "补齐方案编制、审核审批、技术交底、样板确认、实施控制和验收移交的时序链。",
    "12": "补齐前置条件、可穿插工序、禁止交叉情形、移交条件和对应记录表单。",
    "15": "补齐资源预警阈值、调配触发条件、责任岗位、补充时限和效果验证方式。",
    "16": "补齐验证项目、样板部位、通过标准、验收人和推广条件，保证内容可执行可复核。",
}

_DIMENSION_INSERTION_OVERRIDES = {
    "05": "四新技术应用按应用场景、实施步骤、控制参数、验收标准和回退措施逐项列示，并与对应工序直接关联。",
    "08": "质量控制与 ITP 简表至少列明控制点、检查方法、频次、责任人、合格标准和记录表单，采用紧凑字段式表达。",
    "10": "重点专项工程控制按方案编制、审批、交底、样板、实施和验收的顺序列出节点要求，优先压缩为一张表或两段短句。",
    "12": "施工流程穿插与移交内容应明确前置条件、可穿插工序、禁止交叉边界和移交条件，保持短句或表格化表达。",
    "15": "资源风险与调配条款应写清触发阈值、补充时限、责任岗位和效果验证，避免扩写成大段叙述。",
    "16": "技术措施可行性验证应明确验证对象、通过标准、验收动作和推广条件，采用精炼段落或表格句式。",
}


def _shrink_generated_copy(
    text: str,
    original_text: str = "",
    *,
    insert_mode: bool = False,
) -> str:
    cleaned = _strip_extraction_noise(text)
    for forbidden in OPTIMIZATION_GENERATION_FORBIDDEN_OUTPUTS:
        cleaned = cleaned.replace(forbidden, "")
    cleaned = _ANTI_BOILERPLATE_PREFIX_RE.sub("", cleaned)
    cleaned = re.sub(r"^(?:本节改为|补设|建议补充|直接套用模板：)\s*", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ，；")
    if not cleaned:
        return ""
    source_len = len(_clean_snippet(original_text, limit=280))
    limit = 150 if insert_mode else 145
    if source_len:
        dynamic_limit = int(source_len * (1.45 if insert_mode else 1.6))
        limit = max(56, min(limit, dynamic_limit))
    if len(cleaned) <= limit:
        return cleaned
    trimmed = cleaned[:limit].rstrip("，；、,. ")
    return trimmed + ("" if trimmed.endswith("。") else "。")


def _normalize_original_text(snippet: str, *, synthetic: bool = False) -> str:
    clean = _clean_original_excerpt(snippet, limit=220)
    if clean and not synthetic and _is_meaningful_original_text(clean):
        return clean
    return "当前正文未检出与该要求直接对应的有效原句，需在定位章节补设完整条款。"


def _write_location(page_hint: str, chapter_hint: str) -> str:
    page = _safe_str(page_hint) or "页码未知"
    chapter = _safe_str(chapter_hint) or "对应章节"
    return f"在{page}的「{chapter}」末尾"


def _append_targeted_fix_clause(original_text: str, fix_clause: str) -> str:
    base = _normalize_original_text(original_text)
    clause = _clean_original_excerpt(fix_clause, limit=180)
    if not clause:
        return base
    if not _is_meaningful_original_text(base):
        return clause
    trimmed = base.rstrip("，；。 ")
    if clause.startswith(("并", "同时", "且")):
        return f"{trimmed}，{clause}"
    return f"{trimmed}，并{clause}"


def _build_dimension_replacement_text(
    dim_id: str,
    *,
    evidence: str,
    original_text: str = "",
) -> str:
    extra = _DIMENSION_DIRECT_REPLACEMENT_OVERRIDES.get(dim_id)
    source = _normalize_original_text(original_text or evidence)
    if _is_meaningful_original_text(source):
        fix_clause = extra or _materialize_template(
            _REWRITE_TEMPLATES.get(
                dim_id,
                "明确责任岗位、执行频次、控制参数和验收动作，形成可复核的闭环表达。",
            )
        )
        return _shrink_generated_copy(
            _append_targeted_fix_clause(source, fix_clause),
            original_text or evidence,
        )
    parts = [
        _materialize_template(
            _REWRITE_TEMPLATES.get(
                dim_id,
                "该段应明确责任岗位、执行频次、控制参数和验收动作，形成可复核的闭环表达。",
            )
        )
    ]
    if extra:
        parts.append(extra)
    return _shrink_generated_copy(" ".join(parts), original_text or evidence)


def _build_dimension_insertion_guidance(
    dim_id: str,
    *,
    page_hint: str,
    chapter_hint: str,
    evidence: str,
    original_text: str = "",
) -> Dict[str, str]:
    content = _DIMENSION_INSERTION_OVERRIDES.get(
        dim_id,
        "补充一段紧凑条款，明确责任岗位、执行频次、控制参数、验收动作和记录表单，避免长篇扩写。",
    )
    compact = _shrink_generated_copy(content, original_text or evidence, insert_mode=True)
    location = _write_location(page_hint, chapter_hint)
    return {
        "insertion_guidance": f"{location}，补充以下完整内容：{compact}",
        "insertion_content": compact,
    }


def _build_penalty_replacement_text(code: str, *, evidence: str, reason: str) -> str:
    content = _PENALTY_REWRITE_EXAMPLES.get(
        code,
        "本段应明确责任岗位、执行频次、阈值参数和验收动作，形成台账留痕并闭环复验。",
    )
    source = _normalize_original_text(evidence)
    if _is_meaningful_original_text(source):
        content = _append_targeted_fix_clause(source, content)
    if reason and reason not in content:
        content = content + " 同时消除触发原因中的抽象承诺或缺失字段。"
    return _shrink_generated_copy(content, evidence)


def _recommendation_requires_insertion(evidence_row: Dict[str, Any], evidence: str) -> bool:
    if bool(evidence_row.get("synthetic")):
        return True
    clean = _safe_str(evidence_row.get("original_text") or evidence)
    if not clean:
        return True
    if clean.startswith("当前正文未检出"):
        return True
    if not _is_meaningful_original_text(clean):
        return True
    return "建议补充可验证证据" in clean


_DIM_PAGE_KEYWORDS = {
    "01": ["工程概况", "项目整体", "实施路径"],
    "02": ["安全生产", "安全管理", "隐患排查"],
    "03": ["文明施工", "扬尘", "围挡"],
    "04": ["材料", "采购", "部品"],
    "05": ["新工艺", "新技术", "四新"],
    "06": ["关键工序", "工序控制"],
    "07": ["重难点", "危大工程", "专项方案"],
    "08": ["质量保障", "质量管理"],
    "09": ["进度保障", "关键线路", "里程碑"],
    "10": ["专项施工", "工艺流程"],
    "11": ["人力资源", "人员配置", "组织机构"],
    "12": ["总体施工", "施工工艺"],
    "13": ["物资", "设备配置"],
    "14": ["设计协调", "深化设计"],
    "15": ["总体配置", "实施计划"],
    "16": ["技术措施", "可行性", "落地"],
}


_PENALTY_HINT_KEYWORDS = {
    "P-ACTION-002": ["实施", "执行", "报验", "验收", "负责", "项目经理", "技术负责人"],
    "P-EMPTY-002": ["加强", "完善", "做好", "确保", "严格", "落实"],
    "P-CONSIST-001": ["工期", "日历天", "节点", "里程碑", "前后", "冲突"],
}


def _chapter_hint(dim_id: str, category: str) -> str:
    if category == "扣分消减":
        return "扣分触发段落（同页整改并同步关联章节）"
    if category == "保优":
        return "全篇一致性复核"
    return str((DIMENSIONS.get(dim_id) or {}).get("name") or "对应维度章节")


def _snippet_by_pos(text: str, pos: int, window: int = 90) -> str:
    if not text:
        return ""
    p = max(0, min(len(text) - 1, int(pos)))
    left = max(0, p - window)
    right = min(len(text), p + window)
    return _clean_snippet(text[left:right], limit=220)


def _collect_hit_keywords(dim_row: Dict[str, Any]) -> List[str]:
    terms: List[str] = []
    for h in dim_row.get("hits", []) or []:
        if isinstance(h, str):
            terms.append(h)
        elif isinstance(h, dict):
            terms.append(_safe_str(h.get("keyword") or h.get("name") or h.get("text")))
    for sub in dim_row.get("sub_scores", []) or []:
        for h in sub.get("hits", []) or []:
            if isinstance(h, str):
                terms.append(h)
            elif isinstance(h, dict):
                terms.append(_safe_str(h.get("keyword") or h.get("name") or h.get("text")))
    dedup: List[str] = []
    for t in terms:
        tt = _safe_str(t)
        if len(tt) < 2:
            continue
        if tt not in dedup:
            dedup.append(tt)
    return dedup[:8]


def _synthesize_dimension_snippet(dim_id: str, dim_row: Dict[str, Any]) -> str:
    dim_name = _dim_name(dim_id)
    score = _safe_float(dim_row.get("score"))
    max_score = _safe_float(dim_row.get("max_score"), DEFAULT_DIMENSION_MAX_SCORE)
    hit_terms = _collect_hit_keywords(dim_row)
    pieces: List[str] = []
    if hit_terms:
        pieces.append("命中词: " + "、".join(hit_terms[:4]))
    sub_scores = dim_row.get("sub_scores", []) or []
    sub_labels: List[str] = []
    for item in sub_scores:
        sub_name = _safe_str(item.get("name") or item.get("label") or item.get("dimension"))
        if sub_name:
            sub_labels.append(sub_name)
    if sub_labels:
        pieces.append("子项: " + "、".join(sub_labels[:3]))
    if not pieces:
        pieces.append("建议补充可验证证据（责任岗位/频次/阈值/验收记录）")
    return f"{dim_name} 得分 {score:.2f}/{max_score:.2f}；" + "；".join(pieces)


def _synthesize_penalty_snippet(penalty: Dict[str, Any]) -> str:
    code = _safe_str(penalty.get("code") or "UNKNOWN")
    reason = _safe_str(penalty.get("reason") or "未提供原因")
    points = _safe_float(penalty.get("points"))
    reason_short = reason if len(reason) <= 110 else (reason[:107] + "...")
    return f"{code} 扣分 {points:.2f}：{reason_short}"


def _fallback_dimension_evidence(
    dim_id: str,
    text: str,
    markers: List[Tuple[int, int]],
    dim_row: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    keys = list(_DIM_PAGE_KEYWORDS.get(dim_id, []))
    dim_name = _dim_name(dim_id)
    if dim_name and dim_name not in keys:
        keys.append(dim_name)
    keys.extend(_collect_hit_keywords(dim_row or {}))
    dedup_keys: List[str] = []
    for kw in keys:
        kw_clean = _safe_str(kw)
        if len(kw_clean) < 2:
            continue
        if kw_clean not in dedup_keys:
            dedup_keys.append(kw_clean)
    for kw in dedup_keys:
        idx = text.find(kw)
        if idx < 0:
            continue
        return _build_evidence_row(
            snippet=_snippet_by_pos(text, idx),
            locator=f"char:{idx}-{idx + max(1, len(kw))}",
            text=text,
            markers=markers,
        )
    if text:
        idx = 0
        return _build_evidence_row(
            snippet=_snippet_by_pos(text, idx),
            locator="char:0-1",
            text=text,
            markers=markers,
        )
    synthetic = _synthesize_dimension_snippet(dim_id, dim_row or {})
    return {
        "snippet": synthetic,
        "locator": "",
        "page_hint": "页码未知",
        "context_window": "\n".join(
            [
                "页码：页码未知",
                "前文：（未提取到原文）",
                f"命中：{synthetic}",
                "后文：（建议按定位章节补录原文证据）",
            ]
        ),
        "synthetic": True,
    }


def _reason_tokens(reason: str) -> List[str]:
    raw = re.split(r"[，,。:：;；、\s]+", _safe_str(reason))
    tokens = [t for t in raw if len(t) >= 2]
    dedup: List[str] = []
    for t in tokens:
        if t not in dedup:
            dedup.append(t)
    return dedup[:8]


def _fallback_penalty_evidence(
    penalty: Dict[str, Any], text: str, markers: List[Tuple[int, int]]
) -> Dict[str, str]:
    code = _safe_str(penalty.get("code") or "")
    reason = _safe_str(penalty.get("reason") or "")
    keys = list(_PENALTY_HINT_KEYWORDS.get(code, [])) + _reason_tokens(reason)
    for kw in keys:
        idx = text.find(kw)
        if idx < 0:
            continue
        return _build_evidence_row(
            snippet=_snippet_by_pos(text, idx),
            locator=f"char:{idx}-{idx + max(1, len(kw))}",
            text=text,
            markers=markers,
        )
    if text:
        idx = 0
        return _build_evidence_row(
            snippet=_snippet_by_pos(text, idx),
            locator="char:0-1",
            text=text,
            markers=markers,
        )
    synthetic = _synthesize_penalty_snippet(penalty)
    return {
        "snippet": synthetic,
        "locator": "",
        "page_hint": "页码未知",
        "context_window": "\n".join(
            [
                "页码：页码未知",
                "前文：（未提取到原文）",
                f"命中：{synthetic}",
                "后文：（建议回到扣分触发页补录原句与整改后表述）",
            ]
        ),
        "synthetic": True,
    }


def _guess_dimension_page_hint(
    dim_id: str,
    dim_row: Dict[str, Any],
    text: str,
    markers: List[Tuple[int, int]],
) -> str:
    ev_rows = _extract_dim_evidence_rows(dim_row, text, markers, max_items=1, dim_id=dim_id)
    if ev_rows:
        return _safe_str(ev_rows[0].get("page_hint")) or "页码未知"

    for kw in _DIM_PAGE_KEYWORDS.get(dim_id, []):
        idx = text.find(kw)
        if idx >= 0:
            return _position_to_page(idx, text, markers)
    if text:
        return _position_to_page(0, text, markers)
    return "页码未知"


def _build_submission_optimization_cards(
    rankings: List[Dict[str, Any]],
    top: Dict[str, Any],
    all_dim_ids: List[str],
    *,
    total_score_scale_max: float = DEFAULT_TOTAL_SCORE_SCALE_MAX,
    focus_submission_id: str = "",
    isolated: bool = False,
) -> List[Dict[str, Any]]:
    cards: List[Dict[str, Any]] = []
    top_dims = (top.get("report") or {}).get("dimension_scores", {}) or {}
    top_total = _safe_float(top.get("total_score"))
    target_total_score = _normalize_total_score_scale(total_score_scale_max)
    focus_id = _safe_str(focus_submission_id)

    for s in sorted(rankings, key=compare_sort_key):
        sid = _safe_str(s.get("id"))
        if focus_id and sid != focus_id:
            continue
        filename = _safe_str(s.get("filename"))
        total_score = round(_safe_float(s.get("total_score")), 2)
        report = s.get("report") or {}
        report_meta = report.get("meta") if isinstance(report.get("meta"), dict) else {}
        material_gate = (
            report_meta.get("material_utilization_gate")
            if isinstance(report_meta.get("material_utilization_gate"), dict)
            else {}
        )
        material_gate_blocked = bool(material_gate.get("blocked"))
        material_gate_warned = bool(material_gate.get("warned")) and not material_gate_blocked
        material_gate_reasons = [
            _safe_str(item) for item in (material_gate.get("reasons") or []) if _safe_str(item)
        ][:3]
        material_gate_summary = ""
        if material_gate_blocked:
            if material_gate_reasons:
                material_gate_summary = "资料利用门禁阻断：" + "；".join(material_gate_reasons[:2])
            else:
                material_gate_summary = "资料利用门禁阻断：该施组对部分项目资料未形成足够证据关联。"
        elif material_gate_warned and material_gate_reasons:
            material_gate_summary = "资料利用预警：" + "；".join(material_gate_reasons[:2])
        own_dims = report.get("dimension_scores", {}) or {}
        text = _safe_str(s.get("text"))
        markers = _build_page_markers(text)

        dim_candidates = []
        for dim_id in all_dim_ids:
            own_score = _safe_float((own_dims.get(dim_id) or {}).get("score"))
            best_score = _safe_float((top_dims.get(dim_id) or {}).get("score"))
            own_max = _safe_float((own_dims.get(dim_id) or {}).get("max_score"))
            top_max = _safe_float((top_dims.get(dim_id) or {}).get("max_score"))
            dim_max = (
                own_max
                if own_max > 0
                else (top_max if top_max > 0 else DEFAULT_DIMENSION_MAX_SCORE)
            )
            delta_to_full = round(max(0.0, dim_max - own_score), 2)
            delta_to_top = round(max(0.0, best_score - own_score), 2)
            dim_candidates.append(
                (dim_id, own_score, best_score, dim_max, delta_to_full, delta_to_top)
            )
        dim_candidates.sort(key=lambda x: (x[4], x[5], -x[1]), reverse=True)

        recommendations: List[Dict[str, Any]] = []
        for dim_id, own_score, best_score, dim_max, delta_to_full, delta_to_top in dim_candidates[
            :8
        ]:
            if delta_to_full <= 0:
                continue
            dim_row = own_dims.get(dim_id) or {}
            page_hint = _guess_dimension_page_hint(dim_id, dim_row, text, markers)
            actions = _build_dimension_actions(dim_id, delta_to_full)
            dim_ev_rows = _extract_dim_evidence_rows(
                dim_row,
                text,
                markers,
                max_items=1,
                dim_id=dim_id,
            )
            dim_ev = (dim_ev_rows or [{}])[0]
            evidence_snippet = (
                _safe_str(dim_ev.get("snippet")) or "未检测到有效证据片段，请补充可验证内容。"
            )
            original_text = _normalize_original_text(
                _safe_str(dim_ev.get("original_text")) or evidence_snippet,
                synthetic=bool(dim_ev.get("synthetic")),
            )
            evidence_context = _safe_str(dim_ev.get("context_window")) or "\n".join(
                [
                    f"页码：{page_hint}",
                    "前文：（未定位）",
                    f"命中：{evidence_snippet}",
                    "后文：（未定位）",
                ]
            )
            write_mode = (
                "insert"
                if _recommendation_requires_insertion(dim_ev, evidence_snippet)
                else "replace"
            )
            replace_text = _build_dimension_replacement_text(
                dim_id,
                evidence=evidence_snippet,
                original_text=original_text,
            )
            insertion_bundle = _build_dimension_insertion_guidance(
                dim_id,
                page_hint=page_hint,
                chapter_hint=_chapter_hint(dim_id, "维度补强"),
                evidence=evidence_snippet,
                original_text=original_text,
            )
            recommendations.append(
                {
                    "document_id": sid,
                    "document_filename": filename,
                    "category": "维度补强",
                    "dimension": dim_id,
                    "dimension_name": _dim_name(dim_id),
                    "chapter_hint": _chapter_hint(dim_id, "维度补强"),
                    "page_hint": page_hint,
                    "issue": (
                        f"该维度得分 {own_score:.2f}/{dim_max:.2f}，距满分目标差 {delta_to_full:.2f} 分。"
                        + (
                            f"（较当前项目最高稿低 {delta_to_top:.2f} 分）"
                            if (delta_to_top > 0 and not isolated)
                            else ""
                        )
                    ),
                    "evidence": evidence_snippet,
                    "evidence_context": evidence_context,
                    "write_mode": write_mode,
                    "write_mode_label": "原位补充" if write_mode == "insert" else "原句替换",
                    "original_text": original_text,
                    "replacement_text": (
                        insertion_bundle["insertion_content"]
                        if write_mode == "insert"
                        else replace_text
                    ),
                    "insertion_guidance": (
                        insertion_bundle["insertion_guidance"] if write_mode == "insert" else ""
                    ),
                    "insertion_content": (
                        insertion_bundle["insertion_content"] if write_mode == "insert" else ""
                    ),
                    "before_after_example": _build_dimension_before_after_example(
                        dim_id,
                        evidence=evidence_snippet,
                        page_hint=page_hint,
                    ),
                    "rewrite_instruction": _build_dimension_rewrite_plan(
                        dim_id,
                        delta_to_full=delta_to_full,
                        page_hint=page_hint,
                        evidence=evidence_snippet,
                    ),
                    "acceptance_check": _build_dimension_acceptance_plan(dim_id),
                    "execution_checklist": _build_dimension_execution_checklist(dim_id, page_hint),
                    "target_delta_reduction": delta_to_full,
                    "target_full_score": dim_max,
                    "reference_top_score": None if isolated else best_score,
                    "priority_reason": (
                        f"该维度距满分仍有 {delta_to_full:.2f} 分缺口"
                        + (
                            f"，且较项目最高稿落后 {delta_to_top:.2f} 分"
                            if (delta_to_top > 0 and not isolated)
                            else ""
                        )
                        + "，优先补强可直接提升可审查性得分。"
                    ),
                    "actions": actions,
                }
            )

        penalties = sorted(
            report.get("penalties", []) or [],
            key=lambda x: _safe_float(x.get("points")),
            reverse=True,
        )
        for p in penalties[:5]:
            ev_rows = _extract_penalty_evidence_rows(p, text, markers, max_items=1)
            ev = ev_rows[0] if ev_rows else {}
            code = _safe_str(p.get("code") or "UNKNOWN")
            actions = _build_penalty_actions(code)
            points = round(_safe_float(p.get("points")), 2)
            reason = _safe_str(p.get("reason")) or "未提供"
            page_hint = _safe_str(ev.get("page_hint")) or "页码未知"
            evidence_snippet = _safe_str(ev.get("snippet")) or "未提取到证据片段。"
            original_text = _normalize_original_text(
                _safe_str(ev.get("original_text")) or evidence_snippet,
                synthetic=bool(ev.get("synthetic")),
            )
            evidence_context = _safe_str(ev.get("context_window")) or "\n".join(
                [
                    f"页码：{page_hint}",
                    "前文：（未定位）",
                    f"命中：{evidence_snippet}",
                    "后文：（未定位）",
                ]
            )
            recommendations.append(
                {
                    "document_id": sid,
                    "document_filename": filename,
                    "category": "扣分消减",
                    "dimension": "",
                    "dimension_name": "",
                    "chapter_hint": _chapter_hint("", "扣分消减"),
                    "page_hint": page_hint,
                    "issue": f"{code} 扣分 {points} 分，原因：{reason}",
                    "evidence": evidence_snippet,
                    "evidence_context": evidence_context,
                    "write_mode": "replace",
                    "write_mode_label": "原句替换",
                    "original_text": original_text,
                    "replacement_text": _build_penalty_replacement_text(
                        code,
                        evidence=original_text or evidence_snippet,
                        reason=reason,
                    ),
                    "insertion_guidance": "",
                    "insertion_content": "",
                    "before_after_example": _build_penalty_before_after_example(
                        code,
                        evidence=evidence_snippet,
                        page_hint=page_hint,
                        reason=reason,
                    ),
                    "rewrite_instruction": _build_penalty_rewrite_plan(
                        code,
                        points=points,
                        page_hint=page_hint,
                        reason=reason,
                        evidence=evidence_snippet,
                    ),
                    "acceptance_check": _build_penalty_acceptance_plan(code),
                    "execution_checklist": _build_penalty_execution_checklist(code, page_hint),
                    "target_delta_reduction": points,
                    "reference_top_score": None,
                    "priority_reason": (
                        f"{code} 属于高频扣分触发项，当前已产生 {points:.2f} 分损失，"
                        "优先整改可快速抬升总分稳定性。"
                    ),
                    "actions": actions,
                }
            )

        if not recommendations:
            recommendations.append(
                {
                    "document_id": sid,
                    "document_filename": filename,
                    "category": "保优",
                    "dimension": "",
                    "dimension_name": "",
                    "chapter_hint": _chapter_hint("", "保优"),
                    "page_hint": "全篇复核",
                    "issue": "当前稿件已处于项目内高分段，重点防回退。",
                    "evidence": "建议做术语一致性与阈值口径复核。",
                    "evidence_context": "页码：全篇\n前文：（无）\n命中：建议做术语一致性与阈值口径复核。\n后文：（无）",
                    "write_mode": "replace",
                    "write_mode_label": "原句替换",
                    "original_text": "建议做术语一致性与阈值口径复核。",
                    "replacement_text": "统一术语、关键参数和验收动作的口径，并在目录与对应章节同步标注复核结果。",
                    "insertion_guidance": "",
                    "insertion_content": "",
                    "before_after_example": "\n".join(
                        [
                            "定位：全篇",
                            "改写前（摘录）：描述存在口径不一致风险。",
                            "改写后（示例）：统一术语、参数与验收动作口径，并在关键章节做一次一致性复核。",
                        ]
                    ),
                    "rewrite_instruction": "\n".join(
                        [
                            "改写目标：保持高分并防止回退。",
                            "执行步骤：",
                            "1. 术语一致性复核（同一概念全篇同名）；",
                            "2. 参数一致性复核（工期/阈值/频次一致）；",
                            "3. 责任链一致性复核（责任岗位与验收动作闭环）。",
                        ]
                    ),
                    "acceptance_check": "\n".join(
                        [
                            "核验清单：",
                            "1. 抽检关键章节无新增空泛承诺；",
                            "2. 同一锚点在不同章节口径一致；",
                            "3. 所有关键措施均可追溯到记录表或验收单。",
                        ]
                    ),
                    "execution_checklist": "\n".join(
                        [
                            "执行清单（保优）",
                            "1. 抽检关键章节口径一致性；",
                            "2. 抽检参数/频次/责任人是否可追溯；",
                            "3. 抽检验收记录引用是否完整；",
                            "4. 形成一次“保优复核记录”。",
                        ]
                    ),
                    "target_delta_reduction": 0.0,
                    "target_full_score": target_total_score,
                    "reference_top_score": None if isolated else top_total,
                    "priority_reason": "当前稿件已在高分段，重点防止表述回退导致二次扣分。",
                }
            )

        recommendations.sort(
            key=lambda x: (
                _safe_float(x.get("target_delta_reduction")),
                1 if _safe_str(x.get("category")) == "扣分消减" else 0,
            ),
            reverse=True,
        )
        for idx, r in enumerate(recommendations, start=1):
            if idx <= 4:
                level = "P1"
            elif idx <= 8:
                level = "P2"
            else:
                level = "P3"
            r["priority"] = level

        cards.append(
            {
                "submission_id": sid,
                "filename": filename,
                "total_score": total_score,
                "target_score": target_total_score,
                "target_gap": round(max(0.0, target_total_score - total_score), 2),
                "reference_top_score": None if isolated else top_total,
                "reference_top_filename": "" if isolated else _safe_str(top.get("filename")),
                "material_gate_blocked": material_gate_blocked,
                "material_gate_warned": material_gate_warned,
                "material_gate_reasons": material_gate_reasons,
                "material_gate_summary": material_gate_summary,
                "recommendations": recommendations[:12],
            }
        )
    return cards


def _build_submission_scorecards(
    rankings: List[Dict[str, Any]],
    top: Dict[str, Any],
    all_dim_ids: List[str],
    *,
    total_score_scale_max: float = DEFAULT_TOTAL_SCORE_SCALE_MAX,
    focus_submission_id: str = "",
) -> List[Dict[str, Any]]:
    cards: List[Dict[str, Any]] = []
    top_dims = (top.get("report") or {}).get("dimension_scores", {}) or {}
    target_total_score = _normalize_total_score_scale(total_score_scale_max)
    focus_id = _safe_str(focus_submission_id)

    for rank_desc, s in enumerate(rankings, start=1):
        sid = _safe_str(s.get("id"))
        if focus_id and sid != focus_id:
            continue
        filename = _safe_str(s.get("filename"))
        total_score = round(_safe_float(s.get("total_score")), 2)
        report = s.get("report") or {}
        awareness = build_compare_sort_fields(s)
        own_dims = report.get("dimension_scores", {}) or {}
        text = _safe_str(s.get("text"))
        markers = _build_page_markers(text)

        dimension_score_items: List[Dict[str, Any]] = []
        for dim_id in all_dim_ids:
            own_row = own_dims.get(dim_id) or {}
            own_score = round(_safe_float(own_row.get("score")), 2)
            own_max = _safe_float(own_row.get("max_score"))
            top_max = _safe_float((top_dims.get(dim_id) or {}).get("max_score"))
            dim_max = (
                own_max
                if own_max > 0
                else (top_max if top_max > 0 else DEFAULT_DIMENSION_MAX_SCORE)
            )
            gap_to_full = round(max(0.0, dim_max - own_score), 2)
            ev_rows = _extract_dim_evidence_rows(own_row, text, markers, max_items=1, dim_id=dim_id)
            ev = ev_rows[0] if ev_rows else {}
            page_hint = _safe_str(ev.get("page_hint")) or _guess_dimension_page_hint(
                dim_id, own_row, text, markers
            )
            evidence = _safe_str(ev.get("snippet")) or "未提取到证据片段。"
            evidence_context = _safe_str(ev.get("context_window")) or "\n".join(
                [
                    f"页码：{page_hint}",
                    "前文：（未定位）",
                    f"命中：{evidence}",
                    "后文：（未定位）",
                ]
            )
            dimension_score_items.append(
                {
                    "dimension": dim_id,
                    "dimension_name": _dim_name(dim_id),
                    "module": _dim_module(dim_id),
                    "score": own_score,
                    "max_score": round(dim_max, 2),
                    "gap_to_full": gap_to_full,
                    "page_hint": page_hint,
                    "evidence": evidence,
                    "evidence_context": evidence_context,
                }
            )

        loss_items = sorted(
            dimension_score_items,
            key=lambda x: (_safe_float(x.get("gap_to_full")), -_safe_float(x.get("score"))),
            reverse=True,
        )[:5]
        gain_items = sorted(
            dimension_score_items,
            key=lambda x: (_safe_float(x.get("score")), -_safe_float(x.get("gap_to_full"))),
            reverse=True,
        )[:5]

        deduction_items: List[Dict[str, Any]] = []
        total_deduction_points = 0.0
        penalties = sorted(
            report.get("penalties", []) or [],
            key=lambda x: _safe_float(x.get("points")),
            reverse=True,
        )
        for p in penalties:
            points = round(_safe_float(p.get("points")), 2)
            total_deduction_points += points
            ev_rows = _extract_penalty_evidence_rows(p, text, markers, max_items=1)
            ev = ev_rows[0] if ev_rows else {}
            page_hint = _safe_str(ev.get("page_hint")) or "页码未知"
            evidence = _safe_str(ev.get("snippet")) or "未提取到证据片段。"
            evidence_context = _safe_str(ev.get("context_window")) or "\n".join(
                [
                    f"页码：{page_hint}",
                    "前文：（未定位）",
                    f"命中：{evidence}",
                    "后文：（未定位）",
                ]
            )
            deduction_items.append(
                {
                    "code": _safe_str(p.get("code") or "UNKNOWN"),
                    "points": points,
                    "reason": _safe_str(p.get("reason")) or "未提供",
                    "page_hint": page_hint,
                    "evidence": evidence,
                    "evidence_context": evidence_context,
                }
            )

        cards.append(
            {
                "submission_id": sid,
                "filename": filename,
                "rank_desc": rank_desc,
                "total_score": total_score,
                "score_confidence_level": awareness.get("score_confidence_level") or "",
                "score_confidence_score": awareness.get("score_confidence_score"),
                "score_confidence_reason": awareness.get("score_confidence_reason") or "",
                "ranking_evidence_bonus": awareness.get("ranking_evidence_bonus"),
                "ranking_sort_score": awareness.get("ranking_sort_score"),
                "target_full_score": target_total_score,
                "gap_to_full_total": round(max(0.0, target_total_score - total_score), 2),
                "total_deduction_points": round(total_deduction_points, 2),
                "dimension_score_items": dimension_score_items,
                "loss_items": loss_items,
                "gain_items": gain_items,
                "deduction_items": deduction_items[:10],
            }
        )
    return cards


def build_compare_narrative(
    submissions: List[Dict[str, Any]],
    *,
    score_scale_max: float = DEFAULT_TOTAL_SCORE_SCALE_MAX,
    focus_submission_id: str = "",
) -> Dict[str, Any]:
    normalized_scale_max = _normalize_total_score_scale(score_scale_max)
    scale_label = _total_score_scale_label(normalized_scale_max)
    focus_id = _safe_str(focus_submission_id)
    if not submissions or (len(submissions) < 2 and not focus_id):
        return {
            "summary": "施组数量不足，无法进行对比分析。",
            "score_scale_max": int(normalized_scale_max),
            "score_scale_label": scale_label,
            "report_scope": "project",
            "focus_submission": {},
            "top_submission": {},
            "bottom_submission": {},
            "key_diffs": [],
            "score_overview": {},
            "dimension_diagnostics": [],
            "penalty_diagnostics": [],
            "submission_diagnostics": [],
            "priority_actions": [],
            "submission_optimization_cards": [],
            "submission_scorecards": [],
        }

    rankings = sorted(submissions, key=compare_sort_key, reverse=True)
    top = rankings[0]
    bottom = rankings[-1]
    scores = [_safe_float(s.get("total_score")) for s in rankings]

    top_report = top.get("report", {}) or {}
    bottom_report = bottom.get("report", {}) or {}
    top_dims = top_report.get("dimension_scores", {}) or {}
    bottom_dims = bottom_report.get("dimension_scores", {}) or {}
    top_text = _safe_str(top.get("text"))
    bottom_text = _safe_str(bottom.get("text"))
    top_markers = _build_page_markers(top_text)
    bottom_markers = _build_page_markers(bottom_text)

    all_dim_ids = sorted(
        {
            dim_id
            for s in rankings
            for dim_id in (((s.get("report") or {}).get("dimension_scores") or {}).keys())
        }
        | set(top_dims.keys())
        | set(bottom_dims.keys())
    )

    dim_scores_by_file: Dict[str, Dict[str, float]] = {}
    for s in rankings:
        sid = _safe_str(s.get("id"))
        dim_rows = (s.get("report") or {}).get("dimension_scores", {}) or {}
        dim_scores_by_file[sid] = {
            dim_id: _safe_float((dim_rows.get(dim_id) or {}).get("score")) for dim_id in all_dim_ids
        }

    diffs: List[Dict[str, Any]] = []
    for dim_id in all_dim_ids:
        t = _safe_float((top_dims.get(dim_id) or {}).get("score"))
        b = _safe_float((bottom_dims.get(dim_id) or {}).get("score"))
        delta = round(t - b, 2)
        avg = round(
            mean([dim_scores_by_file[_safe_str(s.get("id"))].get(dim_id, 0.0) for s in rankings]),
            2,
        )
        weak_files = []
        weak_files_with_scores = []
        for s in rankings:
            sid = _safe_str(s.get("id"))
            filename = _safe_str(s.get("filename") or sid)
            score_val = _safe_float(dim_scores_by_file[sid].get(dim_id, 0.0))
            if score_val < avg:
                weak_files.append(filename)
                weak_files_with_scores.append(f"{filename}({score_val:.2f})")
        weak_count = len(weak_files)
        dim_ranked = sorted(
            [
                {
                    "submission_id": _safe_str(s.get("id")),
                    "filename": _safe_str(s.get("filename") or s.get("id")),
                    "score": _safe_float(
                        dim_scores_by_file[_safe_str(s.get("id"))].get(dim_id, 0.0)
                    ),
                }
                for s in rankings
            ],
            key=lambda x: x.get("score", 0.0),
            reverse=True,
        )
        dim_top_file = dim_ranked[0] if dim_ranked else {"filename": "", "score": 0.0}
        dim_bottom_file = dim_ranked[-1] if dim_ranked else {"filename": "", "score": 0.0}
        top_ev_rows = _extract_dim_evidence_rows(
            top_dims.get(dim_id) or {},
            top_text,
            top_markers,
            dim_id=dim_id,
        )
        bottom_ev_rows = _extract_dim_evidence_rows(
            bottom_dims.get(dim_id) or {},
            bottom_text,
            bottom_markers,
            dim_id=dim_id,
        )
        diffs.append(
            {
                "dimension": dim_id,
                "dimension_name": _dim_name(dim_id),
                "module": _dim_module(dim_id),
                "top_score": round(t, 2),
                "bottom_score": round(b, 2),
                "project_avg": avg,
                "delta": delta,
                "weak_file_count": weak_count,
                "weak_filenames": weak_files[:8],
                "weak_files_with_scores": weak_files_with_scores[:8],
                "top_filename": _safe_str(dim_top_file.get("filename")),
                "top_dimension_score": round(_safe_float(dim_top_file.get("score")), 2),
                "bottom_filename": _safe_str(dim_bottom_file.get("filename")),
                "bottom_dimension_score": round(_safe_float(dim_bottom_file.get("score")), 2),
                "top_evidence": [x.get("snippet") for x in top_ev_rows if x.get("snippet")],
                "bottom_evidence": [x.get("snippet") for x in bottom_ev_rows if x.get("snippet")],
                "top_evidence_rows": top_ev_rows,
                "bottom_evidence_rows": bottom_ev_rows,
                "top_page_hint": (
                    (top_ev_rows[0].get("page_hint") if top_ev_rows else "")
                    or _guess_dimension_page_hint(
                        dim_id, top_dims.get(dim_id) or {}, top_text, top_markers
                    )
                ),
                "bottom_page_hint": (
                    (bottom_ev_rows[0].get("page_hint") if bottom_ev_rows else "")
                    or _guess_dimension_page_hint(
                        dim_id, bottom_dims.get(dim_id) or {}, bottom_text, bottom_markers
                    )
                ),
                "rewrite_template": _REWRITE_TEMPLATES.get(
                    dim_id,
                    "该段应明确责任岗位、执行频次、控制参数和验收动作，形成可复核的闭环表达。",
                ),
                "actions": _build_dimension_actions(dim_id, delta),
            }
        )

    diffs.sort(key=lambda x: x["delta"], reverse=True)
    key_diffs = diffs[:5]

    penalty_stats: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {
            "count": 0,
            "total_points": 0.0,
            "affected_files": set(),
            "reason_counter": Counter(),
            "evidence_samples": [],
            "evidence_context_samples": [],
            "page_hints": [],
        }
    )

    for s in rankings:
        sid = _safe_str(s.get("id"))
        filename = _safe_str(s.get("filename"))
        text = _safe_str(s.get("text"))
        markers = _build_page_markers(text)
        penalties = (s.get("report") or {}).get("penalties") or []
        for p in penalties:
            code = _safe_str(p.get("code") or "UNKNOWN")
            row = penalty_stats[code]
            row["count"] += 1
            row["total_points"] += _safe_float(p.get("points"))
            row["affected_files"].add(filename or sid)
            reason = _safe_str(p.get("reason"))
            if reason:
                row["reason_counter"][reason] += 1
            for ev_row in _extract_penalty_evidence_rows(p, text, markers):
                snippet = _safe_str(ev_row.get("snippet"))
                context_window = _safe_str(ev_row.get("context_window"))
                page_hint = _safe_str(ev_row.get("page_hint"))
                if snippet and snippet not in row["evidence_samples"]:
                    row["evidence_samples"].append(snippet)
                if context_window and context_window not in row["evidence_context_samples"]:
                    row["evidence_context_samples"].append(context_window)
                if page_hint and page_hint not in row["page_hints"]:
                    row["page_hints"].append(page_hint)

    penalty_diagnostics: List[Dict[str, Any]] = []
    for code, row in penalty_stats.items():
        reason_samples = [x[0] for x in row["reason_counter"].most_common(3)]
        penalty_diagnostics.append(
            {
                "code": code,
                "count": int(row["count"]),
                "affected_submission_count": len(row["affected_files"]),
                "affected_files": sorted(list(row["affected_files"]))[:8],
                "total_points": round(_safe_float(row["total_points"]), 2),
                "reason_samples": reason_samples,
                "evidence_samples": (row["evidence_samples"] or [])[:4],
                "evidence_context_samples": (row["evidence_context_samples"] or [])[:4],
                "page_hints": (row["page_hints"] or [])[:4],
                "actions": _build_penalty_actions(code),
            }
        )
    penalty_diagnostics.sort(
        key=lambda x: (x.get("count", 0), x.get("total_points", 0.0)), reverse=True
    )

    submission_diagnostics: List[Dict[str, Any]] = []
    for rank, s in enumerate(sorted(rankings, key=compare_sort_key), start=1):
        awareness = build_compare_sort_fields(s)
        dim_rows = (s.get("report") or {}).get("dimension_scores", {}) or {}
        dim_rank = sorted(
            [
                {
                    "dimension": dim_id,
                    "dimension_name": _dim_name(dim_id),
                    "score": round(_safe_float((dim_rows.get(dim_id) or {}).get("score")), 2),
                }
                for dim_id in dim_rows.keys()
            ],
            key=lambda x: x["score"],
        )
        penalties = (s.get("report") or {}).get("penalties") or []
        p_counter = Counter([_safe_str(p.get("code") or "UNKNOWN") for p in penalties])
        major_penalties = [{"code": code, "count": cnt} for code, cnt in p_counter.most_common(3)]
        weakest = dim_rank[:3]
        strongest = list(reversed(dim_rank[-3:])) if dim_rank else []
        weak_names = (
            "、".join([w["dimension_name"] for w in weakest if w.get("dimension_name")]) or "未识别"
        )
        penalty_names = "、".join([p["code"] for p in major_penalties]) or "无明显扣分项"
        submission_diagnostics.append(
            {
                "submission_id": _safe_str(s.get("id")),
                "filename": _safe_str(s.get("filename")),
                "rank_ascending": rank,
                "total_score": round(_safe_float(s.get("total_score")), 2),
                "score_confidence_level": awareness.get("score_confidence_level") or "",
                "score_confidence_score": awareness.get("score_confidence_score"),
                "score_confidence_reason": awareness.get("score_confidence_reason") or "",
                "weakest_dimensions": weakest,
                "strongest_dimensions": strongest,
                "major_penalties": major_penalties,
                "actionable_summary": f"优先优化维度：{weak_names}；重点消减扣分：{penalty_names}。",
            }
        )

    priority_actions: List[Dict[str, Any]] = []
    for i, d in enumerate(key_diffs[:3], start=1):
        priority_actions.append(
            {
                "priority": f"P{i}",
                "theme": f"维度 {d.get('dimension')} {d.get('dimension_name')}",
                "reason": f"最高分与最低分在该维度差距 {d.get('delta')} 分（模块：{d.get('module') or '未分类'}）。",
                "evidence": (
                    d.get("bottom_evidence")
                    or d.get("top_evidence")
                    or ["该维度证据片段不足，建议补录过程证据。"]
                )[0],
                "page_hint": d.get("bottom_page_hint") or d.get("top_page_hint") or "页码未知",
                "action": "；".join((d.get("actions") or [])[:2]),
                "expected_impact": "缩小维度分差，提升可执行性与审查通过率。",
            }
        )
    for p in penalty_diagnostics[:2]:
        priority_actions.append(
            {
                "priority": "P2",
                "theme": f"扣分项 {p.get('code')}",
                "reason": f"出现 {p.get('count')} 次，影响 {p.get('affected_submission_count')} 份文件，累计扣分 {p.get('total_points')}。",
                "evidence": (p.get("reason_samples") or p.get("evidence_samples") or ["无"])[0],
                "page_hint": (p.get("page_hints") or ["页码未知"])[0],
                "action": "；".join((p.get("actions") or [])[:2]),
                "expected_impact": "减少重复扣分项，提升整体稳定得分。",
            }
        )

    isolated_report = bool(focus_id)
    submission_optimization_cards = _build_submission_optimization_cards(
        rankings,
        top,
        all_dim_ids,
        total_score_scale_max=normalized_scale_max,
        focus_submission_id=focus_id,
        isolated=isolated_report,
    )
    submission_scorecards = _build_submission_scorecards(
        rankings,
        top,
        all_dim_ids,
        total_score_scale_max=normalized_scale_max,
        focus_submission_id=focus_id,
    )
    gap = round(_safe_float(top.get("total_score")) - _safe_float(bottom.get("total_score")), 2)
    low_confidence_files = [
        _safe_str(s.get("filename"))
        for s in rankings
        if _safe_str(_score_awareness_meta(s).get("score_confidence_level")) == "low"
    ]
    summary = (
        f"最高分为{top.get('filename')}（{_format_total_score_text(top.get('total_score'), normalized_scale_max)}），"
        f"最低分为{bottom.get('filename')}（{_format_total_score_text(bottom.get('total_score'), normalized_scale_max)}），"
        f"分差 {_format_total_score_text(gap, normalized_scale_max)}。"
        f"当前建议优先处理维度：{'、'.join([str(x.get('dimension')) for x in key_diffs[:3]]) or '无'}，"
        f"并优先消减扣分项：{'、'.join([str(x.get('code')) for x in penalty_diagnostics[:2]]) or '无'}。"
        + (
            f"当前有 {len(low_confidence_files)} 份施组处于低置信度评分，排序解读需结合资料完备度。"
            if low_confidence_files
            else "当前评分置信度未见明显低位异常。"
        )
        + "对近似分数施组，排序已叠加极小的证据质量加成以提高稳定性。"
        + "报告已给出逐文件、逐页定位的优化动作，可直接用于编制迭代。"
    )

    base_payload = {
        "summary": summary,
        "score_scale_max": int(normalized_scale_max),
        "score_scale_label": scale_label,
        "report_scope": "project",
        "focus_submission": {},
        "top_submission": {
            "id": top.get("id"),
            "filename": top.get("filename"),
            "total_score": top.get("total_score"),
            **build_compare_sort_fields(top),
        },
        "bottom_submission": {
            "id": bottom.get("id"),
            "filename": bottom.get("filename"),
            "total_score": bottom.get("total_score"),
            **build_compare_sort_fields(bottom),
        },
        "key_diffs": key_diffs,
        "score_overview": {
            "submission_count": len(rankings),
            "score_scale_max": int(normalized_scale_max),
            "score_scale_label": scale_label,
            "top_score": round(_safe_float(top.get("total_score")), 2),
            "bottom_score": round(_safe_float(bottom.get("total_score")), 2),
            "score_gap": gap,
            "project_avg_score": round(mean(scores), 2),
            "project_std_score": round(pstdev(scores), 2) if len(scores) > 1 else 0.0,
            "low_confidence_submission_count": len(low_confidence_files),
            "low_confidence_filenames": low_confidence_files[:8],
            "ranking_mode": "total_score+evidence_bonus",
            "max_ranking_evidence_bonus": round(
                max(
                    (
                        _safe_float(build_compare_sort_fields(s).get("ranking_evidence_bonus"))
                        for s in rankings
                    ),
                    default=0.0,
                ),
                4,
            ),
        },
        "dimension_diagnostics": diffs[:8],
        "penalty_diagnostics": penalty_diagnostics[:8],
        "submission_diagnostics": submission_diagnostics[:6],
        "priority_actions": priority_actions[:6],
        "submission_optimization_cards": submission_optimization_cards,
        "submission_scorecards": submission_scorecards,
    }
    if not focus_id:
        return base_payload

    focus_submission = next(
        (item for item in rankings if _safe_str(item.get("id")) == focus_id),
        {},
    )
    focus_score = round(_safe_float(focus_submission.get("total_score")), 2)
    focus_filename = _safe_str(focus_submission.get("filename") or focus_id)
    focus_card = submission_optimization_cards[0] if submission_optimization_cards else {}
    isolated_summary = (
        f"当前仅分析《{focus_filename}》的逐页优化清单，"
        f"当前分 {_format_total_score_text(focus_score, normalized_scale_max)}，"
        f"距满分目标 {_format_total_score_text(focus_card.get('target_gap') or 0.0, normalized_scale_max)}。"
        f"已生成 {len(focus_card.get('recommendations') or [])} 条即插即用的替换/补充建议，"
        "并严格限定为当前施组上下文。"
    )
    return {
        "summary": isolated_summary,
        "score_scale_max": int(normalized_scale_max),
        "score_scale_label": scale_label,
        "report_scope": "submission",
        "focus_submission": {
            "id": focus_submission.get("id"),
            "filename": focus_submission.get("filename"),
            "total_score": focus_submission.get("total_score"),
            **build_compare_sort_fields(focus_submission),
        },
        "top_submission": {},
        "bottom_submission": {},
        "key_diffs": [],
        "score_overview": {
            "submission_count": 1,
            "score_scale_max": int(normalized_scale_max),
            "score_scale_label": scale_label,
            "focus_submission_id": focus_submission.get("id"),
            "focus_submission_filename": focus_submission.get("filename"),
        },
        "dimension_diagnostics": [],
        "penalty_diagnostics": [],
        "submission_diagnostics": [],
        "priority_actions": [],
        "submission_optimization_cards": submission_optimization_cards[:1],
        "submission_scorecards": submission_scorecards[:1],
    }


_DIM_NAMES = {
    "01": "工程项目整体理解",
    "02": "安全生产管理与措施",
    "03": "文明施工管理与措施",
    "04": "材料与部品管理",
    "05": "新工艺新技术",
    "06": "关键工序",
    "07": "重难点及危大工程",
    "08": "质量保障体系",
    "09": "进度保障措施",
    "10": "专项施工工艺",
    "11": "人力资源配置",
    "12": "总体施工工艺",
    "13": "物资与设备配置",
    "14": "设计协调与深化",
    "15": "总体配置计划",
    "16": "技术措施可行性",
}

_REWRITE_TEMPLATES = {
    "07": "危大工程应先完成清单识别、专项方案闭环和验算/论证，监测预警阈值控制在【阈值/参数】内，关键节点按【频次】实施旁站和【验收动作】，并明确应急处置与复工条件。",
    "09": "总控、月、周、日计划应保持联动，关键线路和节点销项同步更新；当偏差达到【阈值/参数】时，按【频次】启动纠偏、复盘和【验收动作】闭环。",
    "02": "安全管理应按风险分级和隐患排查清单执行，检查频次为【频次】，危大作业同步落实专项方案、班前教育和【验收动作】，应急预案及控制指标控制在【阈值/参数】内。",
    "03": "文明施工应围绕围挡管理、扬尘控制、噪声/光污染和场地清洁展开，按【频次】巡检并记录，关键控制指标保持在【阈值/参数】内，问题闭环通过【验收动作】落实。",
}


def build_rewrite_suggestions(submissions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """基于对比结果生成改写建议报告（针对低分施组的可执行改写句）。"""
    if len(submissions) < 2:
        return {
            "target_filename": "",
            "summary": "施组数量不足，无法生成改写建议。",
            "suggestions": [],
        }

    rankings = sorted(submissions, key=lambda x: float(x.get("total_score", 0.0)), reverse=True)
    bottom = rankings[-1]
    narrative = build_compare_narrative(submissions)

    suggestions: List[Dict[str, Any]] = []
    for d in narrative.get("key_diffs", [])[:8]:
        dim_id = d.get("dimension", "")
        delta = d.get("delta", 0.0)
        if delta <= 0:
            continue
        name = _DIM_NAMES.get(dim_id, dim_id)
        template = _REWRITE_TEMPLATES.get(
            dim_id,
            "该段应明确责任岗位、执行频次、控制参数和验收动作，形成可复核的闭环表达。",
        )
        suggestions.append(
            {
                "dimension": dim_id,
                "dimension_name": name,
                "score_gap": delta,
                "rewrite_template": template,
            }
        )

    return {
        "target_filename": bottom.get("filename", ""),
        "summary": f"针对最低分施组「{bottom.get('filename')}」的改写建议：补齐以下维度可缩小与最高分的差距。",
        "suggestions": suggestions,
    }
