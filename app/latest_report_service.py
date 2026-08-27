from __future__ import annotations

from typing import Callable, Dict, List, Optional

RecordLoader = Callable[[], List[Dict[str, object]]]


def get_latest_report_projection(
    *,
    submission_id: str,
    load_score_reports: RecordLoader,
    load_submissions: RecordLoader,
) -> Optional[Dict[str, object]]:
    """Build the latest-report read projection without owning HTTP concerns."""
    reports = [
        report
        for report in load_score_reports()
        if str(report.get("submission_id")) == submission_id
    ]
    reports.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)

    report_obj: Dict[str, object]
    if reports:
        latest = reports[0]
        report_obj = {
            "id": latest.get("id"),
            "submission_id": latest.get("submission_id"),
            "scoring_engine_version": latest.get("scoring_engine_version"),
            "rule_total_score": latest.get("rule_total_score"),
            "pred_total_score": latest.get("pred_total_score"),
            "llm_total_score": latest.get("llm_total_score"),
            "pred_confidence": latest.get("pred_confidence"),
            "score_blend": latest.get("score_blend"),
            "rule_dim_scores": latest.get("rule_dim_scores", {}),
            "pred_dim_scores": latest.get("pred_dim_scores"),
            "penalties": latest.get("penalties", []),
            "lint_findings": latest.get("lint_findings", []),
            "suggestions": latest.get("suggestions", []),
            "expert_profile_snapshot": latest.get("expert_profile_snapshot", {}),
            "created_at": latest.get("created_at"),
        }
    else:
        submission = next(
            (item for item in load_submissions() if str(item.get("id")) == submission_id),
            None,
        )
        if submission is None:
            return None
        report_obj = dict(submission.get("report") or {})
        if not report_obj:
            return None
        report_obj.setdefault("submission_id", submission_id)
        report_obj.setdefault("rule_total_score", report_obj.get("total_score", 0.0))
        report_obj.setdefault("pred_total_score", report_obj.get("pred_total_score"))
        report_obj.setdefault("llm_total_score", report_obj.get("llm_total_score"))
        report_obj.setdefault("pred_confidence", report_obj.get("pred_confidence"))
        report_obj.setdefault("score_blend", report_obj.get("score_blend"))
        report_obj.setdefault("rule_dim_scores", report_obj.get("rule_dim_scores", {}))
        report_obj.setdefault("penalties", report_obj.get("penalties", []))
        report_obj.setdefault("lint_findings", report_obj.get("lint_findings", []))
        report_obj.setdefault("suggestions", report_obj.get("suggestions", []))

    penalties = report_obj.get("penalties") or []
    lint_findings = report_obj.get("lint_findings") or []
    suggestions = report_obj.get("suggestions") or []

    top_conflicts = [
        penalty for penalty in penalties if str(penalty.get("code") or "") == "P-CONSIST-001"
    ][:10]
    top_missing_requirements = [
        finding
        for finding in lint_findings
        if str(finding.get("issue_code") or "") == "MissingRequirement"
    ][:10]

    ui_summary = {
        "pred_total_score": report_obj.get("pred_total_score"),
        "llm_total_score": report_obj.get("llm_total_score"),
        "pred_confidence": report_obj.get("pred_confidence"),
        "score_blend": report_obj.get("score_blend"),
        "rule_total_score": report_obj.get("rule_total_score", report_obj.get("total_score")),
        "top10_suggestions": suggestions[:10],
        "top_conflicts": top_conflicts,
        "top_missing_requirements": top_missing_requirements,
    }
    return {"report": report_obj, "ui_summary": ui_summary}
