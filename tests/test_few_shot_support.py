from __future__ import annotations

from types import SimpleNamespace

from app.domain.learning.few_shot_support import (
    build_ground_truth_project_by_record_id,
    collect_dimension_evidence_texts,
    collect_dimension_guidance_texts,
    extract_feature_project_ids,
    flatten_ground_truth_qualitative_tags,
    normalize_dimension_id,
    resolve_distillation_feature_ids_for_record,
    select_ground_truth_few_shot_dimensions,
)


def test_normalize_dimension_id_accepts_prefixed_and_plain_values() -> None:
    assert normalize_dimension_id("P09") == "09"
    assert normalize_dimension_id("09") == "09"
    assert normalize_dimension_id("unknown") == ""


def test_flatten_ground_truth_qualitative_tags_dedupes_and_limits() -> None:
    out = flatten_ground_truth_qualitative_tags(
        {
            "qualitative_tags_by_judge": [
                ["进度清晰", "节点明确"],
                ["节点明确", "资源组织合理"],
            ]
        },
        limit=2,
    )

    assert out == ["进度清晰", "节点明确"]


def test_collect_dimension_evidence_and_guidance_texts_extracts_readable_rows() -> None:
    report = {
        "dimension_scores": {
            "09": {
                "evidence": [
                    {"anchor_label": "进度计划网", "quote": "关键线路实行周纠偏。"},
                    {"anchor": "备用锚点", "snippet": "节点验收闭环。"},
                ]
            }
        },
        "suggestions": [
            {"dimension_id": "P09", "text": "补强关键节点纠偏闭环。"},
            {"dimension_id": "10", "text": "不应命中。"},
        ],
    }

    evidence = collect_dimension_evidence_texts(report, dimension_id="09")
    guidance = collect_dimension_guidance_texts(report, dimension_id="09")

    assert evidence == ["进度计划网：关键线路实行周纠偏。", "备用锚点：节点验收闭环。"]
    assert guidance == ["补强关键节点纠偏闭环。"]


def test_select_ground_truth_few_shot_dimensions_prefers_applied_dimensions_then_evidence_density() -> (
    None
):
    out = select_ground_truth_few_shot_dimensions(
        report={
            "dimension_scores": {
                "09": {"score": 9.5, "evidence": [{"quote": "a"}, {"quote": "b"}]},
                "10": {"score": 8.6, "evidence": [{"quote": "c"}]},
            }
        },
        feature_confidence_update={"applied_dimension_ids": ["P14"]},
        max_dimensions=3,
    )

    assert out == ["14", "09", "10"]


def test_build_ground_truth_project_by_record_id_ignores_incomplete_rows() -> None:
    mapping = build_ground_truth_project_by_record_id(
        [
            {"id": "gt-1", "project_id": "p1"},
            {"id": "", "project_id": "p2"},
            {"id": "gt-3"},
        ]
    )

    assert mapping == {"gt-1": "p1"}


def test_extract_feature_project_ids_falls_back_to_source_record_ids() -> None:
    feature = SimpleNamespace(source_project_ids=[], source_record_ids=["gt-1", "gt-2"])

    project_ids = extract_feature_project_ids(
        feature,
        ground_truth_project_by_record_id={"gt-1": "p1", "gt-2": "p2"},
    )

    assert project_ids == {"p1", "p2"}


def test_resolve_distillation_feature_ids_for_record_prefers_record_match_and_recovers_stale_ids() -> (
    None
):
    features = [
        SimpleNamespace(
            feature_id="F-real",
            dimension_id="09",
            source_record_ids=["gt-1"],
            source_project_ids=["p1"],
        ),
        SimpleNamespace(
            feature_id="F-other",
            dimension_id="09",
            source_record_ids=["gt-2"],
            source_project_ids=["p1"],
        ),
    ]

    resolved = resolve_distillation_feature_ids_for_record(
        {"id": "gt-1", "project_id": "p1"},
        {
            "feature_ids": ["F-stale"],
            "dimension_ids": ["09"],
        },
        features=features,
        ground_truth_rows=[{"id": "gt-1", "project_id": "p1"}],
    )

    assert resolved == ["F-real"]


def test_resolve_distillation_feature_ids_for_record_rejects_cross_project_explicit_ids() -> None:
    features = [
        SimpleNamespace(
            feature_id="F-p2",
            dimension_id="09",
            source_record_ids=[],
            source_project_ids=["p2"],
        )
    ]

    resolved = resolve_distillation_feature_ids_for_record(
        {"id": "gt-1", "project_id": "p1"},
        {
            "feature_ids": ["F-p2"],
            "dimension_ids": ["09"],
        },
        features=features,
        ground_truth_rows=[{"id": "gt-1", "project_id": "p1"}],
    )

    assert resolved == []
