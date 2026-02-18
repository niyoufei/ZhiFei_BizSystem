from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Dict, List

from app.engine.dimensions import DIMENSIONS, score_dim_07, score_dim_09, score_dimension
from app.engine.evidence import find_evidence_spans
from app.engine.logic_lock import score_logic_lock
from app.schemas import DimensionScore, LogicLockResult, Penalty, ScoreReport, Suggestion


def _dimension_weighted_score(score: float, max_score: float, weight: float) -> float:
    if max_score <= 0:
        return 0.0
    return (score / max_score) * weight


def _has_grounding_elements(snippet: str) -> bool:
    patterns = [
        r"\d+(?:\.\d+)?\s*(?:m3|m³|m2|m²|㎡|㎥|㎠|mm|cm|m|t|kg|台|套|处|项|座|段|根|个|%|天|小时|h|d)",
        r"[≤≥<>]",
        r"(?:每日|每周|每月|每班|每次|每\d+天|每\d+小时|\d+次/天|\d+次/周|\d+次)",
    ]
    keywords = [
        "项目经理",
        "技术负责人",
        "施工员",
        "安全员",
        "质检员",
        "班组长",
        "报验",
        "签认",
        "验收",
        "旁站",
        "自检",
        "互检",
        "交接检",
        "隐蔽验收",
    ]
    for pattern in patterns:
        if re.search(pattern, snippet, flags=re.IGNORECASE):
            return True
    for kw in keywords:
        if kw in snippet:
            return True
    return False


def _empty_promises_penalties(text: str, rubric: Dict, lexicon: Dict) -> List[Penalty]:
    config = rubric.get("penalties", {}).get("empty_promises", {})
    deduct = float(config.get("deduct", 0.5))
    max_deduct = float(config.get("max_deduct", 3.0))
    window = int(config.get("window", 40))
    keywords = lexicon.get("empty_promises", {}).get("keywords", [])

    penalties: List[Penalty] = []
    total_deduct = 0.0
    for kw in keywords:
        spans = find_evidence_spans(text, keywords=[kw], window=window, max_hits=1000)
        for span in spans:
            if _has_grounding_elements(span.snippet):
                continue
            penalties.append(
                Penalty(
                    code="P-EMPTY-001",
                    message=f"存在空泛承诺但缺少落地要素：{kw}",
                    evidence_span=span,
                    deduct=deduct,
                )
            )
            total_deduct += deduct
            if total_deduct >= max_deduct:
                return penalties
    return penalties


def _action_missing_penalties(text: str, rubric: Dict) -> List[Penalty]:
    config = rubric.get("penalties", {}).get("action_missing", {})
    deduct_per = float(config.get("deduct_per", 0.5))
    max_deduct = float(config.get("max_deduct", 5.0))
    window = int(config.get("window", 60))

    action_triggers = [
        "采取",
        "采用",
        "设置",
        "增设",
        "配置",
        "落实",
        "执行",
        "实施",
        "组织",
        "管控",
        "监督",
        "检查",
        "复核",
        "报验",
        "验收",
    ]

    penalties: List[Penalty] = []
    total_deduct = 0.0
    spans = find_evidence_spans(text, keywords=action_triggers, window=window, max_hits=1000)

    for span in spans:
        if total_deduct >= max_deduct:
            break
        snippet = span.snippet

        has_param = bool(
            re.search(
                r"\d+(?:\.\d+)?\s*(?:m3|m³|m2|m²|㎡|㎥|㎠|mm|cm|m|t|kg|台|套|处|项|座|段|根|个|%|天|小时|h|d)",
                snippet,
                flags=re.IGNORECASE,
            )
            or re.search(r"[≤≥<>]", snippet)
        )
        has_freq = bool(
            re.search(
                r"(?:每日|每周|每月|每班|每次|每\d+天|每\d+小时|\d+次/天|\d+次/周|\d+次|次/天|次/周)",
                snippet,
                flags=re.IGNORECASE,
            )
        )
        has_acceptance = bool(
            re.search(
                r"(?:报验|签认|验收|旁站|自检|互检|交接检|隐蔽验收|销项)",
                snippet,
                flags=re.IGNORECASE,
            )
        )
        has_role = bool(
            re.search(
                r"(?:项目经理|技术负责人|施工员|安全员|质检员|班组长)",
                snippet,
                flags=re.IGNORECASE,
            )
        )

        present_categories = sum([has_param, has_freq, has_acceptance, has_role])
        if present_categories >= 2:
            continue

        tags: List[str] = []
        if not has_param:
            tags.append("missing_param")
        if not has_freq:
            tags.append("missing_freq")
        if not has_acceptance:
            tags.append("missing_acceptance")
        if not has_role:
            tags.append("missing_role")

        penalties.append(
            Penalty(
                code="P-ACTION-001",
                message="措施表述缺少落实要素（参数/频次/验收/责任不足）",
                evidence_span=span,
                deduct=deduct_per,
                tags=tags,
            )
        )
        total_deduct += deduct_per

    return penalties


