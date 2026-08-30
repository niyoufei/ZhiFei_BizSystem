from __future__ import annotations

import copy
from typing import Callable, Dict, List, Optional


def _append_rollback_note(error: BaseException, rollback_error: BaseException) -> None:
    note = f"rescore rollback also failed: {type(rollback_error).__name__}: {rollback_error}"
    add_note = getattr(error, "add_note", None)
    if callable(add_note):
        add_note(note)
        return
    notes = list(getattr(error, "__notes__", []))
    notes.append(note)
    error.__notes__ = notes


def aggregate_material_utilization_summaries(
    summaries: List[Dict[str, object]],
    *,
    to_float_or_none: Callable[[object], Optional[float]],
    normalize_material_type: Callable[[object], str],
) -> Dict[str, object]:
    """聚合多份施组评分的资料利用统计，便于前端直接展示。"""
    by_type: Dict[str, Dict[str, int]] = {}
    available_types: List[str] = []
    uncovered_types: List[str] = []
    retrieval_total = 0
    retrieval_hit = 0
    consistency_total = 0
    consistency_hit = 0
    fallback_total = 0
    fallback_hit = 0
    retrieval_file_total = 0
    retrieval_file_hit = 0
    retrieval_selected_filenames: set[str] = set()
    retrieval_hit_filenames: set[str] = set()
    retrieval_selected_via_counts: Dict[str, int] = {}
    retrieval_total_via_counts: Dict[str, int] = {}
    retrieval_hit_via_counts: Dict[str, int] = {}
    retrieval_top_k = 0
    retrieval_per_type_quota = 0
    retrieval_per_file_quota = 0
    retrieval_base_top_k = 0
    retrieval_base_per_type_quota = 0
    retrieval_base_per_file_quota = 0
    material_total_size_mb = 0.0
    material_type_count = 0
    material_file_count = 0
    retrieval_budget_reasons: List[str] = []
    query_terms_count = 0
    query_numeric_terms_count = 0

    def _rate(hit_cnt: int, total_cnt: int) -> Optional[float]:
        if total_cnt <= 0:
            return None
        return round(float(hit_cnt) / float(total_cnt), 4)

    def _ensure_bucket(material_type: str) -> Dict[str, int]:
        if material_type not in by_type:
            by_type[material_type] = {
                "retrieval_total": 0,
                "retrieval_hit": 0,
                "consistency_total": 0,
                "consistency_hit": 0,
                "fallback_total": 0,
                "fallback_hit": 0,
            }
        return by_type[material_type]

    for raw in summaries:
        if not isinstance(raw, dict):
            continue
        retrieval_total += int(to_float_or_none(raw.get("retrieval_total")) or 0)
        retrieval_hit += int(to_float_or_none(raw.get("retrieval_hit")) or 0)
        retrieval_file_total += int(to_float_or_none(raw.get("retrieval_file_total")) or 0)
        retrieval_file_hit += int(to_float_or_none(raw.get("retrieval_file_hit")) or 0)
        selected_files_raw = raw.get("retrieval_selected_filenames")
        if isinstance(selected_files_raw, list):
            for item in selected_files_raw:
                filename = str(item or "").strip()
                if filename:
                    retrieval_selected_filenames.add(filename)
        hit_files_raw = raw.get("retrieval_hit_filenames")
        if isinstance(hit_files_raw, list):
            for item in hit_files_raw:
                filename = str(item or "").strip()
                if filename:
                    retrieval_hit_filenames.add(filename)
        retrieval_top_k = max(
            retrieval_top_k,
            int(to_float_or_none(raw.get("retrieval_top_k")) or 0),
        )
        retrieval_per_type_quota = max(
            retrieval_per_type_quota,
            int(to_float_or_none(raw.get("retrieval_per_type_quota")) or 0),
        )
        retrieval_per_file_quota = max(
            retrieval_per_file_quota,
            int(to_float_or_none(raw.get("retrieval_per_file_quota")) or 0),
        )
        retrieval_base_top_k = max(
            retrieval_base_top_k,
            int(to_float_or_none(raw.get("retrieval_base_top_k")) or 0),
        )
        retrieval_base_per_type_quota = max(
            retrieval_base_per_type_quota,
            int(to_float_or_none(raw.get("retrieval_base_per_type_quota")) or 0),
        )
        retrieval_base_per_file_quota = max(
            retrieval_base_per_file_quota,
            int(to_float_or_none(raw.get("retrieval_base_per_file_quota")) or 0),
        )
        material_total_size_mb = max(
            material_total_size_mb,
            float(to_float_or_none(raw.get("material_total_size_mb")) or 0.0),
        )
        material_type_count = max(
            material_type_count,
            int(to_float_or_none(raw.get("material_type_count")) or 0),
        )
        material_file_count = max(
            material_file_count,
            int(to_float_or_none(raw.get("material_file_count")) or 0),
        )
        reasons_raw = raw.get("retrieval_budget_reasons")
        if isinstance(reasons_raw, list):
            for item in reasons_raw:
                text = str(item or "").strip()
                if text and text not in retrieval_budget_reasons:
                    retrieval_budget_reasons.append(text)
        selected_via_raw = raw.get("retrieval_selected_via_counts")
        if isinstance(selected_via_raw, dict):
            for key, value in selected_via_raw.items():
                mode = str(key or "").strip() or "unknown"
                retrieval_selected_via_counts[mode] = int(
                    retrieval_selected_via_counts.get(mode, 0)
                ) + int(to_float_or_none(value) or 0)
        total_via_raw = raw.get("retrieval_total_via_counts")
        if isinstance(total_via_raw, dict):
            for key, value in total_via_raw.items():
                mode = str(key or "").strip() or "unknown"
                retrieval_total_via_counts[mode] = int(
                    retrieval_total_via_counts.get(mode, 0)
                ) + int(to_float_or_none(value) or 0)
        hit_via_raw = raw.get("retrieval_hit_via_counts")
        if isinstance(hit_via_raw, dict):
            for key, value in hit_via_raw.items():
                mode = str(key or "").strip() or "unknown"
                retrieval_hit_via_counts[mode] = int(retrieval_hit_via_counts.get(mode, 0)) + int(
                    to_float_or_none(value) or 0
                )
        query_terms_count += int(to_float_or_none(raw.get("query_terms_count")) or 0)
        query_numeric_terms_count += int(
            to_float_or_none(raw.get("query_numeric_terms_count")) or 0
        )
        consistency_total += int(to_float_or_none(raw.get("consistency_total")) or 0)
        consistency_hit += int(to_float_or_none(raw.get("consistency_hit")) or 0)

        raw_types = raw.get("available_types")
        if isinstance(raw_types, list):
            for item in raw_types:
                key = normalize_material_type(item)
                if key not in available_types:
                    available_types.append(key)
                    _ensure_bucket(key)

        raw_uncovered = raw.get("uncovered_types")
        if isinstance(raw_uncovered, list):
            for item in raw_uncovered:
                key = normalize_material_type(item)
                if key not in uncovered_types:
                    uncovered_types.append(key)

        raw_by_type = raw.get("by_type")
        if not isinstance(raw_by_type, dict):
            continue
        for mat_type_raw, row in raw_by_type.items():
            key = normalize_material_type(mat_type_raw)
            bucket = _ensure_bucket(key)
            row_dict = row if isinstance(row, dict) else {}
            rt = int(to_float_or_none(row_dict.get("retrieval_total")) or 0)
            rh = int(to_float_or_none(row_dict.get("retrieval_hit")) or 0)
            ct = int(to_float_or_none(row_dict.get("consistency_total")) or 0)
            ch = int(to_float_or_none(row_dict.get("consistency_hit")) or 0)
            ft = int(to_float_or_none(row_dict.get("fallback_total")) or 0)
            fh = int(to_float_or_none(row_dict.get("fallback_hit")) or 0)
            bucket["retrieval_total"] += rt
            bucket["retrieval_hit"] += rh
            bucket["consistency_total"] += ct
            bucket["consistency_hit"] += ch
            bucket["fallback_total"] += ft
            bucket["fallback_hit"] += fh
            fallback_total += ft
            fallback_hit += fh

    normalized_by_type: Dict[str, Dict[str, object]] = {}
    for mat_type, row in by_type.items():
        normalized_by_type[mat_type] = {
            "retrieval_total": row["retrieval_total"],
            "retrieval_hit": row["retrieval_hit"],
            "retrieval_hit_rate": _rate(row["retrieval_hit"], row["retrieval_total"]),
            "consistency_total": row["consistency_total"],
            "consistency_hit": row["consistency_hit"],
            "consistency_hit_rate": _rate(row["consistency_hit"], row["consistency_total"]),
            "fallback_total": row["fallback_total"],
            "fallback_hit": row["fallback_hit"],
            "fallback_hit_rate": _rate(row["fallback_hit"], row["fallback_total"]),
        }

    retrieval_unhit_filenames = sorted(retrieval_selected_filenames - retrieval_hit_filenames)

    return {
        "retrieval_total": retrieval_total,
        "retrieval_hit": retrieval_hit,
        "retrieval_hit_rate": _rate(retrieval_hit, retrieval_total),
        "retrieval_file_total": retrieval_file_total,
        "retrieval_file_hit": retrieval_file_hit,
        "retrieval_file_coverage_rate": _rate(retrieval_file_hit, retrieval_file_total),
        "retrieval_selected_filenames": sorted(retrieval_selected_filenames)[:120],
        "retrieval_hit_filenames": sorted(retrieval_hit_filenames)[:120],
        "retrieval_unhit_filenames": retrieval_unhit_filenames[:120],
        "retrieval_unhit_file_count": len(retrieval_unhit_filenames),
        "retrieval_top_k": retrieval_top_k,
        "retrieval_per_type_quota": retrieval_per_type_quota,
        "retrieval_per_file_quota": retrieval_per_file_quota,
        "retrieval_base_top_k": retrieval_base_top_k,
        "retrieval_base_per_type_quota": retrieval_base_per_type_quota,
        "retrieval_base_per_file_quota": retrieval_base_per_file_quota,
        "retrieval_budget_reasons": retrieval_budget_reasons[:12],
        "material_total_size_mb": round(material_total_size_mb, 3),
        "material_type_count": material_type_count,
        "material_file_count": material_file_count,
        "retrieval_selected_via_counts": retrieval_selected_via_counts,
        "retrieval_total_via_counts": retrieval_total_via_counts,
        "retrieval_hit_via_counts": retrieval_hit_via_counts,
        "consistency_total": consistency_total,
        "consistency_hit": consistency_hit,
        "consistency_hit_rate": _rate(consistency_hit, consistency_total),
        "fallback_total": fallback_total,
        "fallback_hit": fallback_hit,
        "fallback_hit_rate": _rate(fallback_hit, fallback_total),
        "query_terms_count": query_terms_count,
        "query_numeric_terms_count": query_numeric_terms_count,
        "by_type": normalized_by_type,
        "available_types": available_types,
        "uncovered_types": uncovered_types,
    }


