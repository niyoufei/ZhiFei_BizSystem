from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from app.engine.evidence_units import build_evidence_units
from app.engine.preflight import PreFlightFatalError, pre_flight_check
from app.engine.template_rag import build_probe_template_suggestions, compute_probe_dimensions

DIMENSION_IDS = [f"{i:02d}" for i in range(1, 17)]
DIM_BASE_LEGACY_MAX = 80.0
DIM_BASE_TARGET_MAX = 90.0
CONSISTENCY_BONUS_MAX = 10.0
TOTAL_SCORE_MAX = 100.0


def compute_v2_rule_total(
    *,
    dim_total_80: float,
    consistency_bonus: float,
    penalty_points: float,
) -> Tuple[float, float]:
    """
    将维度主分与一致性/扣分聚合为总分。

    兼容历史字段 dim_total_80（0..80），并统一映射到当前口径：
    - 16维主体：0..90
    - 跨维一致性：0..10
    - 扣分项：按点数扣减
    - 总分：0..100
    """
    dim_total_80 = max(0.0, min(DIM_BASE_LEGACY_MAX, float(dim_total_80)))
    consistency_bonus = max(0.0, min(CONSISTENCY_BONUS_MAX, float(consistency_bonus)))
    penalty_points = max(0.0, float(penalty_points))

    dim_total_90 = dim_total_80 * (DIM_BASE_TARGET_MAX / DIM_BASE_LEGACY_MAX)
    dim_total_90 = max(0.0, min(DIM_BASE_TARGET_MAX, dim_total_90))
    rule_total = max(0.0, min(TOTAL_SCORE_MAX, dim_total_90 + consistency_bonus - penalty_points))
    return round(rule_total, 2), round(dim_total_90, 2)


def _snippet(text: str, start: int, end: int, window: int = 40) -> str:
    left = max(0, start - window)
    right = min(len(text), end + window)
    return text[left:right].replace("\n", " ").strip()


