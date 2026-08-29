from __future__ import annotations

from typing import Callable, Dict, List, Optional

MaterialQualityBuilder = Callable[[str], Dict[str, object]]
EvidenceTraceBuilder = Callable[[Dict[str, object]], Dict[str, object]]
FloatCoercer = Callable[[object], Optional[float]]
TimestampFactory = Callable[[], str]


def build_scoring_basis_projection(
    *,
    project_id: str,
    submission: Dict[str, object],
    build_material_quality_snapshot: MaterialQualityBuilder,
    build_evidence_trace_summary: EvidenceTraceBuilder,
    to_float_or_none: FloatCoercer,
    now_iso: TimestampFactory,
) -> Dict[str, object]:
    """构建评分依据审计：展示评分时注入的输入与资料命中链路。"""
    submission_id = str(submission.get("id") or "")
    filename = str(submission.get("filename") or "")
    report = submission.get("report") if isinstance(submission.get("report"), dict) else {}
    meta = report.get("meta") if isinstance(report.get("meta"), dict) else {}
    input_injection = (
        meta.get("input_injection") if isinstance(meta.get("input_injection"), dict) else {}
    )
    material_quality = (
        meta.get("material_quality") if isinstance(meta.get("material_quality"), dict) else {}
    )
    if not material_quality:
        material_quality = build_material_quality_snapshot(project_id)
    material_retrieval = (
        meta.get("material_retrieval") if isinstance(meta.get("material_retrieval"), dict) else {}
    )
    material_utilization = (
        meta.get("material_utilization")
        if isinstance(meta.get("material_utilization"), dict)
        else {}
    )
    material_utilization_gate = (
        meta.get("material_utilization_gate")
        if isinstance(meta.get("material_utilization_gate"), dict)
        else {}
    )
    evidence_trace = (
        meta.get("evidence_trace") if isinstance(meta.get("evidence_trace"), dict) else {}
    )
    if not evidence_trace:
        evidence_trace = build_evidence_trace_summary(report)

    recommendations: List[str] = []
    mece_inputs = (
        input_injection.get("mece_inputs")
        if isinstance(input_injection.get("mece_inputs"), dict)
        else {}
    )
    if mece_inputs and not bool(mece_inputs.get("materials_quality_gate_passed", True)):
        recommendations.append("资料门禁未通过：建议先完成“3) 项目资料”整改后再评分。")
    if material_utilization_gate:
        for reason in material_utilization_gate.get("reasons") or []:
            reason_text = str(reason).strip()
            if reason_text:
                recommendations.append(reason_text)
    if (to_float_or_none(evidence_trace.get("total_requirements")) or 0) > 0 and (
        to_float_or_none(evidence_trace.get("total_hits")) or 0
    ) <= 0:
        recommendations.append("评分未命中任何资料证据：请补充与清单/图纸/答疑一致的量化约束。")

    deduped_recommendations: List[str] = []
    for item in recommendations:
        text = str(item or "").strip()
        if text and text not in deduped_recommendations:
            deduped_recommendations.append(text)

    return {
        "project_id": project_id,
        "submission_id": submission_id,
        "filename": filename,
        "generated_at": now_iso(),
        "scoring_status": str(report.get("scoring_status") or "unknown"),
        "mece_inputs": mece_inputs,
        "material_quality": material_quality,
        "material_retrieval": material_retrieval,
        "material_utilization": material_utilization,
        "material_utilization_gate": material_utilization_gate,
        "evidence_trace": evidence_trace,
        "recommendations": deduped_recommendations[:16],
    }
