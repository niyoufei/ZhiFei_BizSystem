from __future__ import annotations

from app.scoring_diagnostics import (
    LatestSubmissionContext,
    MaterialCardContext,
    build_dimension_support_cards,
    build_material_type_cards,
    build_project_scoring_summary,
    collect_project_scoring_recommendations,
    prepare_latest_submission_context,
)


def _normalize_material_type(value: object, filename: object = "") -> str:
    text = str(value or "").strip().lower()
    if text:
        return text
    name = str(filename or "").lower()
    if name.endswith(".dxf"):
        return "drawing"
    return ""


def _normalize_numeric_token(token: object) -> str:
    return str(token or "").strip()


def _classify_numeric_anchor_category(
    *,
    terms,
    material_type,
    dimension_id,
    label,
):
    joined = " ".join(str(item or "") for item in (terms or []))
    if "工期" in joined or str(dimension_id or "") == "09":
        return "工期/节点"
    if "规格" in joined:
        return "规格/参数"
    return "阈值/偏差"


def _append_numeric_anchor_bucket(bucket, category, token):
    if not category or not token:
        return
    values = bucket.setdefault(category, [])
    if token not in values:
        values.append(token)


def _build_numeric_anchor_category_summary(bucket):
    return [f"{key}：{'、'.join(values)}" for key, values in sorted(bucket.items()) if values]


def _material_type_label(material_type):
    labels = {
        "tender_qa": "招标文件和答疑",
        "boq": "工程量清单",
        "drawing": "图纸",
        "site_photo": "现场照片",
    }
    return labels.get(str(material_type or ""), str(material_type or ""))


def _to_float_or_none(value):
    if value is None or value == "":
        return None
    return float(value)


def test_prepare_latest_submission_context_enriches_and_builds_reports():
    calls = []

    def ensure_material_usage(report):
        calls.append(("usage", report.get("scoring_status")))

    def ensure_self_awareness(report, *, project_id, material_knowledge_snapshot):
        calls.append(("awareness", project_id, material_knowledge_snapshot.get("summary", {})))

    def build_evidence_trace_report(*, project_id, submission):
        calls.append(("trace", project_id, submission.get("id")))
        return {"summary": {"total_hits": 5}}

    def build_scoring_basis_report(*, project_id, submission):
        calls.append(("basis", project_id, submission.get("id")))
        return {"material_utilization": {"retrieval_hit_rate": 0.8}}

    latest_submission, evidence_trace, scoring_basis = prepare_latest_submission_context(
        project_id="p1",
        latest={
            "id": "s1",
            "filename": "施工组织设计.pdf",
            "created_at": "2026-03-13T10:00:00+08:00",
            "report": {
                "scoring_status": "scored",
                "meta": {
                    "score_self_awareness": {"level": "high"},
                    "score_confidence_level": "high",
                },
            },
        },
        material_knowledge={"summary": {"dimension_coverage_rate": 0.75}},
        context=LatestSubmissionContext(
            ensure_report_material_usage_metadata=ensure_material_usage,
            ensure_report_score_self_awareness=ensure_self_awareness,
            build_submission_evidence_trace_report=build_evidence_trace_report,
            build_submission_scoring_basis_report=build_scoring_basis_report,
        ),
    )

    assert latest_submission["exists"] is True
    assert latest_submission["submission_id"] == "s1"
    assert latest_submission["is_scored"] is True
    assert latest_submission["score_self_awareness"] == {"level": "high"}
    assert latest_submission["score_confidence_level"] == "high"
    assert evidence_trace == {"summary": {"total_hits": 5}}
    assert scoring_basis == {"material_utilization": {"retrieval_hit_rate": 0.8}}
    assert calls == [
        ("usage", "scored"),
        ("awareness", "p1", {"dimension_coverage_rate": 0.75}),
        ("trace", "p1", "s1"),
        ("basis", "p1", "s1"),
    ]