def _collect_units_by_dim(evidence_units: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    units_by_dim: Dict[str, List[Dict[str, Any]]] = {d: [] for d in DIMENSION_IDS}
    for unit in evidence_units:
        dim_id = str(unit.get("dimension_primary") or "01")
        if dim_id not in units_by_dim:
            units_by_dim[dim_id] = []
        units_by_dim[dim_id].append(unit)
    return units_by_dim


def _joined_unit_text(units: List[Dict[str, Any]]) -> str:
    return "\n".join(str(u.get("text") or "") for u in units if str(u.get("text") or "").strip())


def _contains_any_terms(text: str, terms: List[str]) -> bool:
    lower = text.lower()
    for term in terms:
        t = str(term or "").strip().lower()
        if t and t in lower:
            return True
    return False


def _count_hit_terms(text: str, terms: List[str]) -> int:
    lower = text.lower()
    seen: set[str] = set()
    for term in terms:
        t = str(term or "").strip().lower()
        if t and t in lower:
            seen.add(t)
    return len(seen)


def _heading_exact_hit(text: str, heading: str, units: List[Dict[str, Any]]) -> bool:
    h = str(heading or "").strip()
    if not h:
        return False
    pattern = re.compile(
        rf"(?m)^\s*(?:第?[一二三四五六七八九十百零0-9]+[章节部分、.)）\s]*)?{re.escape(h)}\s*$"
    )
    if pattern.search(text):
        return True
    for unit in units:
        heading_path = str(unit.get("heading_path") or "")
        if h in heading_path:
            return True
    return False


def _risk_measure_linked(
    units: List[Dict[str, Any]],
    *,
    risk_terms: List[str],
    measure_terms: List[str],
    neighbor_limit: int = 2,
) -> bool:
    if not units:
        return False
    risk_idx: List[int] = []
    measure_idx: List[int] = []
    for idx, unit in enumerate(units):
        text = str(unit.get("text") or "")
        lower = text.lower()
        if any(str(k).lower() in lower for k in risk_terms if str(k).strip()):
            risk_idx.append(idx)
        if any(str(k).lower() in lower for k in measure_terms if str(k).strip()):
            measure_idx.append(idx)
    if not risk_idx or not measure_idx:
        return False

    # 优先“同标题块”匹配
    for i in risk_idx:
        h1 = str(units[i].get("heading_path") or "")
        for j in measure_idx:
            h2 = str(units[j].get("heading_path") or "")
            if h1 and h1 == h2:
                return True

    # 退化为相邻证据单元距离
    for i in risk_idx:
        for j in measure_idx:
            if abs(i - j) <= max(0, int(neighbor_limit)):
                return True
    return False


def _window_terms_all(text: str, terms: List[str], window: int = 280) -> bool:
    normalized = " ".join(text.split())
    if not normalized:
        return False
    positions: List[int] = []
    for term in terms:
        t = str(term or "").strip()
        if not t:
            continue
        pos = normalized.find(t)
        if pos < 0:
            return False
        positions.append(pos)
    if not positions:
        return False
    return max(positions) - min(positions) <= window


def _match_requirements(
    text: str,
    requirements: List[Dict[str, Any]],
    units_by_dim: Dict[str, List[Dict[str, Any]]],
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, int]]]:
    lower = text.lower()
    results: List[Dict[str, Any]] = []
    dim_stats: Dict[str, Dict[str, int]] = {
        d: {
            "total": 0,
            "hit": 0,
            "mandatory_total": 0,
            "mandatory_hit": 0,
        }
        for d in DIMENSION_IDS
    }

    for req in requirements:
        dim_id = str(req.get("dimension_id") or "01")
        if dim_id not in dim_stats:
            dim_stats[dim_id] = {"total": 0, "hit": 0, "mandatory_total": 0, "mandatory_hit": 0}
        dim_stats[dim_id]["total"] += 1
        mandatory = bool(req.get("mandatory"))
        if mandatory:
            dim_stats[dim_id]["mandatory_total"] += 1

        req_type = str(req.get("req_type") or "keyword")
        patterns = req.get("patterns") or {}
        lint = req.get("lint") or {}
        dim_units = units_by_dim.get(dim_id, [])
        dim_text = _joined_unit_text(dim_units)
        scope_text = dim_text if dim_text else text
        hit = True
        reason = ""
        evaluated = False
        if req_type == "numeric":
            expected = patterns.get("expected_days") or patterns.get("expected_value")
            if expected is not None:
                expected_num = int(float(expected))
                hit = str(expected_num) in text
                reason = f"expected={expected_num}"
                evaluated = True
        elif req_type in {"presence", "keyword"}:
            kws = patterns.get("keywords") or []
            if kws:
                hit = any(str(k).lower() in lower for k in kws if k)
                reason = "keywords"
                evaluated = True
        elif req_type == "semantic":
            hints = patterns.get("hints") or []
            if isinstance(hints, str):
                hints = [hints]
            if hints:
                hit = any(str(h).lower() in lower for h in hints if isinstance(h, str))
                reason = "semantic_hints"
                evaluated = True
        elif req_type == "consistency":
            expected = patterns.get("expected")
            if isinstance(expected, str):
                hit = expected.lower() in lower
                evaluated = True
            elif isinstance(expected, list):
                hit = any(isinstance(x, str) and x.lower() in lower for x in expected)
                evaluated = True
            if evaluated:
                reason = "consistency"

        # advanced pattern checks（用于合肥16章要求包）
        heading_exact = patterns.get("heading_exact")
        if heading_exact:
            heading_ok = _heading_exact_hit(text, str(heading_exact), dim_units)
            hit = hit and heading_ok
            reason = f"{reason}|heading_exact" if reason else "heading_exact"
            evaluated = True

        terms_all = patterns.get("table_header_terms_all")
        if isinstance(terms_all, list) and terms_all:
            within_dim = bool(patterns.get("within_dimension_scope", False))
            table_text = dim_text if within_dim and dim_text else scope_text
            table_ok = _window_terms_all(table_text, [str(t) for t in terms_all], window=320)
            hit = hit and table_ok
            reason = f"{reason}|table_header_terms_all" if reason else "table_header_terms_all"
            evaluated = True

        fallback_terms_all = patterns.get("fallback_line_terms_all")
        if isinstance(fallback_terms_all, list) and fallback_terms_all:
            within_dim = bool(patterns.get("within_dimension_scope", False))
            fb_text = dim_text if within_dim and dim_text else scope_text
            fb_ok = _window_terms_all(fb_text, [str(t) for t in fallback_terms_all], window=320)
            hit = hit and fb_ok
            reason = f"{reason}|fallback_line_terms_all" if reason else "fallback_line_terms_all"
            evaluated = True

        must_have_terms = patterns.get("must_have_terms")
        if isinstance(must_have_terms, list) and must_have_terms:
            must_ok = all(
                str(t).strip() and str(t).lower() in scope_text.lower() for t in must_have_terms
            )
            hit = hit and must_ok
            reason = f"{reason}|must_have_terms" if reason else "must_have_terms"
            evaluated = True

        should_any2 = patterns.get("should_have_terms_any2") or {}
        if isinstance(should_any2, dict) and should_any2.get("terms"):
            min_hit = int(should_any2.get("minimum", 2) or 2)
            cnt = _count_hit_terms(scope_text, [str(t) for t in should_any2.get("terms") or []])
            any2_ok = cnt >= min_hit
            hit = hit and any2_ok
            reason = (
                f"{reason}|should_have_terms_any2:{cnt}"
                if reason
                else f"should_have_terms_any2:{cnt}"
            )
            evaluated = True

        content_any2 = patterns.get("content_terms_any2") or {}
        if isinstance(content_any2, dict) and content_any2:
            category_hits = 0
            for _, terms in content_any2.items():
                if isinstance(terms, list) and _contains_any_terms(
                    scope_text, [str(t) for t in terms]
                ):
                    category_hits += 1
            content_ok = category_hits >= 2
            hit = hit and content_ok
            reason = (
                f"{reason}|content_terms_any2:{category_hits}"
                if reason
                else f"content_terms_any2:{category_hits}"
            )
            evaluated = True

        topics_rule = patterns.get("topic_terms_at_least3") or {}
        if isinstance(topics_rule, dict) and topics_rule.get("topics"):
            topics = [str(t) for t in topics_rule.get("topics") or []]
            minimum = int(topics_rule.get("minimum", 3) or 3)
            topic_hit = _count_hit_terms(scope_text, topics)
            topics_ok = topic_hit >= minimum
            hit = hit and topics_ok
            reason = f"{reason}|topic_terms:{topic_hit}" if reason else f"topic_terms:{topic_hit}"
            evaluated = True

        measure_accept_rule = patterns.get("must_include_measure_and_acceptance") or {}
        if isinstance(measure_accept_rule, dict) and measure_accept_rule:
            has_measure = _contains_any_terms(
                scope_text, [str(t) for t in measure_accept_rule.get("measure_terms") or []]
            )
            has_acceptance = _contains_any_terms(
                scope_text, [str(t) for t in measure_accept_rule.get("acceptance_terms") or []]
            )
            mea_ok = has_measure and has_acceptance
            hit = hit and mea_ok
            reason = f"{reason}|measure_acceptance" if reason else "measure_acceptance"
            evaluated = True

        risk_terms = [str(t) for t in patterns.get("risk_terms") or []]
        measure_terms = [str(t) for t in patterns.get("measure_terms") or []]
        linkage_rule = patterns.get("linkage_rule") or {}
        if risk_terms and measure_terms:
            same_dim_only = bool(linkage_rule.get("same_dimension_scope", True))
            neighbor = int(linkage_rule.get("within_same_heading_or_neighbor_units", 2) or 2)
            rm_units = (
                dim_units
                if same_dim_only
                else [u for units in units_by_dim.values() for u in units]
            )
            linked = _risk_measure_linked(
                rm_units,
                risk_terms=risk_terms,
                measure_terms=measure_terms,
                neighbor_limit=neighbor,
            )
            # 单元不足时走文本窗口兜底
            if not linked and not rm_units and not same_dim_only:
                risk_pattern = "|".join(re.escape(k) for k in risk_terms if k)
                measure_pattern = "|".join(re.escape(k) for k in measure_terms if k)
                if risk_pattern and measure_pattern:
                    linked = bool(
                        re.search(rf"(?:{risk_pattern}).{{0,120}}(?:{measure_pattern})", scope_text)
                        or re.search(
                            rf"(?:{measure_pattern}).{{0,120}}(?:{risk_pattern})", scope_text
                        )
                    )
            hit = hit and linked
            reason = f"{reason}|risk_measure_linked" if reason else "risk_measure_linked"
            evaluated = True

        if not evaluated:
            hit = False
            reason = "no_pattern"

        if hit:
            dim_stats[dim_id]["hit"] += 1
            if mandatory:
                dim_stats[dim_id]["mandatory_hit"] += 1
        results.append(
            {
                "requirement_id": req.get("id"),
                "dimension_id": dim_id,
                "hit": hit,
                "mandatory": mandatory,
                "reason": reason,
                "label": req.get("req_label", ""),
                "req_type": req_type,
                "lint_issue_code": lint.get("issue_code"),
                "lint_severity": lint.get("severity"),
                "lint_why_it_matters": lint.get("why_it_matters"),
                "lint_fix_template": lint.get("fix_template"),
            }
        )

    return results, dim_stats


