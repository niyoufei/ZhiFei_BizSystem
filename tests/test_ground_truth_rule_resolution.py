from __future__ import annotations

from app.domain.learning.ground_truth_rule_resolution import (
    default_ground_truth_score_rule,
    resolve_project_ground_truth_score_rule,
)


def test_default_ground_truth_score_rule_uses_project_score_scale() -> None:
    rule = default_ground_truth_score_rule(
        project_id="p1",
        project={"id": "p1", "meta": {"score_scale_max": 5}},
    )

    assert rule["project_id"] == "p1"
    assert rule["score_scale_max"] == 5
    assert rule["score_scale_label"] == "5分制"
    assert rule["formula"] == "manual"
    assert rule["auto_compute"] is False


def test_resolve_project_ground_truth_score_rule_prefers_project_override() -> None:
    project = {
        "id": "p1",
        "meta": {"score_scale_max": 5, "ground_truth_final_score_formula": "simple_mean"},
    }

    rule = resolve_project_ground_truth_score_rule(
        "p1",
        project=project,
        materials=[],
        extract_rule_from_text=lambda text, filename: None,
        extract_rule_from_material=lambda material: None,
        extract_scale_from_material=lambda material: None,
    )

    assert rule["formula"] == "simple_mean"
    assert rule["auto_compute"] is True
    assert rule["score_scale_max"] == 5
    assert rule["source_filename"] == "project.meta.ground_truth_final_score_formula"


def test_resolve_project_ground_truth_score_rule_applies_scale_candidate_when_rule_missing() -> (
    None
):
    project = {"id": "p1", "meta": {"score_scale_max": 100}}
    materials = [
        {
            "project_id": "p1",
            "material_type": "tender_qa",
            "filename": "招标文件.pdf",
            "parsed_text": "正文",
        }
    ]

    rule = resolve_project_ground_truth_score_rule(
        "p1",
        project=project,
        materials=materials,
        extract_rule_from_text=lambda text, filename: None,
        extract_rule_from_material=lambda material: None,
        extract_scale_from_material=lambda material: {
            "score_scale_max": 5,
            "score_scale_confidence": 88,
            "score_scale_source_filename": material["filename"],
        },
    )

    assert rule["formula"] == "manual"
    assert rule["score_scale_max"] == 5
    assert rule["score_scale_label"] == "5分制"
    assert rule["score_scale_detected"] is True