def aggregate_material_utilization_gates(
    gates: List[Dict[str, object]],
    *,
    default_mode: str,
) -> Dict[str, object]:
    if not gates:
        return {
            "enabled": False,
            "mode": default_mode,
            "blocked_submissions": 0,
            "warn_submissions": 0,
            "pass_submissions": 0,
            "failed_submissions": 0,
            "failed_filenames": [],
        }
    enabled = any(bool(g.get("enabled")) for g in gates if isinstance(g, dict))
    mode = "block"
    if all(str(g.get("mode", "")).lower() == "warn" for g in gates if isinstance(g, dict)):
        mode = "warn"
    blocked_submissions = 0
    warn_submissions = 0
    pass_submissions = 0
    failed_submissions = 0
    failed_filenames: List[str] = []
    for gate in gates:
        if not isinstance(gate, dict):
            continue
        level = str(gate.get("level") or "").strip().lower()
        passed = bool(gate.get("passed", False))
        if passed:
            pass_submissions += 1
        else:
            failed_submissions += 1
        if level == "blocked":
            blocked_submissions += 1
        elif level == "warn":
            warn_submissions += 1
    return {
        "enabled": enabled,
        "mode": mode,
        "blocked_submissions": blocked_submissions,
        "warn_submissions": warn_submissions,
        "pass_submissions": pass_submissions,
        "failed_submissions": failed_submissions,
        "failed_filenames": failed_filenames,
    }


