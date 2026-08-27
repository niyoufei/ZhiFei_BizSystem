from __future__ import annotations

from typing import Any, Callable, Dict, List
from uuid import uuid4


def sync_ground_truth_record_to_qingtian(
    project_id: str,
    gt_record: Dict[str, object],
    *,
    default_qingtian_model_version: str,
    load_projects: Callable[..., Any],
    find_project: Callable[..., Any],
    load_config: Callable[..., Any],
    resolve_project_scoring_context: Callable[..., Any],
    load_submissions: Callable[..., Any],
    build_pending_submission_report: Callable[..., Any],
    now_iso: Callable[..., Any],
    submission_is_scored: Callable[..., Any],
    score_submission_for_project: Callable[..., Any],
    report_is_blocked: Callable[..., Any],
    mark_report_scored: Callable[..., Any],
    save_submissions: Callable[..., Any],
    load_score_reports: Callable[..., Any],
    build_score_report_snapshot: Callable[..., Any],
    save_score_reports: Callable[..., Any],
    load_evidence_units: Callable[..., Any],
    replace_submission_evidence_units: Callable[..., Any],
    save_evidence_units: Callable[..., Any],
    record_history_score: Callable[..., Any],
    load_qingtian_results: Callable[..., Any],
    resolve_project_score_scale_max: Callable[..., Any],
    ground_truth_record_for_learning: Callable[..., Any],
    auto_update_feature_confidence_on_ground_truth: Callable[..., Any],
    load_ground_truth: Callable[..., Any],
    save_ground_truth: Callable[..., Any],
    save_qingtian_results: Callable[..., Any],
    save_projects: Callable[..., Any],
    refresh_project_reflection_objects: Callable[..., Any],
) -> None:
    projects = load_projects()
    project = find_project(project_id, projects)
    config = load_config()
    multipliers, profile_snapshot, _ = resolve_project_scoring_context(project_id)
    scoring_engine_version = str(project.get("scoring_engine_version_locked") or "v1")
    source_gt_id = str(gt_record.get("id") or "")
    gt_text = str(gt_record.get("shigong_text") or "")

    submissions = load_submissions()
    matched_submission = None
    for submission in submissions:
        if str(submission.get("project_id")) != project_id:
            continue
        if str(submission.get("source_ground_truth_id") or "") == source_gt_id:
            matched_submission = submission
            break
        if str(submission.get("text") or "").strip() == gt_text.strip() and gt_text.strip():
            matched_submission = submission
            break

    scored_submission = False
    submission_changed = False
    evidence_units_new: List[Dict[str, object]] = []
    current_time = now_iso()
    if matched_submission is None:
        matched_submission = {
            "id": str(uuid4()),
            "project_id": project_id,
            "filename": f"ground_truth_{source_gt_id[:8]}.txt",
            "total_score": 0.0,
            "report": build_pending_submission_report(
                project=project,
                scoring_engine_version=scoring_engine_version,
            ),
            "text": gt_text,
            "created_at": current_time,
            "updated_at": current_time,
            "expert_profile_id_used": profile_snapshot.get("id") if profile_snapshot else None,
            "source_ground_truth_id": source_gt_id,
            "bidder_name": f"GT_{source_gt_id[:8]}",
        }
        submissions.append(matched_submission)
        submission_changed = True

    if str(matched_submission.get("source_ground_truth_id") or "") != source_gt_id:
        matched_submission["source_ground_truth_id"] = source_gt_id
        submission_changed = True
    if gt_text.strip() and str(matched_submission.get("text") or "").strip() != gt_text.strip():
        matched_submission["text"] = gt_text
        submission_changed = True

    if not submission_is_scored(matched_submission):
        report, evidence_units_new = score_submission_for_project(
            submission_id=str(matched_submission.get("id")),
            text=gt_text,
            project_id=project_id,
            project=project,
            config=config,
            multipliers=multipliers,
            profile_snapshot=profile_snapshot,
            scoring_engine_version=scoring_engine_version,
        )
        if not report_is_blocked(report):
            mark_report_scored(report, trigger="ground_truth_sync")
        matched_submission["report"] = report
        matched_submission["total_score"] = float(
            report.get("total_score", report.get("rule_total_score", 0.0))
        )
        matched_submission["expert_profile_id_used"] = (
            profile_snapshot.get("id") if profile_snapshot else None
        )
        matched_submission["updated_at"] = now_iso()
        scored_submission = True
        submission_changed = True

    if submission_changed:
        save_submissions(submissions)

    if scored_submission:
        snapshots = load_score_reports()
        snapshots.append(
            build_score_report_snapshot(
                submission_id=str(matched_submission.get("id")),
                project=project,
                report=matched_submission.get("report") or {},
                profile_snapshot=profile_snapshot,
                scoring_engine_version=scoring_engine_version,
            )
        )
        save_score_reports(snapshots)
        if evidence_units_new:
            all_units = load_evidence_units()
            all_units = replace_submission_evidence_units(
                all_units,
                submission_id=str(matched_submission.get("id")),
                new_units=evidence_units_new,
            )
            save_evidence_units(all_units)

        report = matched_submission.get("report") or {}
        dimension_scores = {
            dim_id: (dim.get("score", 0.0) if isinstance(dim, dict) else 0.0)
            for dim_id, dim in (report.get("dimension_scores") or {}).items()
        }
        penalty_count = len(report.get("penalties", []))
        if not report_is_blocked(report):
            record_history_score(
                project_id=project_id,
                submission_id=str(matched_submission.get("id")),
                filename=str(matched_submission.get("filename", "")),
                total_score=float(report.get("total_score", report.get("rule_total_score", 0.0))),
                dimension_scores=dimension_scores,
                penalty_count=penalty_count,
            )

    qt_results = load_qingtian_results()
    matched_qt = next(
        (
            result
            for result in qt_results
            if str((result.get("raw_payload") or {}).get("ground_truth_record_id") or "")
            == source_gt_id
        ),
        None,
    )
    project_score_scale = resolve_project_score_scale_max(project)
    gt_for_learning = ground_truth_record_for_learning(
        gt_record,
        default_score_scale_max=project_score_scale,
    )
    feature_confidence_update: Dict[str, object] = {
        "updated": 0,
        "retired": 0,
        "reason": "not_executed",
    }
    report_for_feedback = matched_submission.get("report")
    if isinstance(report_for_feedback, dict):
        try:
            feature_confidence_update = auto_update_feature_confidence_on_ground_truth(
                report=report_for_feedback,
                gt_record=gt_record,
                project_score_scale_max=project_score_scale,
            )
        except Exception as exc:
            feature_confidence_update = {
                "updated": 0,
                "retired": 0,
                "reason": "feature_confidence_update_error",
                "error": str(exc),
            }

    if source_gt_id:
        all_gt_records = load_ground_truth()
        changed_gt = False
        for row in all_gt_records:
            if str(row.get("id") or "") != source_gt_id:
                continue
            row["feature_confidence_update"] = feature_confidence_update
            row["updated_at"] = now_iso()
            changed_gt = True
            break
        if changed_gt:
            save_ground_truth(all_gt_records)

    if matched_qt is None:
        qt_results.append(
            {
                "id": str(uuid4()),
                "submission_id": str(matched_submission.get("id")),
                "qingtian_model_version": str(
                    project.get("qingtian_model_version") or default_qingtian_model_version
                ),
                "qt_total_score": float(gt_for_learning.get("final_score", 0.0)),
                "qt_dim_scores": None,
                "qt_reasons": [
                    {
                        "kind": "ground_truth",
                        "text": f"评委分: {gt_record.get('judge_scores')}",
                    }
                ],
                "raw_payload": {
                    "ground_truth_record_id": source_gt_id,
                    "source": gt_record.get("source"),
                    "judge_scores": gt_record.get("judge_scores"),
                    "final_score": gt_record.get("final_score"),
                    "final_score_raw": gt_for_learning.get("final_score_raw"),
                    "final_score_100": gt_for_learning.get("final_score"),
                    "score_scale_max": gt_for_learning.get("score_scale_max"),
                    "feature_confidence_update": feature_confidence_update,
                },
                "created_at": now_iso(),
            }
        )
        save_qingtian_results(qt_results)
    else:
        raw_payload = matched_qt.get("raw_payload")
        if not isinstance(raw_payload, dict):
            raw_payload = {}
        raw_payload["feature_confidence_update"] = feature_confidence_update
        matched_qt["raw_payload"] = raw_payload
        save_qingtian_results(qt_results)

    if str(project.get("status") or "") == "scoring_preparation":
        project["status"] = "submitted_to_qingtian"
        project["updated_at"] = now_iso()
        save_projects(projects)

    refresh_project_reflection_objects(project_id)
