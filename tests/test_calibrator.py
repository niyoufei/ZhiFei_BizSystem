from __future__ import annotations

from app.engine.calibrator import build_feature_row


def test_build_feature_row_includes_taxonomy_and_material_maturity_features() -> None:
    report = {
        "rule_total_score": 82.5,
        "rule_dim_scores": {"01": {"dim_score": 8.0}},
        "penalties": [],
        "meta": {
            "material_utilization": {
                "retrieval_hit_rate": 0.75,
                "retrieval_file_coverage_rate": 0.5,
                "consistency_hit_rate": 0.8,
                "material_dimension_hit_rate": 0.6,
                "available_types": ["drawing", "boq"],
                "uncovered_types": ["drawing"],
            },
            "material_quality": {
                "total_files": 6,
                "total_parsed_chars": 12000,
                "parse_fail_ratio": 0.1,
            },
            "material_utilization_gate": {"passed": True, "blocked": False, "warned": True},
            "evidence_trace": {
                "mandatory_hit_rate": 0.9,
                "source_files_hit_count": 3,
                "total_requirements": 10,
                "total_hits": 8,
            },
            "material_retrieval": {
                "material_dimension_requirements": 4,
                "feature_confidence_requirements": 2,
                "feedback_evolution_requirements": 1,
            },
        },
    }
    submission = {"text": "施工总平面图|BIM策划\t资源配置", "image_count": 2}
    project = {"project_type": "装修及景观项目", "bid_method": "AI评标"}

    row = build_feature_row(report, submission=submission, project=project)
    x = row["x_features"]

    assert x["project_type_known"] == 1.0
    assert x["project_type_decoration_landscape"] == 1.0
    assert x["project_type_high_standard_farmland"] == 0.0
    assert x["bid_method_known"] == 1.0
    assert x["bid_method_ai_comprehensive_three_stage"] == 1.0
    assert x["bid_method_ai_reasonable_price"] == 0.0
    assert x["material_retrieval_hit_rate"] == 0.75
    assert x["material_retrieval_file_coverage_rate"] == 0.5
    assert x["material_consistency_hit_rate"] == 0.8
    assert x["material_dimension_hit_rate"] == 0.6
    assert x["material_available_type_count"] == 2.0
    assert x["material_uncovered_type_count"] == 1.0
    assert x["material_type_coverage_ratio"] == 0.5
    assert x["material_total_files"] == 6.0
    assert x["material_total_parsed_chars"] == 12000.0
    assert x["material_parse_fail_ratio"] == 0.1
    assert x["material_gate_passed"] == 1.0
    assert x["material_gate_warned"] == 1.0
    assert x["material_gate_blocked"] == 0.0
    assert x["evidence_mandatory_hit_rate"] == 0.9
    assert x["evidence_source_files_hit_count"] == 3.0
    assert x["evidence_total_requirements"] == 10.0
    assert x["evidence_total_hits"] == 8.0
    assert x["material_dimension_requirements"] == 4.0
    assert x["feature_confidence_requirements"] == 2.0
    assert x["feedback_evolution_requirements"] == 1.0
    assert x["has_table"] == 1.0
    assert x["has_images"] == 1.0
