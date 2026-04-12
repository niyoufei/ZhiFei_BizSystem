from __future__ import annotations

from app.domain.material_parse_diagnostics import (
    build_material_parse_business_overview,
    build_material_parse_debug_info,
    build_material_parse_project_cache_request_delta,
)


def test_build_material_parse_business_overview_normalizes_counters_and_latest_values():
    overview = build_material_parse_business_overview(
        {
            "materials_total": "6",
            "parsed_materials": 4.9,
            "previewed_materials": None,
            "processing_materials": "2",
            "queued_materials": 1,
            "failed_materials": "0",
            "latest_finished_at": "2026-04-12T00:00:00+00:00",
            "latest_finished_filename": " sample.pdf ",
        }
    )

    assert overview == {
        "materials_total": 6,
        "parsed_materials": 4,
        "previewed_materials": 0,
        "processing_materials": 2,
        "queued_materials": 1,
        "failed_materials": 0,
        "latest_finished_at": "2026-04-12T00:00:00+00:00",
        "latest_finished_filename": "sample.pdf",
    }


def test_build_material_parse_debug_info_preserves_nested_shape_and_defaults():
    debug_info = build_material_parse_debug_info(
        {
            "backlog": "5",
            "worker_count": 3,
            "boq_resume_hit_rate": "0.25",
            "scheduler_cache_hit_ratio": "0.5",
            "scheduler_project_recent_avoided_rebuild_layers": ["status_core"],
            "scheduler_project_recent_stable_hot": 1,
        }
    )

    assert debug_info["pipeline"]["backlog"] == 5
    assert debug_info["pipeline"]["worker_count"] == 3
    assert debug_info["pipeline"]["alive_worker_count"] == 0
    assert debug_info["boq_acceleration"]["boq_resume_hit_rate"] == 0.25
    assert debug_info["cache"]["scheduler_cache_hit_ratio"] == 0.5
    assert debug_info["project_cache"]["scheduler_project_recent_avoided_rebuild_layers"] == [
        "status_core"
    ]
    assert debug_info["project_cache"]["scheduler_project_recent_stable_hot"] is True
    assert debug_info["project_cache"]["scheduler_project_recent_stable_hot_badge_label"] == ""


def test_build_material_parse_project_cache_request_delta_supports_custom_layer_weights():
    delta = build_material_parse_project_cache_request_delta(
        {},
        {
            "status_materials_cache_hits": 1,
            "jobs_summary_cache_rebuilds": 1,
        },
        layer_work_units={
            "jobs_summary": 10,
            "status_materials": 20,
            "status_core": 30,
        },
    )

    assert delta["scheduler_project_recent_avoided_rebuild_layers"] == ["status_materials"]
    assert delta["scheduler_project_recent_rebuilt_layers"] == ["jobs_summary"]
    assert delta["scheduler_project_recent_avoided_rebuild_work_units"] == 20
    assert delta["scheduler_project_recent_rebuilt_work_units"] == 10
