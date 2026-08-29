from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Callable, Dict, List, Optional

TransactionDecorator = Callable[
    [Callable[[], Dict[str, object]]],
    Callable[[], Dict[str, object]],
]
TransactionFactory = Callable[..., TransactionDecorator]


class PreparedScoringInputError(Exception):
    pass


def _append_rollback_note(error: BaseException, rollback_error: BaseException) -> None:
    note = (
        "inline scoring rollback also failed: " f"{type(rollback_error).__name__}: {rollback_error}"
    )
    add_note = getattr(error, "add_note", None)
    if callable(add_note):
        add_note(note)
        return
    notes = list(getattr(error, "__notes__", []))
    notes.append(note)
    error.__notes__ = notes


def report_is_blocked(report: Optional[Dict[str, object]]) -> bool:
    if not isinstance(report, dict):
        return False
    status = str(report.get("scoring_status") or "").strip().lower()
    if status == "blocked":
        return True
    meta = report.get("meta") if isinstance(report.get("meta"), dict) else {}
    return bool(meta.get("score_blocked_by_material_utilization"))


def submission_is_scored(
    submission: Dict[str, object],
    *,
    to_float_or_none: Callable[[object], Optional[float]],
) -> bool:
    report_obj = submission.get("report")
    if isinstance(report_obj, dict):
        if report_is_blocked(report_obj):
            return False
        status = str(report_obj.get("scoring_status") or "").strip().lower()
        if status == "pending":
            return False
        if status == "scored":
            return True
        if to_float_or_none(report_obj.get("rule_total_score")) is not None:
            return True
        if to_float_or_none(report_obj.get("pred_total_score")) is not None:
            return True
        if to_float_or_none(report_obj.get("total_score")) is not None:
            return True
    return to_float_or_none(submission.get("total_score")) is not None


def mark_report_scored(
    report: Dict[str, object],
    *,
    trigger: str,
    now_iso: Callable[[], str],
) -> None:
    report["scoring_status"] = "scored"
    report["scoring_trigger"] = trigger
    report["scored_at"] = now_iso()


def build_pending_submission_report(
    *,
    project: Dict[str, object],
    scoring_engine_version: str,
    default_region: str,
    determine_engine_version: Callable[[Dict[str, object], str], str],
    now_iso: Callable[[], str],
) -> Dict[str, object]:
    return {
        "scoring_status": "pending",
        "scoring_trigger": "upload_only",
        "queued_at": now_iso(),
        "total_score": None,
        "rule_total_score": None,
        "pred_total_score": None,
        "llm_total_score": None,
        "pred_confidence": None,
        "score_blend": None,
        "dimension_scores": {},
        "rule_dim_scores": {},
        "pred_dim_scores": None,
        "penalties": [],
        "lint_findings": [],
        "suggestions": [],
        "requirement_hits": [],
        "mandatory_req_hit_rate": None,
        "evidence_units_count": 0,
        "meta": {
            "engine_version": determine_engine_version(project, scoring_engine_version),
            "region": project.get("region", default_region),
            "scoring_engine_version": scoring_engine_version,
            "queued_for_scoring": True,
        },
    }


def commit_submission_upload(
    *,
    project_id: str,
    normalized_filename: str,
    text: str,
    now_utc: object,
    record: Dict[str, object],
    atomic_json_transaction: TransactionFactory,
    load_projects: Callable[[], List[Dict[str, object]]],
    load_submissions: Callable[[], List[Dict[str, object]]],
    save_submissions: Callable[[List[Dict[str, object]]], None],
    find_recent_duplicate_submission: Callable[..., Optional[Dict[str, object]]],
    project_not_found_error: Callable[[], Exception],
) -> Dict[str, object]:
    @atomic_json_transaction("projects", "submissions")
    def commit() -> Dict[str, object]:
        if not any(str(project.get("id")) == project_id for project in load_projects()):
            raise project_not_found_error()
        submissions = load_submissions()
        duplicate = find_recent_duplicate_submission(
            submissions,
            project_id=project_id,
            filename=normalized_filename,
            text=text,
            now_utc=now_utc,
        )
        if duplicate is not None:
            return duplicate
        submissions.append(record)
        save_submissions(submissions)
        return record

    return commit()


