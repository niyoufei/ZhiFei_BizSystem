from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence


def _dimension_name(dimensions: Dict[str, Dict[str, object]], dimension_id: str) -> str:
    return str((dimensions.get(dimension_id) or {}).get("name") or dimension_id)


@dataclass(frozen=True)
class SubmissionEvidenceTraceContext:
    dimensions: Dict[str, Dict[str, object]]
    build_evidence_trace_summary: Callable[[Dict[str, object]], Dict[str, object]]
    load_evidence_units: Callable[[], Sequence[Dict[str, object]]]
    build_submission_material_conflicts: Callable[..., Dict[str, object]]
    to_float_or_none: Callable[[Any], Optional[float]]
    now_iso: Callable[[], str]


@dataclass(frozen=True)
class SubmissionScoringBasisContext:
    ensure_report_material_usage_metadata: Callable[[Dict[str, object]], None]
    build_material_quality_snapshot: Callable[[str], Dict[str, object]]
    normalize_material_retrieval_meta: Callable[[object], Dict[str, object]]
    build_evidence_trace_summary: Callable[[Dict[str, object]], Dict[str, object]]
    build_current_runtime_constraint_snapshot: Callable[..., Dict[str, object]]
    to_float_or_none: Callable[[Any], Optional[float]]
    now_iso: Callable[[], str]


def build_submission_evidence_trace_report(
    *,
    project_id: str,
    submission: Dict[str, object],
    context: SubmissionEvidenceTraceContext,
) -> Dict[str, object]:
    submission_id = str(submission.get("id") or "")
    filename = str(submission.get("filename") or "")
    report = submission.get("report") if isinstance(submission.get("report"), dict) else {}
    report_meta = report.get("meta") if isinstance(report.get("meta"), dict) else {}
    summary = (
        report_meta.get("evidence_trace")
        if isinstance(report_meta.get("evidence_trace"), dict)
        else context.build_evidence_trace_summary(report)
    )
    requirement_hits = (
        report.get("requirement_hits") if isinstance(report.get("requirement_hits"), list) else []
    )

    by_dimension_map: Dict[str, Dict[str, object]] = {}
    requirement_rows: List[Dict[str, object]] = []
    for item in requirement_hits:
        if not isinstance(item, dict):
            continue
        dimension_id = str(item.get("dimension_id") or "")
        if dimension_id:
            bucket = by_dimension_map.setdefault(
                dimension_id,
                {
                    "dimension_id": dimension_id,
                    "dimension_name": _dimension_name(context.dimensions, dimension_id),
                    "total": 0,
                    "hit": 0,
                    "mandatory_total": 0,
                    "mandatory_hit": 0,
                },
            )
            bucket["total"] = int(bucket.get("total", 0)) + 1
            if bool(item.get("hit")):
                bucket["hit"] = int(bucket.get("hit", 0)) + 1
            if bool(item.get("mandatory")):
                bucket["mandatory_total"] = int(bucket.get("mandatory_total", 0)) + 1
                if bool(item.get("hit")):
                    bucket["mandatory_hit"] = int(bucket.get("mandatory_hit", 0)) + 1

        requirement_rows.append(
            {
                "dimension_id": dimension_id,
                "dimension_name": _dimension_name(context.dimensions, dimension_id),
                "label": str(item.get("label") or ""),
                "hit": bool(item.get("hit")),
                "mandatory": bool(item.get("mandatory")),
                "reason": str(item.get("reason") or ""),
                "source_pack_id": str(item.get("source_pack_id") or ""),
                "material_type": str(item.get("material_type") or ""),
                "source_filename": str(item.get("source_filename") or ""),
                "chunk_id": str(item.get("chunk_id") or ""),
                "source_mode": str(item.get("source_mode") or ""),
            }
        )

    by_dimension_rows: List[Dict[str, object]] = []
    for dimension_id, row in by_dimension_map.items():
        total = int(row.get("total", 0))
        hit = int(row.get("hit", 0))
        mandatory_total = int(row.get("mandatory_total", 0))
        mandatory_hit = int(row.get("mandatory_hit", 0))
        by_dimension_rows.append(
            {
                **row,
                "hit_rate": round(float(hit) / float(total), 4) if total > 0 else None,
                "mandatory_hit_rate": round(float(mandatory_hit) / float(mandatory_total), 4)
                if mandatory_total > 0
                else None,
            }
        )
    by_dimension_rows.sort(key=lambda item: str(item.get("dimension_id") or ""))

    evidence_units_rows: List[Dict[str, object]] = []
    for unit in context.load_evidence_units():
        if str(unit.get("submission_id") or "") != submission_id:
            continue
        unit_dimension_id = str(unit.get("dimension_id") or "")
        evidence_units_rows.append(
            {
                "id": str(unit.get("id") or ""),
                "dimension_id": unit_dimension_id,
                "dimension_name": _dimension_name(context.dimensions, unit_dimension_id),
                "source_locator": str(
                    unit.get("source_locator")
                    or unit.get("locator")
                    or unit.get("anchor_locator")
                    or ""
                ),
                "source_filename": str(unit.get("source_filename") or ""),
                "confidence": context.to_float_or_none(unit.get("confidence")),
                "text_snippet": str(
                    unit.get("text_snippet") or unit.get("text") or unit.get("unit_text") or ""
                )[:220],
            }
        )
    evidence_units_rows.sort(
        key=lambda item: float(context.to_float_or_none(item.get("confidence")) or 0.0),
        reverse=True,
    )

    material_conflicts = context.build_submission_material_conflicts(
        project_id=project_id,
        submission=submission,
    )
    recommendations: List[str] = []
    total_hits = int(context.to_float_or_none(summary.get("total_hits")) or 0)
    total_requirements = int(context.to_float_or_none(summary.get("total_requirements")) or 0)
    if total_requirements > 0 and total_hits <= 0:
        recommendations.append("当前评分未命中有效证据锚点，建议补充与资料一致的可检索表述。")
    if bool(material_conflicts.get("has_conflicts")):
        recommendations.extend(
            [
                str(item)
                for item in (material_conflicts.get("recommendations") or [])
                if str(item).strip()
            ]
        )

    return {
        "project_id": project_id,
        "submission_id": submission_id,
        "filename": filename,
        "generated_at": context.now_iso(),
        "summary": summary,
        "by_dimension": by_dimension_rows,
        "requirement_hits": requirement_rows[:180],
        "evidence_units": evidence_units_rows[:120],
        "material_conflicts": material_conflicts,
        "recommendations": recommendations[:16],
    }


