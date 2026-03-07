from __future__ import annotations

import re
from collections import Counter, defaultdict
from statistics import mean, pstdev
from typing import Any, Dict, List, Optional, Tuple

from app.engine.dimensions import DIMENSIONS

_PAGE_MARK_RE = re.compile(r"\[PAGE:(\d+)\]")
_CHAR_LOC_RE = re.compile(r"char:(\d+)(?:-(\d+))?")
FULL_TARGET_TOTAL_SCORE = 100.0
DEFAULT_DIMENSION_MAX_SCORE = 10.0
EVIDENCE_CONTEXT_RADIUS = 90

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


def _clean_snippet(text: str, limit: int = 90) -> str:
    if not text:
        return ""
    s = " ".join(str(text).split())
    s = _PAGE_MARK_RE.sub("", s).strip()
    return s[:limit] + ("..." if len(s) > limit else "")


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
        fallback = _clean_snippet(snippet, limit=180) or "未提取到证据片段。"
        return "\n".join(
            [
                f"页码：{page_hint}",
                "前文：（无）",
                f"命中：{fallback}",
                "后文：（无）",
            ]
        )

    start, end = _char_span_from_locator(locator)
    if start < 0:
        pos = _find_pos_by_snippet(t, snippet)
        if pos >= 0:
            start = pos
            end = pos + max(1, len(_safe_str(snippet)))
    if start < 0:
        start, end = 0, min(len(t), 1)

    start = max(0, min(len(t) - 1, start))
    end = max(start + 1, min(len(t), end))

    left = max(0, start - EVIDENCE_CONTEXT_RADIUS)
    right = min(len(t), end + EVIDENCE_CONTEXT_RADIUS)

    before = _clean_snippet(t[left:start], limit=140) or "（无）"
    hit = _clean_snippet(t[start:end], limit=140) or _clean_snippet(snippet, limit=140) or "（无）"
    after = _clean_snippet(t[end:right], limit=140) or "（无）"

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
) -> Dict[str, str]:
    clean = _clean_snippet(snippet, limit=220)
    if not clean and text:
        s, e = _char_span_from_locator(locator)
        if s >= 0:
            clean = _clean_snippet(text[s:e], limit=220)
    page_hint = _resolve_page_hint(locator, clean, text, markers)
    context_window = _build_context_window(
        text,
        locator=locator,
        snippet=clean,
        page_hint=page_hint,
    )
    return {
        "snippet": clean or _clean_snippet(snippet, limit=220),
        "locator": locator,
        "page_hint": page_hint,
        "context_window": context_window,
    }


def _extract_dim_evidence_rows(
    dim_row: Dict[str, Any],
    text: str,
    markers: List[Tuple[int, int]],
    max_items: int = 2,
    dim_id: str = "",
) -> List[Dict[str, str]]:
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
) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
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
        "由【责任岗位】牵头，按【频次】执行检查复核，控制指标【阈值/参数】，并完成【验收动作】闭环。",
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
    "P-EMPTY-002": "由项目经理负责，每周组织1次过程复核，形成《周检记录表》并在48小时内完成问题闭环，验收结论由监理签认。",
    "P-ACTION-002": "由专业工程师在每道工序开工前完成技术交底，施工中按日巡检，完工后执行报验与签认；若偏差>5%，24小时内整改并复验。",
    "P-CONSIST-001": "全篇统一工期口径为180天（开工日期以开工令为准），并在进度计划、节点清单、违约条款中一致引用同一参数。",
}