def score_text(
    text: str,
    rubric: Dict,
    lexicon: Dict,
    dimension_multipliers: Dict[str, float] | None = None,
) -> ScoreReport:
    dimension_scores: Dict[str, DimensionScore] = {}
    total_dimension_score = 0.0
    dimension_multipliers = dimension_multipliers or {}

    for dim_id, meta in DIMENSIONS.items():
        sub_scores = None
        if dim_id == "07":
            score, hits, evidence, sub_scores = score_dim_07(text, rubric)
        elif dim_id == "09":
            score, hits, evidence, sub_scores = score_dim_09(text, rubric)
        else:
            score, hits, evidence = score_dimension(dim_id, text, rubric, lexicon)
        settings = rubric["dimensions"][dim_id]
        max_score = float(settings["max_score"])
        weight = float(settings["weight"])
        weight *= float(dimension_multipliers.get(dim_id, 1.0))
        score = min(max_score, score)
        weighted = _dimension_weighted_score(score, max_score, weight)
        total_dimension_score += weighted

        dimension_scores[dim_id] = DimensionScore(
            id=dim_id,
            name=meta["name"],
            module=meta["module"],
            score=score,
            max_score=max_score,
            hits=hits,
            evidence=evidence,
            sub_scores=sub_scores,
        )

    logic_lock_raw, logic_penalties = score_logic_lock(text, rubric, lexicon)
    logic_lock = LogicLockResult(**logic_lock_raw)
    logic_lock_max = float(rubric["logic_lock"]["max_bonus"])
    logic_lock_total = (
        logic_lock.definition_score + logic_lock.analysis_score + logic_lock.solution_score
    )
    logic_lock_bonus = (
        logic_lock_total / (3 * rubric["logic_lock"]["max_step_score"])
    ) * logic_lock_max

    penalties: List[Penalty] = []
    penalties_logic_lock: List[Penalty] = []
    penalty_sum = 0.0
    for item in logic_penalties:
        penalty_value = float(item["value"])
        penalty_sum += penalty_value
        penalty_obj = Penalty(
            code=item["code"],
            message=item["message"],
            evidence_span=item["evidence_span"],
            deduct=penalty_value,
        )
        penalties.append(penalty_obj)
        penalties_logic_lock.append(penalty_obj)
    empty_penalties = _empty_promises_penalties(text, rubric, lexicon)
    action_penalties = _action_missing_penalties(text, rubric)

    for penalty in empty_penalties:
        penalty_sum += float(penalty.deduct or 0.0)
    for penalty in action_penalties:
        penalty_sum += float(penalty.deduct or 0.0)

    penalties.extend(empty_penalties)
    penalties.extend(action_penalties)
    penalties_empty_promises = empty_penalties
    penalties_action_missing = action_penalties

    suggestions: List[Suggestion] = []
    for dim_id, dim_score in dimension_scores.items():
        threshold = float(rubric["dimensions"][dim_id]["suggestion_threshold"])
        if dim_score.score < threshold:
            suggestion_text = rubric["dimensions"][dim_id]["suggestion"]
            expected_gain = float(rubric["dimensions"][dim_id]["suggested_gain"])
            suggestions.append(
                Suggestion(
                    dimension=dim_id,
                    action=suggestion_text,
                    expected_gain=expected_gain,
                )
            )

    total_score = max(
        0.0,
        min(100.0, total_dimension_score + logic_lock_bonus - penalty_sum),
    )

    report = ScoreReport(
        total_score=round(total_score, 2),
        dimension_scores=dimension_scores,
        logic_lock=logic_lock,
        penalties=penalties,
        penalties_logic_lock=penalties_logic_lock,
        penalties_empty_promises=penalties_empty_promises,
        penalties_action_missing=penalties_action_missing,
        suggestions=suggestions,
        meta={
            "rubric_version": rubric.get("version", "v1"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "input_length": len(text),
            "applied_multipliers": dimension_multipliers,
        },
    )
    return report
