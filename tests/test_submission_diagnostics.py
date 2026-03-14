from __future__ import annotations

from app.submission_diagnostics import (
    SubmissionEvidenceTraceContext,
    SubmissionScoringBasisContext,
    build_submission_evidence_trace_report,
    build_submission_scoring_basis_report,
)


def _to_float_or_none(value):
    if value is None or value == "":
        return None
    return float(value)


def test_build_submission_evidence_trace_report_collects_dimension_rows_and_recommendations():
    payload = build_submission_evidence_trace_report(
        project_id="p1",
        submission={
            "id": "s1",
            "filename": "施工组织设计.pdf",
            "report": {
                "requirement_hits": [
                    {
                        "dimension_id": "01",
                        "label": "总体部署",
                        "hit": False,
                        "mandatory": True,
                        "reason": "missing",
                        "source_pack_id": "runtime_material_rag",
                        "material_type": "tender_qa",
                        "source_filename": "招标文件.pdf",
                        "chunk_id": "招标文件.pdf#c1",
                        "source_mode": "retrieval",
                    }
                ]
            },
        },
        context=SubmissionEvidenceTraceContext(
            dimensions={"01": {"name": "总体部署与信息化管理"}},
            build_evidence_trace_summary=lambda report: {
                "total_requirements": 1,
                "total_hits": 0,
            },
            load_evidence_units=lambda: [
                {
                    "id": "eu-1",
                    "submission_id": "s1",
                    "dimension_id": "01",
                    "source_locator": "P1",
                    "source_filename": "招标文件.pdf",
                    "confidence": 0.88,
                    "text_snippet": "总体部署...",
                },
                {
                    "id": "eu-2",
                    "submission_id": "other",
                    "dimension_id": "01",
                    "source_locator": "P2",
                    "source_filename": "other.pdf",
                    "confidence": 0.99,
                    "text_snippet": "other",
                },
            ],
            build_submission_material_conflicts=lambda **kwargs: {
                "has_conflicts": True,
                "recommendations": ["补齐工期约束"],
            },
            to_float_or_none=_to_float_or_none,
            now_iso=lambda: "2026-03-14T00:00:00+08:00",
        ),
    )

    assert payload["project_id"] == "p1"
    assert payload["submission_id"] == "s1"
    assert payload["summary"]["total_requirements"] == 1
    assert payload["by_dimension"][0]["dimension_name"] == "总体部署与信息化管理"
    assert payload["by_dimension"][0]["mandatory_hit_rate"] == 0.0
    assert len(payload["evidence_units"]) == 1
    assert payload["evidence_units"][0]["id"] == "eu-1"
    assert payload["recommendations"] == [
        "当前评分未命中有效证据锚点，建议补充与资料一致的可检索表述。",
        "补齐工期约束",
    ]


def test_build_submission_scoring_basis_report_uses_fallbacks_and_dedupes_recommendations():
    calls = []

    def ensure_report_material_usage_metadata(report):
        calls.append(report)

    payload = build_submission_scoring_basis_report(
        project_id="p1",
        submission={
            "id": "s1",
            "filename": "施工组织设计.pdf",
            "text": "关键工期 90 天",
            "report": {
                "scoring_status": "scored",
                "meta": {
                    "input_injection": {"mece_inputs": {"materials_quality_gate_passed": False}},
                    "material_utilization_gate": {
                        "reasons": ["资料解析覆盖率不足", "资料解析覆盖率不足"]
                    },
                    "material_constraint_shaping": {"applied": True},
                },
            },
        },
        context=SubmissionScoringBasisContext(
            ensure_report_material_usage_metadata=ensure_report_material_usage_metadata,
            build_material_quality_snapshot=lambda project_id: {"total_files": 2},
            normalize_material_retrieval_meta=lambda value: {"chunks": 10},
            build_evidence_trace_summary=lambda report: {
                "total_requirements": 2,
                "total_hits": 0,
            },
            build_current_runtime_constraint_snapshot=lambda project_id, submission_text: {
                "weights_source": "evolution",
                "submission_length": len(submission_text),
            },
            to_float_or_none=_to_float_or_none,
            now_iso=lambda: "2026-03-14T00:00:00+08:00",
        ),
    )

    assert len(calls) == 1
    assert payload["project_id"] == "p1"
    assert payload["submission_id"] == "s1"
    assert payload["material_quality"] == {"total_files": 2}
    assert payload["material_retrieval"] == {"chunks": 10}
    assert payload["evidence_trace"]["total_hits"] == 0
    assert payload["current_runtime_constraints"]["weights_source"] == "evolution"
    assert payload["recommendations"] == [
        "资料门禁未通过：建议先完成“3) 项目资料”整改后再评分。",
        "资料解析覆盖率不足",
        "评分未命中任何资料证据：请补充与清单/图纸/答疑一致的量化约束。",
    ]