def _build_dimension_before_after_example(dim_id: str, evidence: str, page_hint: str) -> str:
    before = _safe_str(evidence) or "（请按定位页补提原句）"
    after = _materialize_template(
        _REWRITE_TEMPLATES.get(
            dim_id,
            "由【责任岗位】牵头，按【频次】执行检查复核，控制指标【阈值/参数】，并完成【验收动作】闭环。",
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
        "由项目经理牵头明确责任岗位、执行频次、阈值参数和验收动作，形成台账留痕并闭环复验。",
    )
    return "\n".join(
        [
            f"定位：{page_hint}",
            f"改写前（摘录）：{before}",
            f"改写后（示例）：{after}",
        ]
    )


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

_CHAPTER_HINTS = {
    "01": "总体部署与信息化管理",
    "02": "安全管理与劳保用品配置",
    "03": "文明施工与绿色工地",
    "04": "材料采购、进场验收与特殊材料闭环",
    "05": "四新技术应用",
    "06": "关键工序控制点",
    "07": "危大工程闭环管理",
    "08": "质量管理体系与ITP简表",
    "09": "进度计划体系与纠偏阈值",
    "10": "专项方案管理与审批验收节点",
    "11": "人力配置与培训",
    "12": "施工流程、专业穿插与移交条件",
    "13": "机械设备配置、验收与维保",
    "14": "图纸会审、深化设计与变更闭环",
    "15": "资源总控与动态调配",
    "16": "可行性验证、样板先行与落地清单",
}


def _chapter_hint(dim_id: str, category: str) -> str:
    if category == "扣分消减":
        return "扣分触发段落（同页整改并同步关联章节）"
    if category == "保优":
        return "全篇一致性复核"
    return _CHAPTER_HINTS.get(dim_id, "对应维度章节")


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
) -> List[Dict[str, Any]]:
    cards: List[Dict[str, Any]] = []
    top_dims = (top.get("report") or {}).get("dimension_scores", {}) or {}
    top_total = _safe_float(top.get("total_score"))

    for s in sorted(rankings, key=lambda x: _safe_float(x.get("total_score"))):
        sid = _safe_str(s.get("id"))
        filename = _safe_str(s.get("filename"))
        total_score = round(_safe_float(s.get("total_score")), 2)
        report = s.get("report") or {}
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
            evidence_context = _safe_str(dim_ev.get("context_window")) or "\n".join(
                [
                    f"页码：{page_hint}",
                    "前文：（未定位）",
                    f"命中：{evidence_snippet}",
                    "后文：（未定位）",
                ]
            )
            recommendations.append(
                {
                    "category": "维度补强",
                    "dimension": dim_id,
                    "dimension_name": _dim_name(dim_id),
                    "chapter_hint": _chapter_hint(dim_id, "维度补强"),
                    "page_hint": page_hint,
                    "issue": (
                        f"该维度得分 {own_score:.2f}/{dim_max:.2f}，距满分目标差 {delta_to_full:.2f} 分。"
                        + (
                            f"（较当前项目最高稿低 {delta_to_top:.2f} 分）"
                            if delta_to_top > 0
                            else ""
                        )
                    ),
                    "evidence": evidence_snippet,
                    "evidence_context": evidence_context,
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
                    "reference_top_score": best_score,
                    "priority_reason": (
                        f"该维度距满分仍有 {delta_to_full:.2f} 分缺口"
                        + (
                            f"，且较项目最高稿落后 {delta_to_top:.2f} 分"
                            if delta_to_top > 0
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
                    "category": "扣分消减",
                    "dimension": "",
                    "dimension_name": "",
                    "chapter_hint": _chapter_hint("", "扣分消减"),
                    "page_hint": page_hint,
                    "issue": f"{code} 扣分 {points} 分，原因：{reason}",
                    "evidence": evidence_snippet,
                    "evidence_context": evidence_context,
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
                    "category": "保优",
                    "dimension": "",
                    "dimension_name": "",
                    "chapter_hint": _chapter_hint("", "保优"),
                    "page_hint": "全篇复核",
                    "issue": "当前稿件已处于项目内高分段，重点防回退。",
                    "evidence": "建议做术语一致性与阈值口径复核。",
                    "evidence_context": "页码：全篇\n前文：（无）\n命中：建议做术语一致性与阈值口径复核。\n后文：（无）",
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
                    "target_full_score": FULL_TARGET_TOTAL_SCORE,
                    "reference_top_score": top_total,
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
                "target_score": FULL_TARGET_TOTAL_SCORE,
                "target_gap": round(max(0.0, FULL_TARGET_TOTAL_SCORE - total_score), 2),
                "reference_top_score": top_total,
                "recommendations": recommendations[:12],
            }
        )
    return cards


def _build_submission_scorecards(
    rankings: List[Dict[str, Any]],
    top: Dict[str, Any],
    all_dim_ids: List[str],
) -> List[Dict[str, Any]]:
    cards: List[Dict[str, Any]] = []
    top_dims = (top.get("report") or {}).get("dimension_scores", {}) or {}

    for rank_desc, s in enumerate(rankings, start=1):
        sid = _safe_str(s.get("id"))
        filename = _safe_str(s.get("filename"))
        total_score = round(_safe_float(s.get("total_score")), 2)
        report = s.get("report") or {}
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
                "target_full_score": FULL_TARGET_TOTAL_SCORE,
                "gap_to_full_total": round(max(0.0, FULL_TARGET_TOTAL_SCORE - total_score), 2),
                "total_deduction_points": round(total_deduction_points, 2),
                "dimension_score_items": dimension_score_items,
                "loss_items": loss_items,
                "gain_items": gain_items,
                "deduction_items": deduction_items[:10],
            }
        )
    return cards