def _score_14_dims(
    dim_id: str,
    units: List[Dict[str, Any]],
    req_stats: Dict[str, int],
) -> Dict[str, Any]:
    evidence_count = len(units)
    if req_stats.get("total", 0) > 0:
        coverage = 2.5 * (req_stats.get("hit", 0) / max(1, req_stats.get("total", 0)))
    else:
        coverage = 2.5 * min(1.0, evidence_count / 4.0)

    has_definition = any(u.get("tag_definition") for u in units)
    has_analysis = any(u.get("tag_analysis") for u in units)
    has_solution = any(u.get("tag_solution") for u in units)
    base_closure = 2.5 * (sum([has_definition, has_analysis, has_solution]) / 3.0)
    if has_analysis and has_solution:
        closure = base_closure
    else:
        # 未形成 analysis+solution 双闭环时，Closure 不给满档
        closure = min(1.5, base_closure)

    landing_scores: List[float] = []
    specificity_scores: List[float] = []
    for u in units:
        present = sum(
            [
                1 if u.get("landing_param") else 0,
                1 if u.get("landing_freq") else 0,
                1 if u.get("landing_accept") else 0,
                1 if u.get("landing_role") else 0,
            ]
        )
        landing_scores.append(present / 4.0)
        specificity_scores.append(float(u.get("specificity_score", 0.0)))

    if landing_scores:
        landing = 2.5 * (
            sum(sorted(landing_scores, reverse=True)[: min(3, len(landing_scores))])
            / min(3, len(landing_scores))
        )
    else:
        landing = 0.0
    specificity = (
        2.5 * (sum(specificity_scores) / len(specificity_scores)) if specificity_scores else 0.0
    )

    # mandatory requirement 未满足时，Coverage 封顶
    mandatory_total = int(req_stats.get("mandatory_total", 0) or 0)
    mandatory_hit = int(req_stats.get("mandatory_hit", 0) or 0)
    if mandatory_total > 0 and mandatory_hit < mandatory_total:
        coverage = min(coverage, 1.0)

    dim_score = min(10.0, max(0.0, coverage + closure + landing + specificity))
    return {
        "dim_score": round(dim_score, 2),
        "subscores": {
            "Coverage": round(coverage, 2),
            "Closure": round(closure, 2),
            "Landing": round(landing, 2),
            "Specificity": round(specificity, 2),
        },
        "coverage_rate": round((req_stats.get("hit", 0) / max(1, req_stats.get("total", 0))), 4)
        if req_stats.get("total", 0) > 0
        else None,
        "evidence_count": evidence_count,
    }


