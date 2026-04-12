from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Sequence


def summarize_feature_rows_for_governance(
    rows: object,
    *,
    normalize_dimension_id: Callable[[object], str],
) -> Dict[str, object]:
    typed_rows = [row for row in (rows if isinstance(rows, list) else []) if isinstance(row, dict)]
    active_rows = [row for row in typed_rows if bool(row.get("active", True))]
    high_conf_rows = [
        row
        for row in active_rows
        if float(_to_float_or_none(row.get("confidence_score")) or 0.0) >= 0.7
    ]
    dim_counter: Dict[str, int] = {}
    for row in active_rows:
        dim_id = (
            normalize_dimension_id(row.get("dimension_id"))
            or str(row.get("dimension_id") or "").strip()
        )
        if not dim_id:
            continue
        dim_counter[dim_id] = dim_counter.get(dim_id, 0) + 1
    top_dimensions = [
        {"dimension_id": dim_id, "feature_count": count}
        for dim_id, count in sorted(dim_counter.items(), key=lambda item: (-item[1], item[0]))[:4]
    ]
    return {
        "summary_type": "high_score_features",
        "primary_count": len(typed_rows),
        "active_count": len(active_rows),
        "high_confidence_count": len(high_conf_rows),
        "dimension_count": len(dim_counter),
        "top_dimensions": top_dimensions,
    }


def summarize_versioned_artifact_payload(
    artifact: str,
    payload: object,
    *,
    project_id: str,
    normalize_dimension_id: Callable[[object], str],
    calibrator_auto_review_state: Callable[[Mapping[str, object]], Mapping[str, object]],
    calibrator_bootstrap_small_sample: Callable[[Mapping[str, object]], bool],
    calibrator_deployment_mode: Callable[[Mapping[str, object]], str],
) -> Dict[str, object]:
    if artifact == "high_score_features":
        return summarize_feature_rows_for_governance(
            payload,
            normalize_dimension_id=normalize_dimension_id,
        )
    if artifact == "calibration_models":
        rows = [
            row for row in (payload if isinstance(payload, list) else []) if isinstance(row, dict)
        ]
        latest = max(
            rows,
            key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""),
            default={},
        )
        latest_auto_review = calibrator_auto_review_state(latest)
        return {
            "summary_type": "calibration_models",
            "primary_count": len(rows),
            "latest_calibrator_version": str(latest.get("calibrator_version") or ""),
            "latest_model_type": str(latest.get("model_type") or ""),
            "gate_passed_count": sum(1 for row in rows if bool(row.get("gate_passed"))),
            "latest_bootstrap_small_sample": calibrator_bootstrap_small_sample(latest),
            "latest_deployment_mode": calibrator_deployment_mode(latest),
            "latest_auto_review_action": str(latest_auto_review.get("action") or ""),
            "latest_auto_review_passed": latest_auto_review.get("passed"),
        }
    if artifact == "expert_profiles":
        rows = [
            row for row in (payload if isinstance(payload, list) else []) if isinstance(row, dict)
        ]
        latest = max(
            rows,
            key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""),
            default={},
        )
        return {
            "summary_type": "expert_profiles",
            "primary_count": len(rows),
            "read_only_count": sum(1 for row in rows if bool(row.get("read_only"))),
            "latest_profile_id": str(latest.get("id") or ""),
            "latest_profile_name": str(latest.get("name") or ""),
        }
    if artifact == "evolution_reports":
        rows = payload if isinstance(payload, dict) else {}
        project_payload = rows.get(project_id) if isinstance(rows.get(project_id), dict) else {}
        return {
            "summary_type": "evolution_reports",
            "primary_count": len(rows),
            "project_present": bool(project_payload),
            "project_high_score_logic_count": len(project_payload.get("high_score_logic") or []),
            "project_writing_guidance_count": len(project_payload.get("writing_guidance") or []),
            "project_updated_at": str(
                project_payload.get("updated_at") or project_payload.get("created_at") or ""
            ),
        }
    if isinstance(payload, list):
        return {"summary_type": "list", "primary_count": len(payload)}
    if isinstance(payload, dict):
        return {"summary_type": "dict", "primary_count": len(payload)}
    return {"summary_type": type(payload).__name__, "primary_count": 0}