def test_build_dimension_support_cards_sorts_by_coverage_score():
    cards = build_dimension_support_cards(
        [
            {
                "dimension_id": "07",
                "dimension_name": "重难点及危险性较大工程管控",
                "coverage_score": 0.2,
                "coverage_level": "low",
                "keyword_hits": 1,
                "numeric_signal_hits": 0,
                "source_types": [],
            },
            {
                "dimension_id": "01",
                "dimension_name": "工程项目整体理解与实施路径",
                "coverage_score": 0.8,
                "coverage_level": "high",
                "keyword_hits": 5,
                "numeric_signal_hits": 2,
                "source_types": ["tender_qa", "drawing"],
            },
        ],
        to_float_or_none=_to_float_or_none,
        normalize_material_type=_normalize_material_type,
    )

    assert [item["dimension_id"] for item in cards] == ["01", "07"]
    assert cards[0]["source_types"] == ["tender_qa", "drawing"]


def test_build_material_type_cards_summarizes_material_signals_and_missing_required_types():
    cards = build_material_type_cards(
        material_rows=[
            {
                "material_type": "tender_qa",
                "filename": "招标文件.pdf",
                "parse_status": "parsed",
                "parse_backend": "gpt-5.4",
                "parse_confidence": 0.9,
            },
            {
                "material_type": "tender_qa",
                "filename": "答疑纪要.docx",
                "parse_status": "failed",
                "parse_error_message": "ocr fail",
            },
        ],
        material_depth={
            "depth_gate": {"enforce": True},
            "by_type": [
                {
                    "material_type": "tender_qa",
                    "files": 2,
                    "parsed_chars": 12000,
                    "parsed_chunks": 12,
                    "numeric_terms": 4,
                    "meets_chars": True,
                    "meets_chunks": True,
                    "meets_numeric_terms": True,
                }
            ],
        },
        material_knowledge={
            "by_type": [
                {
                    "material_type": "tender_qa",
                    "top_numeric_terms": ["90", "48"],
                    "top_terms": ["工期", "偏差"],
                    "top_dimensions": [{"dimension_id": "09"}],
                    "structured_quality_score": 0.82,
                    "structured_quality_max": 0.91,
                    "structured_quality_signal_coverage": 0.77,
                }
            ]
        },
        readiness={"material_gate": {"required_types": ["tender_qa", "boq"]}},
        basis_util={
            "available_types": ["tender_qa"],
            "by_type": {
                "tender_qa": {
                    "retrieval_total": 3,
                    "retrieval_hit": 2,
                    "consistency_total": 2,
                    "consistency_hit": 1,
                    "fallback_total": 0,
                    "fallback_hit": 0,
                }
            },
        },
        basis_retrieval={
            "preview": [
                {
                    "material_type": "tender_qa",
                    "matched_terms": ["工期"],
                    "dimension_id": "09",
                    "filename": "招标文件.pdf",
                    "matched_numeric_terms": ["90"],
                }
            ],
            "consistency_preview": [
                {
                    "material_type": "tender_qa",
                    "terms": ["工期", "偏差"],
                    "dimension_id": "09",
                    "label": "工期节点",
                    "numbers": ["90", "48"],
                }
            ],
        },
        conflict_summary={"conflicts": [{"material_type": "tender_qa", "label": "危大工程清单"}]},
        requirement_hits=[
            {
                "material_type": "tender_qa",
                "label": "工期节点",
                "hit": True,
                "source_filename": "招标文件.pdf",
                "source_mode": "retrieval",
                "reason": "keywords",
            },
            {
                "material_type": "tender_qa",
                "label": "危大工程清单",
                "hit": False,
                "source_filename": "招标文件.pdf",
                "source_mode": "material_consistency",
                "reason": "missing",
            },
        ],
        context=MaterialCardContext(
            normalize_material_type=_normalize_material_type,
            normalize_numeric_token=_normalize_numeric_token,
            classify_numeric_anchor_category=_classify_numeric_anchor_category,
            append_numeric_anchor_bucket=_append_numeric_anchor_bucket,
            build_numeric_anchor_category_summary=_build_numeric_anchor_category_summary,
            material_type_label=_material_type_label,
            to_float_or_none=_to_float_or_none,
        ),
    )

    tender = next(item for item in cards if item["material_type"] == "tender_qa")
    boq = next(item for item in cards if item["material_type"] == "boq")

    assert tender["status"] == "active"
    assert tender["parse_status_counts"] == {"parsed": 1, "failed": 1}
    assert tender["parse_backend_summary"] == ["GPT-5.4×1"]
    assert tender["parse_confidence_avg"] == 0.9
    assert tender["hit_requirement_labels"] == ["工期节点"]
    assert tender["miss_requirement_labels"] == ["危大工程清单"]
    assert tender["project_numeric_terms"] == ["90", "48"]
    assert tender["missing_numeric_terms"] == ["48"]
    assert tender["conflict_labels"] == ["危大工程清单"]
    assert tender["guidance"] == ["招标文件和答疑已进入评分证据链。"]

    assert boq["required"] is True
    assert boq["status"] == "missing"
    assert "缺少工程量清单" in boq["guidance"][0]