def _score_dim07(text: str, units: List[Dict[str, Any]]) -> Dict[str, Any]:
    corpus = " ".join([u.get("text", "") for u in units]) if units else text
    s1 = (
        2.0
        if any(k in corpus for k in ["危大工程", "重难点", "重点难点", "专项方案", "论证"])
        else 0.0
    )
    s2 = (
        2.0
        if any(k in corpus for k in ["风险", "隐患", "不利因素", "可能", "易导致", "影响"])
        else 0.0
    )
    s3 = 0.0
    has_param = bool(re.search(r"(?:控制在|不大于|不小于|≤|≥|<|>)\s*\d+(?:\.\d+)?", corpus))
    has_freq = bool(
        re.search(r"(?:每日|每周|每月|每班|每次|每\d+天|每\d+小时|\d+次/天|\d+次/周)", corpus)
    )
    has_role = bool(
        re.search(
            r"(?:项目经理|技术负责人|施工员|质检员|安全员|班组长).{0,10}(?:负责|牵头|组织|落实)",
            corpus,
        )
    )
    has_accept = bool(re.search(r"(?:报验|签认|验收|旁站|自检|互检|交接检|隐蔽验收)", corpus))
    if sum([has_param, has_freq, has_role, has_accept]) >= 2:
        s3 = 2.0
    s4 = (
        2.0
        if any(k in corpus for k in ["监测", "旁站", "报验", "签认", "隐蔽验收", "销项"])
        else 0.0
    )
    s5 = 2.0 if any(k in corpus for k in ["应急预案", "处置", "停工", "复工条件", "整改"]) else 0.0
    dim_score = s1 + s2 + s3 + s4 + s5
    return {
        "dim_score": round(dim_score, 2),
        "subscores": {
            "危大工程/重点难点识别完整": s1,
            "风险点分析到位": s2,
            "措施参数化落地": s3,
            "监测与验收闭环": s4,
            "应急与纠偏": s5,
        },
        "coverage_rate": None,
        "evidence_count": len(units),
    }