def commit_inline_scoring_result(
    *,
    project_id: str,
    record: Dict[str, object],
    snapshot: Dict[str, object],
    evidence_units: List[Dict[str, object]],
    history_args: Dict[str, object],
    atomic_json_transaction: TransactionFactory,
    load_projects: Callable[[], List[Dict[str, object]]],
    load_submissions: Callable[[], List[Dict[str, object]]],
    save_submissions: Callable[[List[Dict[str, object]]], None],
    load_score_reports: Callable[[], List[Dict[str, object]]],
    save_score_reports: Callable[[List[Dict[str, object]]], None],
    load_evidence_units: Callable[[], List[Dict[str, object]]],
    save_evidence_units: Callable[[List[Dict[str, object]]], None],
    load_score_history: Callable[[], List[Dict[str, object]]],
    save_score_history: Callable[[List[Dict[str, object]]], None],
    record_history_score: Callable[..., object],
    replace_submission_evidence_units: Callable[..., List[Dict[str, object]]],
    project_not_found_error: Callable[[], Exception],
) -> None:
    @atomic_json_transaction(
        "evidence_units",
        "projects",
        "score_history",
        "score_reports",
        "submissions",
    )
    def commit() -> None:
        if not any(str(project.get("id")) == project_id for project in load_projects()):
            raise project_not_found_error()

        originals = {
            "submissions": deepcopy(load_submissions()),
            "score_reports": deepcopy(load_score_reports()),
            "evidence_units": deepcopy(load_evidence_units()),
            "score_history": deepcopy(load_score_history()),
        }
        savers = {
            "submissions": save_submissions,
            "score_reports": save_score_reports,
            "evidence_units": save_evidence_units,
            "score_history": save_score_history,
        }
        attempted: List[str] = []

        try:
            submissions = deepcopy(originals["submissions"])
            submissions.append(deepcopy(record))
            attempted.append("submissions")
            save_submissions(submissions)

            score_reports = deepcopy(originals["score_reports"])
            score_reports.append(deepcopy(snapshot))
            attempted.append("score_reports")
            save_score_reports(score_reports)

            if evidence_units:
                updated_units = replace_submission_evidence_units(
                    deepcopy(originals["evidence_units"]),
                    submission_id=str(record.get("id") or ""),
                    new_units=deepcopy(evidence_units),
                )
                attempted.append("evidence_units")
                save_evidence_units(updated_units)

            attempted.append("score_history")
            record_history_score(**history_args)
        except BaseException as error:
            for name in reversed(attempted):
                try:
                    savers[name](deepcopy(originals[name]))
                except BaseException as rollback_error:
                    _append_rollback_note(error, rollback_error)
            raise

    commit()


def delete_submission_cascade(
    *,
    project_id: str,
    submission_id: str,
    locale: str,
    ensure_data_dirs: Callable[[], None],
    load_projects: Callable[[], List[Dict[str, object]]],
    load_submissions: Callable[[], List[Dict[str, object]]],
    save_submissions: Callable[[List[Dict[str, object]]], None],
    load_score_reports: Callable[[], List[Dict[str, object]]],
    save_score_reports: Callable[[List[Dict[str, object]]], None],
    load_evidence_units: Callable[[], List[Dict[str, object]]],
    save_evidence_units: Callable[[List[Dict[str, object]]], None],
    load_qingtian_results: Callable[[], List[Dict[str, object]]],
    save_qingtian_results: Callable[[List[Dict[str, object]]], None],
    load_delta_cases: Callable[[], List[Dict[str, object]]],
    save_delta_cases: Callable[[List[Dict[str, object]]], None],
    load_calibration_samples: Callable[[], List[Dict[str, object]]],
    save_calibration_samples: Callable[[List[Dict[str, object]]], None],
    project_not_found_error: Callable[[], Exception],
    submission_not_found_error: Callable[[], Exception],
    run_feedback_closed_loop_safe: Callable[..., Dict[str, object]],
) -> None:
    ensure_data_dirs()
    projects = load_projects()
    if not any(project["id"] == project_id for project in projects):
        raise project_not_found_error()
    submissions = load_submissions()
    found = next(
        (
            submission
            for submission in submissions
            if submission.get("id") == submission_id and submission.get("project_id") == project_id
        ),
        None,
    )
    if not found:
        raise submission_not_found_error()
    raw_path = str(found.get("path") or "").strip()
    if raw_path:
        path = Path(raw_path)
        if path.exists():
            path.unlink()
    submissions = [
        submission
        for submission in submissions
        if not (
            submission.get("id") == submission_id and submission.get("project_id") == project_id
        )
    ]
    save_submissions(submissions)
    snapshots = load_score_reports()
    snapshots = [
        report
        for report in snapshots
        if not (
            report.get("submission_id") == submission_id and report.get("project_id") == project_id
        )
    ]
    save_score_reports(snapshots)
    evidence_units = load_evidence_units()
    evidence_units = [
        unit for unit in evidence_units if str(unit.get("submission_id")) != submission_id
    ]
    save_evidence_units(evidence_units)
    qingtian_results = load_qingtian_results()
    qingtian_results = [
        result for result in qingtian_results if str(result.get("submission_id")) != submission_id
    ]
    save_qingtian_results(qingtian_results)
    delta_cases = load_delta_cases()
    delta_cases = [case for case in delta_cases if str(case.get("submission_id")) != submission_id]
    save_delta_cases(delta_cases)
    calibration_samples = load_calibration_samples()
    calibration_samples = [
        sample
        for sample in calibration_samples
        if str(sample.get("submission_id")) != submission_id
    ]
    save_calibration_samples(calibration_samples)
    run_feedback_closed_loop_safe(
        project_id,
        locale=locale,
        trigger="delete_submission",
    )


