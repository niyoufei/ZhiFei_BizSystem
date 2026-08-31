from __future__ import annotations

import copy

import pytest

from app import assessment_contract_service


def _project() -> dict[str, object]:
    return {
        "id": "project-1",
        "region": "CN",
        "scoring_engine_version_locked": "v2",
        "calibrator_version_locked": "calib-1",
        "expert_profile_id": "expert-1",
        "meta": {
            "score_blend": {"rule_weight": 0.7, "llm_weight": 0.3},
            "tender_profile_state": {
                "approved": True,
                "profile": {
                    "version": "tender-v1",
                    "scoring_items": [{"item_id": "T01", "max_score": 5}],
                },
                "attention_profile": {
                    "selection_context": {
                        "version": "expert-point-selector-v3",
                        "catalog_summary": {
                            "catalog_version": "construction-expert-catalog-v1"
                        },
                    },
                    "items": [
                        {
                            "item_id": "T01",
                            "evidence": [
                                {
                                    "requirement": "工期保障",
                                    "expert_points": [{"name": "总进度计划"}],
                                }
                            ],
                        }
                    ],
                },
            },
        },
    }


def _build(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "project_id": "project-1",
        "project": _project(),
        "config_rubric": {"dimensions": {"D01": {"weight": 1.0}}},
        "config_lexicon": {"quality": ["质量"]},
        "multipliers": {"D01": 1.2},
        "profile_snapshot": {"id": "expert-1", "weights_norm": {"D01": 1.0}},
        "scoring_engine_version": "v2",
        "engine_version": "v2",
        "deployed_patch": {"id": "patch-1", "patch_payload": {"threshold": 0.8}},
        "calibrator_model": {
            "calibrator_version": "calib-1",
            "model_artifact": {"model_type": "offset", "bias": 1.0},
            "train_filter": {"project_id": "project-1"},
        },
        "resolved_scoring_inputs": {
            "anchors": [{"id": "anchor-1", "created_at": "first"}],
            "runtime_custom_requirements": [
                {"id": "runtime-1", "patterns": {"hints": ["进度"]}, "created_at": "first"}
            ],
        },
        "post_processing": {
            "evolution_total_score_scale": 1.1,
            "evolution_total_score_scale_applied": True,
        },
    }
    values.update(overrides)
    return assessment_contract_service.build_assessment_contract(**values)


def test_contract_hash_is_canonical_and_ignores_generation_timestamps() -> None:
    first = _build()
    second_inputs = {
        "runtime_custom_requirements": [
            {"created_at": "second", "patterns": {"hints": ["进度"]}, "id": "runtime-1"}
        ],
        "anchors": [{"created_at": "second", "id": "anchor-1"}],
    }
    second = _build(resolved_scoring_inputs=second_inputs)

    assert first["contract_hash"] == second["contract_hash"]
    assert assessment_contract_service.verify_assessment_contract(first) is True
    assert "created_at" not in str(first["inputs"])


@pytest.mark.parametrize(
    "mutate",
    [
        lambda values: values.update(project_id="project-2"),
        lambda values: values["project"]["meta"]["tender_profile_state"]["profile"][
            "scoring_items"
        ][0].update(max_score=6),
        lambda values: values["project"]["meta"]["tender_profile_state"][
            "attention_profile"
        ]["items"][0]["evidence"][0]["expert_points"][0].update(name="节点控制"),
        lambda values: values["config_rubric"]["dimensions"]["D01"].update(weight=2.0),
        lambda values: values["config_lexicon"].update(quality=["质量", "验收"]),
        lambda values: values["multipliers"].update(D01=1.3),
        lambda values: values["profile_snapshot"]["weights_norm"].update(D01=0.9),
        lambda values: values.update(engine_version="v2.1"),
        lambda values: values["deployed_patch"]["patch_payload"].update(threshold=0.9),
        lambda values: values["calibrator_model"]["model_artifact"].update(bias=2.0),
        lambda values: values["post_processing"].update(evolution_total_score_scale=1.2),
        lambda values: values["resolved_scoring_inputs"]["anchors"][0].update(id="anchor-2"),
    ],
)
def test_every_scoring_context_change_gets_a_new_contract_hash(mutate) -> None:
    base_values = {
        "project_id": "project-1",
        "project": _project(),
        "config_rubric": {"dimensions": {"D01": {"weight": 1.0}}},
        "config_lexicon": {"quality": ["质量"]},
        "multipliers": {"D01": 1.2},
        "profile_snapshot": {"id": "expert-1", "weights_norm": {"D01": 1.0}},
        "scoring_engine_version": "v2",
        "engine_version": "v2",
        "deployed_patch": {"id": "patch-1", "patch_payload": {"threshold": 0.8}},
        "calibrator_model": {
            "calibrator_version": "calib-1",
            "model_artifact": {"model_type": "offset", "bias": 1.0},
        },
        "resolved_scoring_inputs": {"anchors": [{"id": "anchor-1"}]},
        "post_processing": {
            "evolution_total_score_scale": 1.1,
            "evolution_total_score_scale_applied": True,
        },
    }
    changed_values = copy.deepcopy(base_values)
    mutate(changed_values)

    base = assessment_contract_service.build_assessment_contract(**base_values)
    changed = assessment_contract_service.build_assessment_contract(**changed_values)

    assert base["contract_hash"] != changed["contract_hash"]


def test_attach_snapshot_and_summary_use_the_same_certified_identity() -> None:
    report: dict[str, object] = {"meta": {}}
    contract = _build()

    assessment_contract_service.attach_assessment_contract(report, contract)
    snapshot = assessment_contract_service.score_report_snapshot_contract_fields(report)
    summary = report["meta"]["assessment_contract_summary"]

    assert report["assessment_contract_hash"] == contract["contract_hash"]
    assert snapshot["assessment_contract_hash"] == contract["contract_hash"]
    assert snapshot["assessment_contract"] == contract
    assert summary["selector_version"] == "expert-point-selector-v3"
    assert summary["criteria_catalog_version"] == "construction-expert-catalog-v1"
    assert summary["secondary_criteria_count"] == 1


def test_legacy_snapshot_is_labeled_without_fabricated_identity() -> None:
    row = {"id": "legacy-report", "rule_total_score": 80.0}

    normalized = assessment_contract_service.normalize_score_report_snapshot(row)

    assert normalized["assessment_contract_status"] == "legacy_unversioned"
    assert normalized["assessment_contract_hash"] is None
    assert normalized["assessment_contract_schema_version"] is None
    assert normalized["assessment_contract"] is None


def test_tampered_contract_is_not_certified() -> None:
    contract = _build()
    contract["inputs"]["effective_multipliers"]["D01"] = 9.9

    normalized = assessment_contract_service.normalize_score_report_snapshot(
        {"assessment_contract": contract}
    )

    assert normalized["assessment_contract_status"] == "invalid_contract"