def _score_dim09(text: str, units: List[Dict[str, Any]]) -> Dict[str, Any]:
    corpus = " ".join([u.get("text", "") for u in units]) if units else text
    plan_hits = sum(1 for k in ["总控计划", "月计划", "周计划", "日计划"] if k in corpus)
    s1 = 2.0 if plan_hits >= 2 else 0.0
    s2 = 2.0 if any(k in corpus for k in ["节点", "关键线路", "里程碑", "倒排", "穿插"]) else 0.0
    s3 = 2.0 if any(k in corpus for k in ["劳动力", "机械", "材料保障", "冗余", "调配"]) else 0.0
    s4 = (
        2.0
        if any(k in corpus for k in ["纠偏", "赶工", "调整", "追赶", "索赔工期", "顺延条件"])
        else 0.0
    )
    s5 = (
        2.0
        if any(k in corpus for k in ["每日", "每周", "例会", "调度会", "17:00", "日报", "周报"])
        else 0.0
    )
    dim_score = s1 + s2 + s3 + s4 + s5
    return {
        "dim_score": round(dim_score, 2),
        "subscores": {
            "计划体系": s1,
            "节点与关键线路": s2,
            "资源与人机保障": s3,
            "纠偏机制": s4,
            "例会与跟踪频次": s5,
        },
        "coverage_rate": None,
        "evidence_count": len(units),
    }


def _consistency_bonus(
    text: str,
    units_by_dim: Dict[str, List[Dict[str, Any]]],
    anchors: List[Dict[str, Any]],
) -> Tuple[float, List[Dict[str, Any]]]:
    bonus = 0.0
    checks: List[Dict[str, Any]] = []
    for a in anchors:
        key = str(a.get("anchor_key") or "")
        if key == "contract_duration_days":
            expected = a.get("value_num")
            if expected is not None:
                val = str(int(float(expected)))
                dim9_text = " ".join(u.get("text", "") for u in units_by_dim.get("09", []))
                dim15_text = " ".join(u.get("text", "") for u in units_by_dim.get("15", []))
                if val in dim9_text and val in dim15_text:
                    bonus += 2.5
                    checks.append({"check": "duration_consistency", "ok": True, "anchor": key})
                else:
                    checks.append({"check": "duration_consistency", "ok": False, "anchor": key})
        elif key == "quality_standard":
            dim8_text = " ".join(u.get("text", "") for u in units_by_dim.get("08", []))
            if dim8_text:
                bonus += 2.5
                checks.append({"check": "quality_consistency", "ok": True, "anchor": key})
            else:
                checks.append({"check": "quality_consistency", "ok": False, "anchor": key})
        elif key == "dangerous_works_list":
            dim7 = " ".join(u.get("text", "") for u in units_by_dim.get("07", []))
            dim2 = " ".join(u.get("text", "") for u in units_by_dim.get("02", []))
            if dim7 and dim2:
                bonus += 2.5
                checks.append({"check": "dangerous_work_consistency", "ok": True, "anchor": key})
            else:
                checks.append({"check": "dangerous_work_consistency", "ok": False, "anchor": key})
        elif key == "key_milestones":
            dim9 = " ".join(u.get("text", "") for u in units_by_dim.get("09", []))
            dim15 = " ".join(u.get("text", "") for u in units_by_dim.get("15", []))
            if dim9 and dim15:
                bonus += 2.5
                checks.append({"check": "milestone_consistency", "ok": True, "anchor": key})
            else:
                checks.append({"check": "milestone_consistency", "ok": False, "anchor": key})
    return min(10.0, bonus), checks


def _empty_penalties(text: str, lexicon: Dict[str, Any]) -> List[Dict[str, Any]]:
    kws = (lexicon.get("empty_promises") or {}).get("keywords", [])
    penalties: List[Dict[str, Any]] = []
    total = 0.0
    for kw in kws:
        if not kw:
            continue
        for m in re.finditer(re.escape(str(kw)), text):
            snippet = _snippet(text, m.start(), m.end())
            has_ground = bool(
                re.search(r"\d", snippet)
                or re.search(r"(?:报验|签认|验收|旁站|自检|互检|交接检|隐蔽验收)", snippet)
                or re.search(r"(?:项目经理|技术负责人|施工员|安全员|质检员|班组长)", snippet)
                or re.search(r"(?:每日|每周|每月|每班|每次|\d+次/天|\d+次/周)", snippet)
            )
            if has_ground:
                continue
            penalties.append(
                {
                    "code": "P-EMPTY-002",
                    "points": 0.5,
                    "reason": f"空泛承诺未绑定证据：{kw}",
                    "evidence_refs": [
                        {"locator": f"char:{m.start()}-{m.end()}", "text_snippet": snippet}
                    ],
                }
            )
            total += 0.5
            if total >= 3.0:
                return penalties
    return penalties