def prepare_rescore_batch(
    *,
    targets: List[Dict[str, object]],
    project_id: str,
    project: Dict[str, object],
    config: object,
    multipliers: Dict[str, float],
    profile_snapshot: Optional[Dict[str, object]],
    profile_for_meta: Optional[Dict[str, object]],
    scoring_engine_version: str,
    score_scale_max: int,
    score_scale_label: str,
    anchors: Optional[List[Dict[str, object]]],
    requirements: Optional[List[Dict[str, object]]],
    material_quality_snapshot: Dict[str, object],
    score_submission_for_project: Callable[..., tuple[Dict[str, object], List[Dict[str, object]]]],
    apply_evolution_total_scale: Callable[[str, Dict[str, object]], None],
    report_is_blocked: Callable[[Optional[Dict[str, object]]], bool],
    mark_report_scored: Callable[..., None],
    build_score_report_snapshot: Callable[..., Dict[str, object]],
    now_iso: Callable[[], str],
) -> Dict[str, object]:
    computed_updates: List[Dict[str, object]] = []
    material_utilization_summaries: List[Dict[str, object]] = []
    material_utilization_by_submission: List[Dict[str, object]] = []
    material_utilization_gates: List[Dict[str, object]] = []
    failed_gate_filenames: List[str] = []
    now = now_iso()

    for submission in targets:
        text = submission.get("text") or ""
        if not text.strip():
            continue
        report, evidence_units = score_submission_for_project(
            submission_id=str(submission.get("id")),
            text=text,
            project_id=project_id,
            project=project,
            config=config,
            multipliers=multipliers,
            profile_snapshot=profile_snapshot,
            scoring_engine_version=scoring_engine_version,
            anchors=anchors,
            requirements=requirements,
            material_quality_snapshot=material_quality_snapshot,
            evolution_total_scale_applied=True,
        )
        apply_evolution_total_scale(project_id, report)
        if not report_is_blocked(report):
            mark_report_scored(report, trigger="manual_rescore")
        report_meta = report.get("meta") if isinstance(report.get("meta"), dict) else {}
        report_meta = dict(report_meta or {})
        report_meta["score_scale_max"] = score_scale_max
        report_meta["score_scale_label"] = score_scale_label
        report["meta"] = report_meta

        material_utilization = report_meta.get("material_utilization")
        if isinstance(material_utilization, dict):
            material_utilization_summaries.append(material_utilization)
            material_utilization_gate = (
                report_meta.get("material_utilization_gate")
                if isinstance(report_meta.get("material_utilization_gate"), dict)
                else {}
            )
            detail_item: Dict[str, object] = {
                "submission_id": str(submission.get("id") or ""),
                "filename": str(submission.get("filename") or ""),
                "summary": material_utilization,
            }
            if material_utilization_gate:
                detail_item["gate"] = material_utilization_gate
                material_utilization_gates.append(material_utilization_gate)
                if not bool(material_utilization_gate.get("passed", True)):
                    filename_text = str(submission.get("filename") or "")
                    if filename_text and filename_text not in failed_gate_filenames:
                        failed_gate_filenames.append(filename_text)
            alerts = report_meta.get("material_utilization_alerts")
            if isinstance(alerts, list):
                detail_item["alerts"] = [str(item) for item in alerts[:6] if str(item).strip()]
            material_utilization_by_submission.append(detail_item)

        submission["report"] = report
        submission["total_score"] = float(
            report.get("total_score", report.get("rule_total_score", 0.0))
        )
        submission["updated_at"] = now
        submission["expert_profile_id_used"] = (
            profile_for_meta.get("id") if profile_for_meta else None
        )

        snapshot = build_score_report_snapshot(
            submission_id=str(submission.get("id")),
            project=project,
            report=report,
            profile_snapshot=profile_for_meta,
            scoring_engine_version=scoring_engine_version,
        )
        dimension_scores = {
            dim_id: dim.get("score", 0.0)
            for dim_id, dim in report.get("dimension_scores", {}).items()
        }
        penalty_count = len(report.get("penalties", []))
        history_args: Optional[Dict[str, object]] = None
        if not report_is_blocked(report):
            history_args = {
                "project_id": project_id,
                "submission_id": str(submission.get("id")),
                "filename": str(submission.get("filename", "")),
                "total_score": report.get("total_score", 0.0),
                "dimension_scores": dimension_scores,
                "penalty_count": penalty_count,
            }
        computed_updates.append(
            {
                "submission_id": str(submission.get("id")),
                "report": report,
                "total_score": submission["total_score"],
                "updated_at": now,
                "expert_profile_id_used": submission["expert_profile_id_used"],
                "snapshot": snapshot,
                "evidence_units": evidence_units,
                "history_args": history_args,
            }
        )

    return {
        "computed_updates": computed_updates,
        "material_utilization_summaries": material_utilization_summaries,
        "material_utilization_gates": material_utilization_gates,
        "failed_gate_filenames": failed_gate_filenames,
        "material_utilization_by_submission": material_utilization_by_submission,
    }