def build_compare_narrative(submissions: List[Dict[str, Any]]) -> Dict[str, Any]:
    if len(submissions) < 2:
        return {
            "summary": "施组数量不足，无法进行对比分析。",
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

    rankings = sorted(
        submissions, key=lambda x: _safe_float(x.get("total_score", 0.0)), reverse=True
    )
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
                    "由【责任岗位】牵头，按【频次】执行检查复核，控制指标【阈值/参数】，并完成【验收动作】闭环。",
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
    for rank, s in enumerate(
        sorted(rankings, key=lambda x: _safe_float(x.get("total_score"))), start=1
    ):
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

    submission_optimization_cards = _build_submission_optimization_cards(rankings, top, all_dim_ids)
    submission_scorecards = _build_submission_scorecards(rankings, top, all_dim_ids)
    gap = round(_safe_float(top.get("total_score")) - _safe_float(bottom.get("total_score")), 2)
    summary = (
        f"最高分为{top.get('filename')}（{top.get('total_score')}分），"
        f"最低分为{bottom.get('filename')}（{bottom.get('total_score')}分），"
        f"分差 {gap} 分。"
        f"当前建议优先处理维度：{'、'.join([str(x.get('dimension')) for x in key_diffs[:3]]) or '无'}，"
        f"并优先消减扣分项：{'、'.join([str(x.get('code')) for x in penalty_diagnostics[:2]]) or '无'}。"
        "报告已给出逐文件、逐页定位的优化动作，可直接用于编制迭代。"
    )

    return {
        "summary": summary,
        "top_submission": {
            "id": top.get("id"),
            "filename": top.get("filename"),
            "total_score": top.get("total_score"),
        },
        "bottom_submission": {
            "id": bottom.get("id"),
            "filename": bottom.get("filename"),
            "total_score": bottom.get("total_score"),
        },
        "key_diffs": key_diffs,
        "score_overview": {
            "submission_count": len(rankings),
            "top_score": round(_safe_float(top.get("total_score")), 2),
            "bottom_score": round(_safe_float(bottom.get("total_score")), 2),
            "score_gap": gap,
            "project_avg_score": round(mean(scores), 2),
            "project_std_score": round(pstdev(scores), 2) if len(scores) > 1 else 0.0,
        },
        "dimension_diagnostics": diffs[:8],
        "penalty_diagnostics": penalty_diagnostics[:8],
        "submission_diagnostics": submission_diagnostics[:6],
        "priority_actions": priority_actions[:6],
        "submission_optimization_cards": submission_optimization_cards,
        "submission_scorecards": submission_scorecards,
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
    "07": "由【责任岗位】牵头（建议：技术负责人），按【频次】执行危大清单识别与专项方案闭环，完成验算/论证并设置监测预警阈值【阈值/参数】；关键节点实施旁站/验收【验收动作】，并设置应急处置与复工条件。",
    "09": "由【责任岗位】牵头（建议：施工员），建立总控/月/周/日计划联动，锁定关键线路与节点销项，资源调配按【频次】（建议：每日+每周）；纠偏触发阈值【阈值/参数】，执行日清周结与报验/签认闭环。",
    "02": "由【责任岗位】牵头（建议：安全员），实施风险分级与隐患排查，频次【频次】（建议：每日+每周），危大专项按方案执行；班前教育与旁站/验收【验收动作】闭环，控制指标【阈值/参数】并落实应急预案。",
    "03": "由【责任岗位】牵头（建议：施工员），落实围挡与出入口管理，扬尘治理采用PM10/喷淋/雾炮（如适用）并按【频次】（建议：每日+每周）巡检；噪声/光污染控制指标【阈值/参数】，场地清洁与垃圾分类按【检查动作】闭环，投诉响应限时处置。",
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
            "由【责任岗位】牵头，按【频次】执行检查复核，控制指标【阈值/参数】，并完成【验收动作】闭环。",
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