def _action_penalties(text: str, lexicon: Dict[str, Any]) -> List[Dict[str, Any]]:
    triggers = (lexicon.get("action_triggers") or []) + [
        "采取",
        "采用",
        "设置",
        "配置",
        "落实",
        "执行",
        "实施",
        "组织",
        "报验",
        "验收",
    ]
    penalties: List[Dict[str, Any]] = []
    total = 0.0
    for kw in triggers:
        if not kw:
            continue
        for m in re.finditer(re.escape(str(kw)), text):
            snippet = _snippet(text, m.start(), m.end(), window=60)
            has_accept = bool(
                re.search(r"(?:报验|签认|验收|旁站|自检|互检|交接检|隐蔽验收|销项)", snippet)
            )
            has_role = bool(
                re.search(r"(?:项目经理|技术负责人|施工员|安全员|质检员|班组长)", snippet)
            )
            if has_accept and has_role:
                continue
            miss = []
            if not has_role:
                miss.append("role")
            if not has_accept:
                miss.append("accept")
            penalties.append(
                {
                    "code": "P-ACTION-002",
                    "points": 0.8,
                    "reason": "措施缺少硬要素：" + ",".join(miss),
                    "evidence_refs": [
                        {"locator": f"char:{m.start()}-{m.end()}", "text_snippet": snippet}
                    ],
                }
            )
            total += 0.8
            if total >= 6.0:
                return penalties
    return penalties


