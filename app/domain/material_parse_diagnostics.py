from __future__ import annotations

from typing import Dict, Mapping

DEFAULT_PROJECT_CACHE_LAYER_WORK_UNITS: Dict[str, int] = {
    "jobs_summary": 1,
    "status_materials": 2,
    "status_core": 3,
}


def _to_int(value: object) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _to_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def build_material_parse_business_overview(summary: Mapping[str, object]) -> Dict[str, object]:
    meta = dict(summary or {})
    return {
        "materials_total": _to_int(meta.get("materials_total")),
        "parsed_materials": _to_int(meta.get("parsed_materials")),
        "previewed_materials": _to_int(meta.get("previewed_materials")),
        "processing_materials": _to_int(meta.get("processing_materials")),
        "queued_materials": _to_int(meta.get("queued_materials")),
        "failed_materials": _to_int(meta.get("failed_materials")),
        "latest_finished_at": str(meta.get("latest_finished_at") or "").strip() or None,
        "latest_finished_filename": str(meta.get("latest_finished_filename") or "").strip() or None,
    }


def build_material_parse_debug_info(summary: Mapping[str, object]) -> Dict[str, object]:
    meta = dict(summary or {})
    return {
        "pipeline": {
            "backlog": _to_int(meta.get("backlog")),
            "worker_count": _to_int(meta.get("worker_count")),
            "alive_worker_count": _to_int(meta.get("alive_worker_count")),
            "preview_express_reserved_worker_count": _to_int(
                meta.get("preview_express_reserved_worker_count")
            ),
            "preview_reserved_worker_count": _to_int(meta.get("preview_reserved_worker_count")),
        },
        "boq_acceleration": {
            "boq_guided_full_materials": _to_int(meta.get("boq_guided_full_materials")),
            "boq_guided_full_strong_materials": _to_int(
                meta.get("boq_guided_full_strong_materials")
            ),
            "boq_resumed_full_materials": _to_int(meta.get("boq_resumed_full_materials")),
            "boq_resumed_sheet_count": _to_int(meta.get("boq_resumed_sheet_count")),
            "boq_saved_row_count": _to_int(meta.get("boq_saved_row_count")),
            "boq_skipped_tail_sheets": _to_int(meta.get("boq_skipped_tail_sheets")),
            "boq_resume_hit_rate": _to_float(meta.get("boq_resume_hit_rate")),
        },
        "scheduler_hits": {
            "scheduler_project_continuity_bonus_hits": _to_int(
                meta.get("scheduler_project_continuity_bonus_hits")
            ),
            "scheduler_followup_full_bonus_hits": _to_int(
                meta.get("scheduler_followup_full_bonus_hits")
            ),
            "scheduler_same_material_followup_bonus_hits": _to_int(
                meta.get("scheduler_same_material_followup_bonus_hits")
            ),
            "scheduler_active_project_bonus_hits": _to_int(
                meta.get("scheduler_active_project_bonus_hits")
            ),
            "scheduler_active_project_type_bonus_hits": _to_int(
                meta.get("scheduler_active_project_type_bonus_hits")
            ),
            "scheduler_active_project_quota_exhausted_count": _to_int(
                meta.get("scheduler_active_project_quota_exhausted_count")
            ),
            "scheduler_active_project_type_quota_exhausted_count": _to_int(
                meta.get("scheduler_active_project_type_quota_exhausted_count")
            ),
        },
        "cache": {
            "scheduler_claim_snapshot_cache_hits": _to_int(
                meta.get("scheduler_claim_snapshot_cache_hits")
            ),
            "scheduler_claim_snapshot_cache_rebuilds": _to_int(
                meta.get("scheduler_claim_snapshot_cache_rebuilds")
            ),
            "scheduler_claim_context_cache_hits": _to_int(
                meta.get("scheduler_claim_context_cache_hits")
            ),
            "scheduler_claim_context_cache_rebuilds": _to_int(
                meta.get("scheduler_claim_context_cache_rebuilds")
            ),
            "scheduler_project_stage_cache_hits": _to_int(
                meta.get("scheduler_project_stage_cache_hits")
            ),
            "scheduler_project_stage_cache_rebuilds": _to_int(
                meta.get("scheduler_project_stage_cache_rebuilds")
            ),
            "scheduler_priority_context_cache_hits": _to_int(
                meta.get("scheduler_priority_context_cache_hits")
            ),
            "scheduler_priority_context_cache_rebuilds": _to_int(
                meta.get("scheduler_priority_context_cache_rebuilds")
            ),
            "scheduler_jobs_summary_cache_hits": _to_int(
                meta.get("scheduler_jobs_summary_cache_hits")
            ),
            "scheduler_jobs_summary_cache_rebuilds": _to_int(
                meta.get("scheduler_jobs_summary_cache_rebuilds")
            ),
            "scheduler_status_materials_cache_hits": _to_int(
                meta.get("scheduler_status_materials_cache_hits")
            ),
            "scheduler_status_materials_cache_rebuilds": _to_int(
                meta.get("scheduler_status_materials_cache_rebuilds")
            ),
            "scheduler_status_core_cache_hits": _to_int(
                meta.get("scheduler_status_core_cache_hits")
            ),
            "scheduler_status_core_cache_rebuilds": _to_int(
                meta.get("scheduler_status_core_cache_rebuilds")
            ),
            "scheduler_cache_hit_total": _to_int(meta.get("scheduler_cache_hit_total")),
            "scheduler_cache_rebuild_total": _to_int(meta.get("scheduler_cache_rebuild_total")),
            "scheduler_cache_hit_ratio": _to_float(meta.get("scheduler_cache_hit_ratio")),
        },
        "project_cache": {
            "scheduler_project_jobs_summary_cache_hits": _to_int(
                meta.get("scheduler_project_jobs_summary_cache_hits")
            ),
            "scheduler_project_jobs_summary_cache_rebuilds": _to_int(
                meta.get("scheduler_project_jobs_summary_cache_rebuilds")
            ),
            "scheduler_project_jobs_summary_cache_state": str(
                meta.get("scheduler_project_jobs_summary_cache_state") or ""
            ).strip(),
            "scheduler_project_status_materials_cache_hits": _to_int(
                meta.get("scheduler_project_status_materials_cache_hits")
            ),
            "scheduler_project_status_materials_cache_rebuilds": _to_int(
                meta.get("scheduler_project_status_materials_cache_rebuilds")
            ),
            "scheduler_project_status_materials_cache_state": str(
                meta.get("scheduler_project_status_materials_cache_state") or ""
            ).strip(),
            "scheduler_project_status_core_cache_hits": _to_int(
                meta.get("scheduler_project_status_core_cache_hits")
            ),
            "scheduler_project_status_core_cache_rebuilds": _to_int(
                meta.get("scheduler_project_status_core_cache_rebuilds")
            ),
            "scheduler_project_status_core_cache_state": str(
                meta.get("scheduler_project_status_core_cache_state") or ""
            ).strip(),
            "scheduler_project_cache_hit_total": _to_int(
                meta.get("scheduler_project_cache_hit_total")
            ),
            "scheduler_project_cache_rebuild_total": _to_int(
                meta.get("scheduler_project_cache_rebuild_total")
            ),
            "scheduler_project_cache_hit_ratio": _to_float(
                meta.get("scheduler_project_cache_hit_ratio")
            ),
            "scheduler_project_cache_net_savings": _to_int(
                meta.get("scheduler_project_cache_net_savings")
            ),
            "scheduler_project_cache_state": str(
                meta.get("scheduler_project_cache_state") or ""
            ).strip(),
            "scheduler_project_cache_hot_layer_count": _to_int(
                meta.get("scheduler_project_cache_hot_layer_count")
            ),
            "scheduler_project_cache_warming_layer_count": _to_int(
                meta.get("scheduler_project_cache_warming_layer_count")
            ),
            "scheduler_project_cache_cold_layer_count": _to_int(
                meta.get("scheduler_project_cache_cold_layer_count")
            ),
            "scheduler_project_recent_avoided_rebuild_layers": list(
                meta.get("scheduler_project_recent_avoided_rebuild_layers") or []
            ),
            "scheduler_project_recent_rebuilt_layers": list(
                meta.get("scheduler_project_recent_rebuilt_layers") or []
            ),
            "scheduler_project_recent_request_window_size": _to_int(
                meta.get("scheduler_project_recent_request_window_size")
            ),
            "scheduler_project_recent_window_state": str(
                meta.get("scheduler_project_recent_window_state") or ""
            ).strip(),
            "scheduler_project_recent_avoided_rebuild_work_units": _to_int(
                meta.get("scheduler_project_recent_avoided_rebuild_work_units")
            ),
            "scheduler_project_recent_rebuilt_work_units": _to_int(
                meta.get("scheduler_project_recent_rebuilt_work_units")
            ),
            "scheduler_project_recent_avoided_rebuild_work_units_avg": _to_float(
                meta.get("scheduler_project_recent_avoided_rebuild_work_units_avg")
            ),
            "scheduler_project_recent_rebuilt_work_units_avg": _to_float(
                meta.get("scheduler_project_recent_rebuilt_work_units_avg")
            ),
            "scheduler_project_recent_net_saved_work_units_avg": _to_float(
                meta.get("scheduler_project_recent_net_saved_work_units_avg")
            ),
            "scheduler_project_recent_stable_hot_threshold": _to_int(
                meta.get("scheduler_project_recent_stable_hot_threshold")
            ),
            "scheduler_project_recent_consecutive_steady_round_count": _to_int(
                meta.get("scheduler_project_recent_consecutive_steady_round_count")
            ),
            "scheduler_project_recent_stable_hot": bool(
                meta.get("scheduler_project_recent_stable_hot")
            ),
            "scheduler_project_recent_stable_hot_remaining_rounds": _to_int(
                meta.get("scheduler_project_recent_stable_hot_remaining_rounds")
            ),
            "scheduler_project_recent_stable_hot_progress_summary_label": str(
                meta.get("scheduler_project_recent_stable_hot_progress_summary_label") or ""
            ).strip(),
            "scheduler_project_recent_stable_hot_badge_label": str(
                meta.get("scheduler_project_recent_stable_hot_badge_label") or ""
            ).strip(),
        },
    }