def artifact_payload_fingerprint(payload: object) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except TypeError:
        return repr(payload)


def artifact_summary_delta(
    current_summary: Dict[str, object],
    latest_summary: Dict[str, object],
) -> Dict[str, object]:
    delta: Dict[str, object] = {}
    for key, current_value in current_summary.items():
        latest_value = latest_summary.get(key)
        if not isinstance(current_value, (int, float)) or not isinstance(
            latest_value, (int, float)
        ):
            continue
        diff = float(current_value) - float(latest_value)
        delta[key] = (
            int(diff)
            if isinstance(current_value, int) and isinstance(latest_value, int)
            else round(diff, 4)
        )
    return delta


def build_artifact_version_history(
    version_targets: Sequence[tuple[str, Path]],
    *,
    list_versions: Callable[[Path], list[dict[str, Any]]],
) -> list[dict[str, object]]:
    history: list[dict[str, object]] = []
    for artifact, path in version_targets:
        versions = list_versions(path)
        latest = versions[0] if versions else {}
        history.append(
            {
                "artifact": artifact,
                "version_count": len(versions),
                "latest_version_id": str(latest.get("version_id") or ""),
                "latest_created_at": str(latest.get("created_at") or ""),
                "recent_versions": [
                    {
                        "version_id": str(item.get("version_id") or ""),
                        "created_at": str(item.get("created_at") or ""),
                    }
                    for item in versions[:6]
                ],
            }
        )
    return history


def build_governance_artifact_impacts(
    artifact_specs: Mapping[str, Mapping[str, object]],
    *,
    project_id: str,
    load_payload: Callable[[str], object],
    list_versions: Callable[[Path], list[dict[str, Any]]],
    load_version: Callable[[Path, str, object], object],
    summarize_payload: Callable[[str, object, str], Dict[str, object]],
    is_snapshot_load_error: Callable[[Exception], bool],
) -> list[dict[str, object]]:
    impacts: list[dict[str, object]] = []
    for artifact, spec in artifact_specs.items():
        path = spec.get("path")
        if not isinstance(path, Path):
            continue
        default_payload = spec.get("default_payload")
        current_payload = load_payload(artifact)
        current_summary = summarize_payload(artifact, current_payload, project_id)
        versions = list_versions(path)
        latest_snapshot = versions[0] if versions else {}
        latest_summary: Dict[str, object] = {}
        latest_payload = None
        latest_version_id = str(latest_snapshot.get("version_id") or "")
        if latest_version_id:
            try:
                latest_payload = load_version(path, latest_version_id, default_payload)
                latest_summary = summarize_payload(artifact, latest_payload, project_id)
            except Exception as exc:  # pragma: no cover - caller controls recoverable errors
                if not is_snapshot_load_error(exc):
                    raise
                latest_summary = {}
                latest_payload = None
        matches_latest_snapshot = (
            bool(latest_version_id)
            and latest_payload is not None
            and artifact_payload_fingerprint(current_payload)
            == artifact_payload_fingerprint(latest_payload)
        )
        impacts.append(
            {
                "artifact": artifact,
                "current_summary": current_summary,
                "latest_snapshot_version_id": latest_version_id or None,
                "latest_snapshot_created_at": str(latest_snapshot.get("created_at") or ""),
                "latest_snapshot_summary": latest_summary,
                "matches_latest_snapshot": matches_latest_snapshot,
                "changed_since_latest_snapshot": bool(latest_version_id)
                and not matches_latest_snapshot,
                "delta_vs_latest_snapshot": artifact_summary_delta(current_summary, latest_summary),
            }
        )
    return impacts


def _to_float_or_none(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
