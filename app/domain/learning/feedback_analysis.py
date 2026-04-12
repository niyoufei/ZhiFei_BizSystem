from __future__ import annotations

from statistics import pstdev
from typing import Dict, List, Optional

from app.domain.learning.ground_truth_records import convert_score_to_100, to_float_or_none


def resolve_report_predicted_score_100(
    report: Dict[str, object],
    *,
    project_score_scale_max: int,
) -> Optional[float]:
    pred_score_100 = to_float_or_none(report.get("pred_total_score"))
    if pred_score_100 is None:
        pred_score_100 = to_float_or_none(report.get("total_score"))
    if pred_score_100 is None:
        pred_score_100 = to_float_or_none(report.get("rule_total_score"))
    if pred_score_100 is None:
        return None
    if int(project_score_scale_max) == 5 and pred_score_100 <= 5.0:
        pred_score_100 = float(convert_score_to_100(pred_score_100, 5) or 0.0)
    return float(pred_score_100)


def build_high_consensus_auto_approval(
    *,
    gt_for_learning: Dict[str, object],
    min_judges: int,
    max_score_span: float,
    max_score_stddev: float,
    max_final_delta: float,
) -> Dict[str, object]:
    judge_scores_raw = gt_for_learning.get("judge_scores")
    if not isinstance(judge_scores_raw, list):
        return {"eligible": False}
    judge_scores: List[float] = []
    for value in judge_scores_raw:
        normalized = to_float_or_none(value)
        if normalized is None:
            return {"eligible": False}
        judge_scores.append(float(normalized))
    if len(judge_scores) < int(min_judges):
        return {"eligible": False}
    avg_score = sum(judge_scores) / float(len(judge_scores))
    score_span = max(judge_scores) - min(judge_scores)
    score_stddev = pstdev(judge_scores) if len(judge_scores) > 1 else 0.0
    final_score = float(to_float_or_none(gt_for_learning.get("final_score")) or 0.0)
    final_vs_avg_abs_delta = abs(final_score - avg_score)
    eligible = (
        score_span <= float(max_score_span)
        and score_stddev <= float(max_score_stddev)
        and final_vs_avg_abs_delta <= float(max_final_delta)
    )
    return {
        "eligible": bool(eligible),
        "judge_count": len(judge_scores),
        "avg_score": round(avg_score, 2),
        "score_span": round(score_span, 2),
        "score_stddev": round(score_stddev, 4),
        "final_vs_avg_abs_delta": round(final_vs_avg_abs_delta, 2),
    }


def build_learning_quality_gate_payload(
    report: Dict[str, object],
    *,
    min_awareness_score: float,
    min_evidence_hits: int,
) -> Dict[str, object]:
    report_meta = report.get("meta") if isinstance(report.get("meta"), dict) else {}
    material_gate = (
        report_meta.get("material_utilization_gate")
        if isinstance(report_meta.get("material_utilization_gate"), dict)
        else {}
    )
    evidence_trace = (
        report_meta.get("evidence_trace")
        if isinstance(report_meta.get("evidence_trace"), dict)
        else {}
    )
    score_self_awareness = (
        report_meta.get("score_self_awareness")
        if isinstance(report_meta.get("score_self_awareness"), dict)
        else {}
    )
    material_quality = (
        report_meta.get("material_quality")
        if isinstance(report_meta.get("material_quality"), dict)
        else {}
    )
    awareness_score = to_float_or_none(score_self_awareness.get("score_0_100"))
    awareness_level = str(score_self_awareness.get("level") or "").strip()
    evidence_hits = int(to_float_or_none(evidence_trace.get("total_hits")) or 0)
    material_gate_blocked = bool(material_gate.get("blocked"))
    total_parsed_chars = int(to_float_or_none(material_quality.get("total_parsed_chars")) or 0)

    reasons: List[str] = []
    reason_labels: List[str] = []
    if material_gate_blocked:
        reasons.append("material_gate_blocked")
        reason_labels.append("资料利用门禁未通过")
    if awareness_score is not None and awareness_score < float(min_awareness_score):
        reasons.append("low_score_self_awareness")
        reason_labels.append(
            f"评分自感知过低（{awareness_score:.1f} < {float(min_awareness_score):.1f}）"
        )
    if evidence_hits < int(min_evidence_hits):
        reasons.append("missing_evidence_hits")
        reason_labels.append(f"证据命中不足（{evidence_hits} < {int(min_evidence_hits)}）")
    blocked = bool(reasons)
    warning_message = None
    if blocked and reason_labels:
        warning_message = (
            "当前真实评分样本未纳入自动学习："
            + "；".join(reason_labels)
            + "。建议先补齐资料、重评分或修复证据链后再学习。"
        )
    return {
        "blocked": blocked,
        "status": "blocked" if blocked else "accepted",
        "reasons": reasons,
        "score_self_awareness_score": (
            round(float(awareness_score), 2) if awareness_score is not None else None
        ),
        "score_self_awareness_level": awareness_level or None,
        "evidence_hits": evidence_hits,
        "material_gate_blocked": material_gate_blocked,
        "total_parsed_chars": total_parsed_chars,
        "min_awareness_score": round(float(min_awareness_score), 2),
        "min_evidence_hits": int(min_evidence_hits),
        "warning_message": warning_message,
    }