def commit_rescore_batch(
    *,
    project_id: str,
    project_patch: Dict[str, object],
    profile_created: bool,
    profile: Dict[str, object],
    computed_updates: List[Dict[str, object]],
    load_projects: Callable[[], List[Dict[str, object]]],
    find_latest_project: Callable[[str, List[Dict[str, object]]], Dict[str, object]],
    load_expert_profiles: Callable[[], List[Dict[str, object]]],
    save_expert_profiles: Callable[[List[Dict[str, object]]], None],
    load_submissions: Callable[[], List[Dict[str, object]]],
    save_submissions: Callable[[List[Dict[str, object]]], None],
    load_score_reports: Callable[[], List[Dict[str, object]]],
    save_score_reports: Callable[[List[Dict[str, object]]], None],
    load_evidence_units: Callable[[], List[Dict[str, object]]],
    save_evidence_units: Callable[[List[Dict[str, object]]], None],
    load_score_history: Callable[[], List[Dict[str, object]]],
    save_score_history: Callable[[List[Dict[str, object]]], None],
    save_projects: Callable[[List[Dict[str, object]]], None],
    record_history_score: Callable[..., object],
    replace_submission_evidence_units: Callable[..., List[Dict[str, object]]],
) -> set[str]:
    committed_ids: set[str] = set()
    originals = {
        "projects": copy.deepcopy(load_projects()),
        "expert_profiles": copy.deepcopy(load_expert_profiles()),
        "submissions": copy.deepcopy(load_submissions()),
        "score_reports": copy.deepcopy(load_score_reports()),
        "evidence_units": copy.deepcopy(load_evidence_units()),
        "score_history": copy.deepcopy(load_score_history()),
    }
    latest_projects = copy.deepcopy(originals["projects"])
    latest_project = find_latest_project(project_id, latest_projects)
    latest_project.update(copy.deepcopy(project_patch))

    latest_profiles = copy.deepcopy(originals["expert_profiles"])
    if profile_created:
        if not any(str(item.get("id")) == str(profile.get("id")) for item in latest_profiles):
            latest_profiles.append(copy.deepcopy(profile))

    latest_submissions = copy.deepcopy(originals["submissions"])
    submissions_by_id = {
        str(item.get("id")): item
        for item in latest_submissions
        if isinstance(item, dict) and str(item.get("id"))
    }
    latest_reports = copy.deepcopy(originals["score_reports"])
    latest_evidence_units = copy.deepcopy(originals["evidence_units"])
    history_args_list: List[Dict[str, object]] = []
    for item in computed_updates:
        submission_id = str(item["submission_id"])
        latest_submission = submissions_by_id.get(submission_id)
        if latest_submission is None:
            continue
        latest_submission["report"] = copy.deepcopy(item["report"])
        latest_submission["total_score"] = item["total_score"]
        latest_submission["updated_at"] = item["updated_at"]
        latest_submission["expert_profile_id_used"] = item["expert_profile_id_used"]
        latest_reports.append(copy.deepcopy(item["snapshot"]))
        latest_evidence_units = replace_submission_evidence_units(
            latest_evidence_units,
            submission_id=submission_id,
            new_units=copy.deepcopy(item["evidence_units"]),
        )
        committed_ids.add(submission_id)
        history_args = item.get("history_args")
        if isinstance(history_args, dict):
            history_args_list.append(copy.deepcopy(history_args))

    savers = {
        "projects": save_projects,
        "expert_profiles": save_expert_profiles,
        "submissions": save_submissions,
        "score_reports": save_score_reports,
        "evidence_units": save_evidence_units,
        "score_history": save_score_history,
    }
    attempted: List[str] = []

    try:
        if profile_created:
            attempted.append("expert_profiles")
            save_expert_profiles(latest_profiles)
        attempted.append("submissions")
        save_submissions(latest_submissions)
        attempted.append("score_reports")
        save_score_reports(latest_reports)
        attempted.append("evidence_units")
        save_evidence_units(latest_evidence_units)
        attempted.append("projects")
        save_projects(latest_projects)
        if history_args_list:
            attempted.append("score_history")
            for history_args in history_args_list:
                record_history_score(**history_args)
    except BaseException as error:
        for name in reversed(attempted):
            try:
                savers[name](copy.deepcopy(originals[name]))
            except BaseException as rollback_error:
                _append_rollback_note(error, rollback_error)
        raise

    return committed_ids