def build_submission_scoring_basis_report(
    *,
    project_id: str,
    submission: Dict[str, object],
    context: SubmissionScoringBasisContext,
) -> Dict[str, object]:
    submission_id = str(submission.get("id") or "")
    filename = str(submission.get("filename") or "")
    report = submission.get("report") if isinstance(submission.get("report"), dict) else {}
    context.ensure_report_material_usage_metadata(report)
    meta = report.get("meta") if isinstance(report.get("meta"), dict) else {}
    input_injection = (
        meta.get("input_injection") if isinstance(meta.get("input_injection"), dict) else {}
    )
    material_quality = (
        meta.get("material_quality") if isinstance(meta.get("material_quality"), dict) else {}
    )
    if not material_quality:
        material_quality = context.build_material_quality_snapshot(project_id)
    material_retrieval = context.normalize_material_retrieval_meta(meta.get("material_retrieval"))
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
    material_constraint_shaping = (
        meta.get("material_constraint_shaping")
        if isinstance(meta.get("material_constraint_shaping"), dict)
        else {}
    )
    if not evidence_trace:
        evidence_trace = context.build_evidence_trace_summary(report)
    current_runtime_constraints = context.build_current_runtime_constraint_snapshot(
        project_id,
        submission_text=str(submission.get("text") or ""),
    )

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
    if (context.to_float_or_none(evidence_trace.get("total_requirements")) or 0) > 0 and (
        context.to_float_or_none(evidence_trace.get("total_hits")) or 0
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
        "generated_at": context.now_iso(),
        "scoring_status": str(report.get("scoring_status") or "unknown"),
        "mece_inputs": mece_inputs,
        "material_quality": material_quality,
        "material_retrieval": material_retrieval,
        "material_utilization": material_utilization,
        "material_utilization_gate": material_utilization_gate,
        "evidence_trace": evidence_trace,
        "material_constraint_shaping": material_constraint_shaping,
        "current_runtime_constraints": current_runtime_constraints,
        "recommendations": deduped_recommendations[:16],
    }
