#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.engine.dimensions import DIMENSIONS
from app.storage import (
    ensure_data_dirs,
    load_expert_profiles,
    load_projects,
    load_score_reports,
    load_submissions,
    save_expert_profiles,
    save_projects,
    save_score_reports,
)

DIMENSION_IDS = sorted(DIMENSIONS.keys())
DEFAULT_REGION = "合肥"
DEFAULT_QINGTIAN_MODEL_VERSION = "qingtian-2026.02"
DEFAULT_SCORING_ENGINE_LOCKED = "v2.0.0"
DEFAULT_CALIBRATOR_LOCKED = "calib_ridge_v1"
DEFAULT_NORM_RULE_VERSION = "v1_m=0.5+a/10_norm=sum"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_weights_raw() -> dict[str, int]:
    return {dim_id: 5 for dim_id in DIMENSION_IDS}


def normalize_weights(weights_raw: dict[str, int]) -> dict[str, float]:
    multipliers = {
        dim_id: 0.5 + (int(weights_raw.get(dim_id, 5)) / 10.0) for dim_id in DIMENSION_IDS
    }
    total = sum(multipliers.values()) or 1.0
    return {dim_id: multipliers[dim_id] / total for dim_id in DIMENSION_IDS}


def build_profile(name: str, raw: dict[str, int]) -> dict:
    now = now_iso()
    return {
        "id": str(uuid4()),
        "name": name,
        "weights_raw": raw,
        "weights_norm": normalize_weights(raw),
        "norm_rule_version": DEFAULT_NORM_RULE_VERSION,
        "created_at": now,
        "updated_at": now,
    }


def main() -> None:
    ensure_data_dirs()
    projects = load_projects()
    profiles = load_expert_profiles()
    submissions = load_submissions()
    snapshots = load_score_reports()

    profile_ids = {str(p.get("id")) for p in profiles}
    snapshot_keys = {(str(r.get("submission_id")), str(r.get("project_id"))) for r in snapshots}

    project_changed = 0
    profile_created = 0
    snapshot_created = 0

    for project in projects:
        changed = False
        if not project.get("region"):
            project["region"] = DEFAULT_REGION
            changed = True
        if not project.get("qingtian_model_version"):
            project["qingtian_model_version"] = DEFAULT_QINGTIAN_MODEL_VERSION
            changed = True
        if not project.get("scoring_engine_version_locked"):
            project["scoring_engine_version_locked"] = DEFAULT_SCORING_ENGINE_LOCKED
            changed = True
        if not project.get("calibrator_version_locked"):
            project["calibrator_version_locked"] = DEFAULT_CALIBRATOR_LOCKED
            changed = True
        if not project.get("status"):
            project["status"] = "scoring_preparation"
            changed = True
        if not project.get("updated_at"):
            project["updated_at"] = project.get("created_at") or now_iso()
            changed = True

        profile_id = str(project.get("expert_profile_id") or "")
        if not profile_id or profile_id not in profile_ids:
            profile = build_profile(
                f"{project.get('name', '项目')} 默认配置", default_weights_raw()
            )
            profiles.append(profile)
            profile_ids.add(str(profile["id"]))
            project["expert_profile_id"] = profile["id"]
            project["updated_at"] = now_iso()
            changed = True
            profile_created += 1

        if changed:
            project_changed += 1

    profile_map = {str(p.get("id")): p for p in profiles}
    for submission in submissions:
        sid = str(submission.get("id") or "")
        pid = str(submission.get("project_id") or "")
        if not sid or not pid or (sid, pid) in snapshot_keys:
            continue
        report = submission.get("report") or {}
        project = next((p for p in projects if str(p.get("id")) == pid), None)
        if project is None:
            continue
        profile = profile_map.get(str(project.get("expert_profile_id") or ""))
        snapshots.append(
            {
                "id": str(uuid4()),
                "submission_id": sid,
                "project_id": pid,
                "scoring_engine_version": str(
                    project.get("scoring_engine_version_locked") or DEFAULT_SCORING_ENGINE_LOCKED
                ),
                "expert_profile_snapshot": profile or {},
                "rule_dim_scores": report.get("dimension_scores", {}),
                "rule_total_score": float(
                    report.get("total_score", submission.get("total_score", 0.0)) or 0.0
                ),
                "pred_dim_scores": None,
                "pred_total_score": None,
                "pred_confidence": None,
                "penalties": report.get("penalties", []),
                "lint_findings": [],
                "suggestions": report.get("suggestions", []),
                "created_at": submission.get("created_at") or now_iso(),
            }
        )
        snapshot_keys.add((sid, pid))
        snapshot_created += 1

    save_projects(projects)
    save_expert_profiles(profiles)
    save_score_reports(snapshots)

    print("V2 P0 migration finished")
    print(f"projects_updated={project_changed}")
    print(f"profiles_created={profile_created}")
    print(f"score_snapshots_created={snapshot_created}")


if __name__ == "__main__":
    main()