def score_prepared_v2_submission(
    *,
    submission_id: str,
    text: str,
    project_id: str,
    project: Dict[str, object],
    config: object,
    weights_norm: Dict[str, float],
    profile_snapshot: Optional[Dict[str, object]],
    scoring_engine_version: str,
    anchors: List[Dict[str, object]],
    requirements: List[Dict[str, object]],
    runtime_custom_requirements: List[Dict[str, object]],
    runtime_req_meta: Dict[str, object],
    constraints_rebuilt: bool,
    material_quality_snapshot: Dict[str, object],
    material_required_types: List[str],
    material_utilization_policy: Dict[str, object],
    strict_pre_flight: bool,
    score_text_v2: Callable[..., Dict[str, object]],
    build_v2_report_payload: Callable[..., Dict[str, object]],
    build_scoring_input_injection_meta: Callable[..., Dict[str, object]],
    build_material_utilization_summary: Callable[..., Dict[str, object]],
    evaluate_material_utilization_gate: Callable[..., Dict[str, object]],
    build_material_utilization_alerts: Callable[..., List[str]],
    build_evidence_trace_summary: Callable[[Dict[str, object]], Dict[str, object]],
    to_float_or_none: Callable[[object], Optional[float]],
    now_iso: Callable[[], str],
) -> tuple[Dict[str, object], List[Dict[str, object]]]:
    effective_requirements = list(requirements) + list(runtime_custom_requirements)
    try:
        v2_result = score_text_v2(
            submission_id=submission_id,
            text=text,
            lexicon=config.lexicon,
            weights_norm=weights_norm,
            anchors=anchors,
            requirements=effective_requirements,
            strict_pre_flight=strict_pre_flight,
        )
    except ValueError as exc:
        raise PreparedScoringInputError(str(exc)) from exc
    report = build_v2_report_payload(
        v2_result,
        text=text,
        project=project,
        profile_snapshot=profile_snapshot,
        scoring_engine_version=scoring_engine_version,
    )
    snapshot_for_meta = dict(material_quality_snapshot)
    report_meta = report.get("meta") if isinstance(report.get("meta"), dict) else {}
    report_meta["input_injection"] = build_scoring_input_injection_meta(
        project_id=project_id,
        text=text,
        anchors_count=len(anchors),
        base_requirements_count=len(requirements),
        runtime_custom_requirements_count=len(runtime_custom_requirements),
        weights_norm=weights_norm,
        profile_snapshot=profile_snapshot,
        constraints_rebuilt=constraints_rebuilt,
        runtime_req_meta=runtime_req_meta,
        material_quality_snapshot=snapshot_for_meta,
    )
    report_meta["material_quality"] = snapshot_for_meta
    report_meta["material_retrieval"] = {
        "chunks": int(to_float_or_none(runtime_req_meta.get("material_retrieval_chunks")) or 0),
        "requirements": int(
            to_float_or_none(runtime_req_meta.get("material_retrieval_requirements")) or 0
        ),
        "preview": runtime_req_meta.get("material_retrieval_preview") or [],
        "consistency_requirements": int(
            to_float_or_none(runtime_req_meta.get("material_consistency_requirements")) or 0
        ),
        "consistency_preview": runtime_req_meta.get("material_consistency_preview") or [],
        "available_types": runtime_req_meta.get("material_available_types") or [],
        "retrieval_types": runtime_req_meta.get("material_retrieval_types") or [],
        "missing_types": runtime_req_meta.get("material_retrieval_missing_types") or [],
    }
    report_meta["material_utilization"] = build_material_utilization_summary(
        report,
        runtime_req_meta,
    )
    gate_obj = snapshot_for_meta.get("gate")
    utilization_gate = evaluate_material_utilization_gate(
        report_meta.get("material_utilization")
        if isinstance(report_meta.get("material_utilization"), dict)
        else {},
        policy=material_utilization_policy,
        required_types=material_required_types,
    )
    if isinstance(gate_obj, dict):
        report_meta["material_gate"] = gate_obj
    report_meta["material_utilization_gate"] = utilization_gate
    report_meta["material_utilization_alerts"] = build_material_utilization_alerts(
        report_meta.get("material_utilization")
        if isinstance(report_meta.get("material_utilization"), dict)
        else {},
        gate_obj if isinstance(gate_obj, dict) else {},
    )
    report_meta["evidence_trace"] = build_evidence_trace_summary(report)
    if bool(utilization_gate.get("blocked")):
        report_meta["score_confidence_level"] = "low"
        report_meta["score_blocked_by_material_utilization"] = True
        alerts = (
            report_meta.get("material_utilization_alerts")
            if isinstance(report_meta.get("material_utilization_alerts"), list)
            else []
        )
        for reason in utilization_gate.get("reasons") or []:
            reason_text = str(reason).strip()
            if reason_text and reason_text not in alerts:
                alerts.append("资料利用门禁：" + reason_text)
        report_meta["material_utilization_alerts"] = alerts[:8]
        report["scoring_status"] = "blocked"
        report["scoring_trigger"] = "material_utilization_gate"
        report["scored_at"] = now_iso()
    elif bool(utilization_gate.get("warned")):
        report_meta["score_confidence_level"] = "medium"
    else:
        report_meta["score_confidence_level"] = "high"
    report["meta"] = report_meta
    return report, list(v2_result.get("evidence_units") or [])