def build_material_parse_project_cache_request_delta(
    before_stats: Mapping[str, object],
    after_stats: Mapping[str, object],
    *,
    layer_work_units: Mapping[str, int] | None = None,
) -> Dict[str, object]:
    normalized_before = dict(before_stats or {})
    normalized_after = dict(after_stats or {})
    work_units = dict(layer_work_units or DEFAULT_PROJECT_CACHE_LAYER_WORK_UNITS)

    def _delta(name: str) -> int:
        return int(normalized_after.get(name, 0)) - int(normalized_before.get(name, 0))

    jobs_summary_hit_delta = _delta("jobs_summary_cache_hits")
    jobs_summary_rebuild_delta = _delta("jobs_summary_cache_rebuilds")
    status_materials_hit_delta = _delta("status_materials_cache_hits")
    status_materials_rebuild_delta = _delta("status_materials_cache_rebuilds")
    status_core_hit_delta = _delta("status_core_cache_hits")
    status_core_rebuild_delta = _delta("status_core_cache_rebuilds")
    jobs_summary_saved = jobs_summary_hit_delta > 0 and jobs_summary_rebuild_delta <= 0
    status_materials_saved = status_materials_hit_delta > 0 and status_materials_rebuild_delta <= 0
    status_core_saved = status_core_hit_delta > 0 and status_core_rebuild_delta <= 0

    recent_avoided_rebuild_layers: list[str] = []
    if status_core_saved:
        recent_avoided_rebuild_layers.extend(["status_core", "status_materials", "jobs_summary"])
    else:
        if status_materials_saved:
            recent_avoided_rebuild_layers.append("status_materials")
        if jobs_summary_saved:
            recent_avoided_rebuild_layers.append("jobs_summary")

    recent_rebuilt_layers = [
        layer
        for layer, rebuild_delta in (
            ("status_core", status_core_rebuild_delta),
            ("status_materials", status_materials_rebuild_delta),
            ("jobs_summary", jobs_summary_rebuild_delta),
        )
        if rebuild_delta > 0
    ]
    recent_avoided_rebuild_work_units = sum(
        int(work_units.get(layer, 0)) for layer in recent_avoided_rebuild_layers
    )
    recent_rebuilt_work_units = sum(
        int(work_units.get(layer, 0)) for layer in recent_rebuilt_layers
    )
    return {
        "scheduler_project_recent_avoided_rebuild_layers": list(recent_avoided_rebuild_layers),
        "scheduler_project_recent_rebuilt_layers": list(recent_rebuilt_layers),
        "scheduler_project_recent_avoided_rebuild_layer_count": len(recent_avoided_rebuild_layers),
        "scheduler_project_recent_rebuilt_layer_count": len(recent_rebuilt_layers),
        "scheduler_project_recent_avoided_rebuild_work_units": int(
            recent_avoided_rebuild_work_units
        ),
        "scheduler_project_recent_rebuilt_work_units": int(recent_rebuilt_work_units),
    }