def _consistency_penalties(text: str, anchors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    penalties: List[Dict[str, Any]] = []
    total = 0.0
    duration_anchor = next(
        (a for a in anchors if str(a.get("anchor_key")) == "contract_duration_days"), None
    )
    if duration_anchor and duration_anchor.get("value_num") is not None:
        expected = int(float(duration_anchor["value_num"]))
        for m in re.finditer(r"工期[^\n。]{0,20}?(\d{2,4})\s*(?:日历天|天)", text):
            val = int(m.group(1))
            if val == expected:
                continue
            penalties.append(
                {
                    "code": "P-CONSIST-001",
                    "points": 2.0,
                    "reason": f"工期锚点冲突：锚点{expected}天，文中{val}天",
                    "evidence_refs": [
                        {
                            "locator": f"char:{m.start()}-{m.end()}",
                            "text_snippet": _snippet(text, m.start(), m.end()),
                        }
                    ],
                }
            )
            total += 2.0
            if total >= 6.0:
                break
    return penalties


def _build_lint_findings(
    requirement_hits: List[Dict[str, Any]],
    penalties: List[Dict[str, Any]],
    dim_scores: Dict[str, Dict[str, Any]],
    anchors: List[Dict[str, Any]],
    text: str,
    units_by_dim: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    for req in requirement_hits:
        if req.get("mandatory") and not req.get("hit"):
            issue_code = str(req.get("lint_issue_code") or "MissingRequirement")
            severity = str(req.get("lint_severity") or "high")
            why_it_matters = str(
                req.get("lint_why_it_matters") or f"未满足必备要求：{req.get('label', '')}"
            )
            fix_template = str(
                req.get("lint_fix_template")
                or "补充【参数/阈值】、【频次】、【责任岗位】、【验收动作】并与项目锚点保持一致。"
            )
            findings.append(
                {
                    "issue_code": issue_code,
                    "dimension_id": req.get("dimension_id"),
                    "severity": severity,
                    "evidence_locator": None,
                    "suggested_heading_path": None,
                    "why_it_matters": why_it_matters,
                    "fix_template": fix_template,
                }
            )
    for p in penalties:
        code = str(p.get("code") or "")
        issue_map = {
            "P-EMPTY-002": "EmptyPromiseWithoutEvidence",
            "P-ACTION-002": "ActionMissingHardElements",
            "P-CONSIST-001": "ConsistencyConflict",
        }
        if code not in issue_map:
            continue
        findings.append(
            {
                "issue_code": issue_map[code],
                "dimension_id": "09" if code == "P-CONSIST-001" else "02",
                "severity": "high" if code != "P-EMPTY-002" else "medium",
                "evidence_locator": (p.get("evidence_refs") or [{}])[0].get("locator"),
                "suggested_heading_path": None,
                "why_it_matters": p.get("reason", ""),
                "fix_template": "将描述改为：由【责任岗位】按【频次】执行，控制【参数/阈值】，并完成【验收动作】。",
            }
        )
    for dim_id, s in dim_scores.items():
        subs = s.get("subscores") or {}
        closure = float(subs.get("Closure", 2.5))
        if closure < 1.5:
            findings.append(
                {
                    "issue_code": "ClosureGap",
                    "dimension_id": dim_id,
                    "severity": "medium",
                    "evidence_locator": None,
                    "suggested_heading_path": f"{dim_id} 维度补强",
                    "why_it_matters": "定义-分析-措施闭环不足，影响青天可解释性评分。",
                    "fix_template": "补充“现状定义→风险分析→控制措施”三段式，并加上验收闭环。",
                }
            )

    def _anchor_referenced(anchor_key: str) -> bool:
        for units in units_by_dim.values():
            for unit in units:
                links = unit.get("anchor_links") or []
                if anchor_key in links:
                    return True
        return False

    anchor_dim_map = {
        "contract_duration_days": "09",
        "quality_standard": "08",
        "dangerous_works_list": "07",
        "key_milestones": "09",
        "safety_civil_clauses": "02",
        "project_scope": "01",
    }
    seen_anchor_issues: set[tuple[str, str]] = set()
    has_duration_conflict = any(str(p.get("code") or "") == "P-CONSIST-001" for p in penalties)

    for anchor in anchors:
        anchor_key = str(anchor.get("anchor_key") or "").strip()
        if not anchor_key:
            continue
        dim_id = anchor_dim_map.get(anchor_key, "01")

        if anchor_key == "contract_duration_days" and has_duration_conflict:
            issue_pair = ("AnchorMismatch", anchor_key)
            if issue_pair not in seen_anchor_issues:
                seen_anchor_issues.add(issue_pair)
                findings.append(
                    {
                        "issue_code": "AnchorMismatch",
                        "dimension_id": dim_id,
                        "severity": "high",
                        "evidence_locator": None,
                        "suggested_heading_path": "09 进度保障措施",
                        "why_it_matters": "工期与项目锚点不一致，青天一致性校验将直接扣分。",
                        "fix_template": "统一工期天数，确保总控计划、资源计划与锚点一致。",
                    }
                )
            continue

        missing = False
        if anchor_key == "contract_duration_days":
            missing = not bool(re.search(r"工期[^\n。]{0,20}?\d{2,4}\s*(?:日历天|天)", text))
        elif anchor_key == "quality_standard":
            missing = (not _anchor_referenced(anchor_key)) and (not bool(units_by_dim.get("08")))
        elif anchor_key == "dangerous_works_list":
            missing = not (bool(units_by_dim.get("07")) and bool(units_by_dim.get("02")))
        elif anchor_key == "key_milestones":
            missing = not (bool(units_by_dim.get("09")) and bool(units_by_dim.get("15")))
        else:
            missing = not _anchor_referenced(anchor_key)

        if missing:
            issue_pair = ("AnchorMissing", anchor_key)
            if issue_pair not in seen_anchor_issues:
                seen_anchor_issues.add(issue_pair)
                findings.append(
                    {
                        "issue_code": "AnchorMissing",
                        "dimension_id": dim_id,
                        "severity": "medium",
                        "evidence_locator": None,
                        "suggested_heading_path": f"{dim_id} 维度补强",
                        "why_it_matters": f"未体现项目锚点：{anchor_key}，可能导致青天规则命中不足。",
                        "fix_template": "在对应章节补充与锚点一致的参数、频次、责任岗位和验收闭环。",
                    }
                )
    return findings


def _build_suggestions(
    dim_scores: Dict[str, Dict[str, Any]],
    weights_norm: Dict[str, float],
    *,
    probe_dimensions: List[Dict[str, Any]] | None = None,
) -> List[Dict[str, Any]]:
    suggestions: List[Dict[str, Any]] = []
    for dim_id, score_item in dim_scores.items():
        score = float(score_item.get("dim_score", 0.0))
        if score >= 8.0:
            continue
        gap = max(0.0, 8.0 - score)
        weight = float(weights_norm.get(dim_id, 1.0 / 16))
        expected_gain = round(min(20.0, gap * (1.2 + weight * 8)), 2)
        suggestions.append(
            {
                "dimension_id": dim_id,
                "title": f"提升维度{dim_id}分数",
                "expected_gain": expected_gain,
                "action_steps": [
                    "补充与项目锚点一致的参数化内容",
                    "至少补齐责任岗位与验收动作",
                    "将措施写成“频次+阈值+闭环”格式",
                ],
                "evidence_to_add": ["控制参数/阈值", "执行频次", "责任岗位", "验收动作"],
                "references": [],
            }
        )
    suggestions.sort(
        key=lambda x: (
            -float(x.get("expected_gain", 0.0)),
            -float(weights_norm.get(str(x.get("dimension_id")), 1.0 / 16)),
        )
    )
    if probe_dimensions:
        rag_suggestions = build_probe_template_suggestions(probe_dimensions, threshold=0.8)
        suggestions = rag_suggestions + suggestions
    return suggestions


def score_text_v2(
    *,
    submission_id: str,
    text: str,
    lexicon: Dict[str, Any],
    weights_norm: Dict[str, float] | None = None,
    anchors: List[Dict[str, Any]] | None = None,
    requirements: List[Dict[str, Any]] | None = None,
    evidence_units: List[Dict[str, Any]] | None = None,
    strict_pre_flight: bool = False,
) -> Dict[str, Any]:
    pre_flight_result: Dict[str, object] = {"ok": True, "fatal": False}
    if strict_pre_flight:
        try:
            pre_flight_result = pre_flight_check(text, raise_on_fatal=True)
        except PreFlightFatalError as exc:
            raise ValueError(f"红线校验未通过：{exc}") from exc
    else:
        pre_flight_result = pre_flight_check(text, raise_on_fatal=False)

    anchors = anchors or []
    requirements = requirements or []
    if evidence_units is None:
        evidence_units = build_evidence_units(
            submission_id=submission_id, text=text, lexicon=lexicon, anchors=anchors
        )

    if not weights_norm:
        weights_norm = {d: 1.0 / 16 for d in DIMENSION_IDS}
    else:
        # 对缺失维度做归一兜底
        filled = {d: float(weights_norm.get(d, 0.0)) for d in DIMENSION_IDS}
        total = sum(filled.values())
        if total <= 0:
            filled = {d: 1.0 / 16 for d in DIMENSION_IDS}
        else:
            filled = {d: v / total for d, v in filled.items()}
        weights_norm = filled

    units_by_dim = _collect_units_by_dim(evidence_units)
    req_hits, req_dim_stats = _match_requirements(text, requirements, units_by_dim)

    dim_scores: Dict[str, Dict[str, Any]] = {}
    for dim_id in DIMENSION_IDS:
        units = units_by_dim.get(dim_id, [])
        req_stat = req_dim_stats.get(dim_id, {"total": 0, "hit": 0})
        if dim_id == "07":
            dim_scores[dim_id] = _score_dim07(text, units)
        elif dim_id == "09":
            dim_scores[dim_id] = _score_dim09(text, units)
        else:
            dim_scores[dim_id] = _score_14_dims(dim_id, units, req_stat)

    dim_total_80 = 0.0
    for dim_id in DIMENSION_IDS:
        dim_score = float(dim_scores.get(dim_id, {}).get("dim_score", 0.0))
        dim_total_80 += (
            DIM_BASE_LEGACY_MAX * float(weights_norm.get(dim_id, 1.0 / 16)) * (dim_score / 10.0)
        )

    consistency_bonus, consistency_checks = _consistency_bonus(text, units_by_dim, anchors)

    penalties = []
    penalties.extend(_empty_penalties(text, lexicon))
    penalties.extend(_action_penalties(text, lexicon))
    penalties.extend(_consistency_penalties(text, anchors))
    penalty_points = round(sum(float(p.get("points", 0.0)) for p in penalties), 2)

    rule_total, dim_total_90 = compute_v2_rule_total(
        dim_total_80=dim_total_80,
        consistency_bonus=consistency_bonus,
        penalty_points=penalty_points,
    )

    lint_findings = _build_lint_findings(
        req_hits,
        penalties,
        dim_scores,
        anchors=anchors,
        text=text,
        units_by_dim=units_by_dim,
    )
    probe_dimensions = compute_probe_dimensions(text=text, dim_scores=dim_scores)
    suggestions = _build_suggestions(
        dim_scores,
        weights_norm,
        probe_dimensions=probe_dimensions,
    )

    mandatory_total = sum(1 for r in req_hits if r.get("mandatory"))
    mandatory_hit = sum(1 for r in req_hits if r.get("mandatory") and r.get("hit"))
    req_hit_rate = round((mandatory_hit / mandatory_total), 4) if mandatory_total > 0 else None
    requirement_pack_versions = sorted(
        {
            str(r.get("source_pack_version") or "").strip()
            for r in requirements
            if str(r.get("source_pack_version") or "").strip()
        }
    )

    return {
        "engine_version": "v2",
        "rule_total_score": rule_total,
        "dim_total_80": round(dim_total_80, 2),
        "dim_total_90": dim_total_90,
        "consistency_bonus": round(consistency_bonus, 2),
        "consistency_checks": consistency_checks,
        "rule_dim_scores": dim_scores,
        "penalties": penalties,
        "lint_findings": lint_findings,
        "suggestions": suggestions,
        "probe_dimensions": probe_dimensions,
        "pre_flight": pre_flight_result,
        "requirement_hits": req_hits,
        "mandatory_req_hit_rate": req_hit_rate,
        "requirement_pack_versions": requirement_pack_versions,
        "evidence_units_count": len(evidence_units),
        "evidence_units": evidence_units,
    }
