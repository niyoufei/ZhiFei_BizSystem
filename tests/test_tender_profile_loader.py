import json

import pytest

from app.engine.tender_profile import (
    EVAL_METHOD_COMPREHENSIVE,
    WEIGHT_GATE,
    TenderProfileValidationError,
    load_tender_profile,
    load_tender_profile_by_id,
    profile_from_dict,
    tender_profile_to_dict,
    validate_tender_profile,
)


def _valid_profile_dict():
    return {
        "tender_id": "synthetic-tender",
        "tender_name": "Synthetic Tender",
        "version": "v1",
        "score_scale": 100,
        "legacy_dimension_refs": ["legacy-overall"],
        "source_note": "synthetic test fixture only",
        "scoring_items": [
            {
                "item_id": "method",
                "name": "Construction method",
                "max_score": 60,
                "bands": [
                    {
                        "band_id": "method-pass",
                        "label": "Pass",
                        "min_score": 0,
                        "max_score": 60,
                        "description": "synthetic band",
                        "triggers": ["complete"],
                    }
                ],
                "evidence_requirements": ["synthetic evidence"],
                "legacy_dimension_refs": ["legacy-method"],
            },
            {
                "item_id": "safety",
                "name": "Safety plan",
                "max_score": 40,
                "bands": [
                    {
                        "band_id": "safety-pass",
                        "label": "Pass",
                        "min_score": 0,
                        "max_score": 40,
                    }
                ],
            },
        ],
        "hard_redlines": [
            {
                "redline_id": "manual-check",
                "description": "Synthetic manual review trigger",
                "action": "manual_review",
                "applies_to": ["method"],
            }
        ],
    }


def _write_profile(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


def test_load_tender_profile_from_synthetic_json(tmp_path):
    profile_path = tmp_path / "profile.json"
    _write_profile(profile_path, _valid_profile_dict())

    profile = load_tender_profile(profile_path)
    serialized = tender_profile_to_dict(profile)

    assert profile.tender_id == "synthetic-tender"
    assert serialized["score_scale"] == 100.0
    assert serialized["scoring_items"][0]["bands"][0]["band_id"] == "method-pass"
    assert serialized["scoring_items"][0]["bands"][0]["min_score"] == 0.0


def test_validate_score_scale_matches_scoring_item_total(tmp_path):
    profile_path = tmp_path / "profile.json"
    _write_profile(profile_path, _valid_profile_dict())

    profile = load_tender_profile(profile_path)

    validate_tender_profile(profile)
    assert sum(item.max_score for item in profile.scoring_items) == profile.score_scale


def test_duplicate_item_id_fails(tmp_path):
    data = _valid_profile_dict()
    data["scoring_items"][1]["item_id"] = "method"
    profile_path = tmp_path / "profile.json"
    _write_profile(profile_path, data)

    with pytest.raises(TenderProfileValidationError, match="item_id"):
        load_tender_profile(profile_path)


def test_band_out_of_range_fails(tmp_path):
    data = _valid_profile_dict()
    data["scoring_items"][0]["bands"][0]["max_score"] = 61
    profile_path = tmp_path / "profile.json"
    _write_profile(profile_path, data)

    with pytest.raises(TenderProfileValidationError, match="max_score"):
        load_tender_profile(profile_path)


def test_invalid_hard_redline_action_fails(tmp_path):
    data = _valid_profile_dict()
    data["hard_redlines"][0]["action"] = "block"
    profile_path = tmp_path / "profile.json"
    _write_profile(profile_path, data)

    with pytest.raises(TenderProfileValidationError, match="action"):
        load_tender_profile(profile_path)


def test_load_tender_profile_by_id_uses_temp_base_dir(tmp_path):
    data = _valid_profile_dict()
    base_dir = tmp_path / "profiles"
    base_dir.mkdir()
    profile_path = base_dir / "synthetic-tender.json"
    _write_profile(profile_path, data)

    profile = load_tender_profile_by_id("synthetic-tender", base_dir=base_dir)

    assert profile.tender_name == "Synthetic Tender"


def test_legacy_dimension_refs_are_retained_but_not_required(tmp_path):
    data = _valid_profile_dict()
    profile_path = tmp_path / "profile.json"
    _write_profile(profile_path, data)

    profile = load_tender_profile(profile_path)
    serialized = tender_profile_to_dict(profile)

    assert profile.legacy_dimension_refs == ("legacy-overall",)
    assert serialized["scoring_items"][1]["legacy_dimension_refs"] == []


def test_existing_profile_from_dict_backbone_smoke():
    profile = profile_from_dict(
        {
            "tender_id": "legacy-synthetic",
            "tender_name": "Legacy Synthetic Tender",
            "eval_method": EVAL_METHOD_COMPREHENSIVE,
            "shigong_max_score": 5,
            "bands": [
                {
                    "name": "eligible",
                    "lower": 0,
                    "upper": 5,
                    "lower_inclusive": True,
                }
            ],
            "considerations": ["synthetic consideration"],
            "shigong_weight_type": WEIGHT_GATE,
        }
    )

    assert profile.band_of(3) == "eligible"
    assert profile.normalize(2.5) == 0.5