def test_build_material_type_cards_marks_parsed_materials_waiting_for_score():
    cards = build_material_type_cards(
        material_rows=[
            {
                "material_type": "drawing",
                "filename": "总图.dxf",
                "parse_status": "parsed",
                "parse_backend": "local",
                "parse_confidence": 0.86,
            }
        ],
        material_depth={
            "depth_gate": {"enforce": True},
            "by_type": [
                {
                    "material_type": "drawing",
                    "files": 1,
                    "parsed_chars": 3200,
                    "parsed_chunks": 4,
                    "numeric_terms": 3,
                    "meets_chars": True,
                    "meets_chunks": True,
                    "meets_numeric_terms": False,
                }
            ],
        },
        material_knowledge={
            "by_type": [
                {
                    "material_type": "drawing",
                    "top_numeric_terms": ["10", "11"],
                    "top_terms": ["节点", "偏差"],
                    "top_dimensions": [{"dimension_id": "14"}],
                    "structured_quality_score": 0.55,
                    "structured_quality_max": 0.72,
                    "structured_quality_signal_coverage": 0.61,
                }
            ]
        },
        readiness={"material_gate": {"required_types": ["drawing"]}},
        latest_submission={
            "exists": True,
            "is_scored": False,
            "scoring_status": "pending",
        },
        basis_util={
            "available_types": ["drawing"],
            "by_type": {
                "drawing": {
                    "retrieval_total": 0,
                    "retrieval_hit": 0,
                    "consistency_total": 0,
                    "consistency_hit": 0,
                    "fallback_total": 0,
                    "fallback_hit": 0,
                }
            },
        },
        basis_retrieval={"preview": [], "consistency_preview": []},
        conflict_summary={"conflicts": []},
        requirement_hits=[],
        context=MaterialCardContext(
            normalize_material_type=_normalize_material_type,
            normalize_numeric_token=_normalize_numeric_token,
            classify_numeric_anchor_category=_classify_numeric_anchor_category,
            append_numeric_anchor_bucket=_append_numeric_anchor_bucket,
            build_numeric_anchor_category_summary=_build_numeric_anchor_category_summary,
            material_type_label=_material_type_label,
            to_float_or_none=_to_float_or_none,
        ),
    )

    drawing = next(item for item in cards if item["material_type"] == "drawing")

    assert drawing["status"] == "parsed_ready"
    assert drawing["status_label"] == "已解析待评分"
    assert drawing["guidance"][0] == "图纸已解析，待完成施组评分后自动进入证据链。"