def summarize_rescore_material_utilization(
    *,
    prepared_batch: Dict[str, object],
    material_quality_snapshot: Dict[str, object],
    build_material_utilization_alerts: Callable[..., List[str]],
    to_float_or_none: Callable[[object], Optional[float]],
    normalize_material_type: Callable[[object], str],
    default_material_utilization_gate_mode: str,
) -> Dict[str, object]:
    material_utilization = aggregate_material_utilization_summaries(
        prepared_batch["material_utilization_summaries"],
        to_float_or_none=to_float_or_none,
        normalize_material_type=normalize_material_type,
    )
    material_gate = (
        material_quality_snapshot.get("gate")
        if isinstance(material_quality_snapshot, dict)
        and isinstance(material_quality_snapshot.get("gate"), dict)
        else {}
    )
    material_utilization_alerts = build_material_utilization_alerts(
        material_utilization,
        material_gate if isinstance(material_gate, dict) else {},
    )
    material_utilization_gate = aggregate_material_utilization_gates(
        prepared_batch["material_utilization_gates"],
        default_mode=default_material_utilization_gate_mode,
    )
    material_utilization_gate["failed_filenames"] = prepared_batch["failed_gate_filenames"]

    return {
        "material_utilization": material_utilization,
        "material_utilization_alerts": material_utilization_alerts,
        "material_utilization_gate": material_utilization_gate,
        "material_utilization_by_submission": prepared_batch["material_utilization_by_submission"],
    }