def score_prepared_legacy_submission(
    *,
    text: str,
    project: Dict[str, object],
    config: object,
    multipliers: Dict[str, float],
    profile_snapshot: Optional[Dict[str, object]],
    scoring_engine_version: str,
    default_region: str,
    score_text_legacy: Callable[..., object],
    rule_dim_scores_from_legacy: Callable[[Dict[str, object]], Dict[str, object]],
    build_evidence_trace_summary: Callable[[Dict[str, object]], Dict[str, object]],
) -> tuple[Dict[str, object], List[Dict[str, object]]]:
    legacy = score_text_legacy(
        text,
        config.rubric,
        config.lexicon,
        dimension_multipliers=multipliers,
    ).model_dump()
    legacy.setdefault("rule_total_score", float(legacy.get("total_score", 0.0)))
    legacy.setdefault(
        "rule_dim_scores", rule_dim_scores_from_legacy(legacy.get("dimension_scores", {}))
    )
    legacy.setdefault("pred_dim_scores", None)
    legacy.setdefault("pred_total_score", None)
    legacy.setdefault("pred_confidence", None)
    legacy.setdefault("lint_findings", [])
    legacy.setdefault("requirement_hits", [])
    legacy.setdefault("mandatory_req_hit_rate", None)
    legacy.setdefault("evidence_units_count", 0)
    legacy.setdefault("meta", {})
    legacy["meta"]["engine_version"] = "v1"
    legacy["meta"]["region"] = project.get("region", default_region)
    legacy["meta"]["scoring_engine_version"] = scoring_engine_version
    if profile_snapshot:
        legacy["meta"]["expert_profile_snapshot"] = profile_snapshot
        legacy["meta"]["expert_profile_id"] = profile_snapshot.get("id")
    legacy["meta"]["evidence_trace"] = build_evidence_trace_summary(legacy)
    return legacy, []