def test_build_material_type_cards_marks_parsed_materials_waiting_for_evidence():
    cards = build_material_type_cards(
        material_rows=[
            {
                "material_type": "drawing",
                "filename": "总图.dxf",
                "parse_status": "parsed",
                "parse_backend": "local",
                "parse_confidence": 0.86,
            }
        ],
        material_depth={
            "depth_gate": {"enforce": True},
            "by_type": [
                {
                    "material_type": "drawing",
                    "files": 1,
                    "parsed_chars": 3200,
                    "parsed_chunks": 4,
                    "numeric_terms": 3,
                    "meets_chars": True,
                    "meets_chunks": True,
                    "meets_numeric_terms": False,
                }
            ],
        },
        material_knowledge={
            "by_type": [
                {
                    "material_type": "drawing",
                    "top_numeric_terms": ["10", "11"],
                    "top_terms": ["节点", "偏差"],
                    "top_dimensions": [{"dimension_id": "14"}],
                    "structured_quality_score": 0.55,
                    "structured_quality_max": 0.72,
                    "structured_quality_signal_coverage": 0.61,
                }
            ]
        },
        readiness={"material_gate": {"required_types": ["drawing"]}},
        latest_submission={
            "exists": True,
            "is_scored": True,
            "scoring_status": "scored",
        },
        basis_util={
            "available_types": ["drawing"],
            "by_type": {
                "drawing": {
                    "retrieval_total": 0,
                    "retrieval_hit": 0,
                    "consistency_total": 0,
                    "consistency_hit": 0,
                    "fallback_total": 0,
                    "fallback_hit": 0,
                }
            },
        },
        basis_retrieval={"preview": [], "consistency_preview": []},
        conflict_summary={"conflicts": []},
        requirement_hits=[],
        context=MaterialCardContext(
            normalize_material_type=_normalize_material_type,
            normalize_numeric_token=_normalize_numeric_token,
            classify_numeric_anchor_category=_classify_numeric_anchor_category,
            append_numeric_anchor_bucket=_append_numeric_anchor_bucket,
            build_numeric_anchor_category_summary=_build_numeric_anchor_category_summary,
            material_type_label=_material_type_label,
            to_float_or_none=_to_float_or_none,
        ),
    )

    drawing = next(item for item in cards if item["material_type"] == "drawing")

    assert drawing["status"] == "parsed_not_used"
    assert drawing["status_label"] == "已解析待补证据"
    assert drawing["guidance"][0] == "图纸已解析，但当前施组评分尚未命中到有效证据。"


def test_build_project_scoring_summary_and_recommendations():
    summary = build_project_scoring_summary(
        readiness={"ready": True, "gate_passed": True},
        parse_job_summary={"total_jobs": 3, "backlog": 1, "failed_jobs": 0, "gpt_ratio": 1.0},
        material_rows=[
            {"parse_status": "parsed"},
            {"parse_status": "queued"},
            {"parse_status": "processing"},
        ],
        quality_summary={"total_files": 3, "total_parsed_chars": 18000, "total_parsed_chunks": 24},
        latest_submission={
            "exists": True,
            "is_scored": True,
            "score_self_awareness": {"level": "medium"},
            "score_confidence_level": "medium",
        },
        trace_summary={"total_requirements": 10, "total_hits": 7, "mandatory_hit_rate": 0.8},
        basis_util={
            "retrieval_hit_rate": 0.7,
            "material_profile_focus_dimensions": ["09", "14"],
        },
        basis_gate={"blocked": False},
        conflict_summary={"conflict_count": 1, "high_severity_count": 0},
        knowledge_summary={
            "dimension_coverage_rate": 0.62,
            "structured_signal_total": 12,
            "structured_quality_avg": 0.66,
            "structured_quality_max": 0.88,
            "structured_quality_type_rate": 0.75,
            "strong_structured_types": 2,
            "low_coverage_dimensions": 5,
            "covered_dimensions": 9,
            "numeric_category_summary": ["工期/节点：90"],
        },
        basis_runtime_constraints={
            "weights_source": "evolution",
            "effective_multipliers_preview": [{"dimension_id": "09", "multiplier": 1.16}],
            "feedback_evolution_requirements": 1,
            "feature_confidence_requirements": 0,
        },
        to_float_or_none=_to_float_or_none,
    )
    recommendations = collect_project_scoring_recommendations(
        ["补充图纸章节"],
        ["补充图纸章节", "校准进度阈值"],
        latest_submission_exists=False,
    )

    assert summary["ready_to_score"] is True
    assert summary["parse_total_jobs"] == 3
    assert summary["queued_materials"] == 1
    assert summary["processing_materials"] == 1
    assert summary["current_weights_source"] == "evolution"
    assert summary["recent_feedback_context_active"] is True
    assert summary["latest_score_self_awareness"] == {"level": "medium"}
    assert recommendations == [
        "暂无施组评分证据链，请先上传并评分至少 1 份施组。",
        "补充图纸章节",
        "校准进度阈值",
    ]
