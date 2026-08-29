from __future__ import annotations

from copy import deepcopy
from typing import Callable, Dict, List

Record = Dict[str, object]
Records = List[Record]
Loader = Callable[[], Records]
Saver = Callable[[Records], None]
Refresh = Callable[[str], object]


def _is_synthetic_ground_truth_submission(
    submission: Record,
    *,
    project_id: str,
    ground_truth_id: str,
) -> bool:
    if (
        str(submission.get("project_id") or "") != project_id
        or str(submission.get("source_ground_truth_id") or "") != ground_truth_id
    ):
        return False
    if submission.get("ground_truth_generated") is True:
        return True
    suffix = ground_truth_id[:8]
    return (
        str(submission.get("filename") or "") == f"ground_truth_{suffix}.txt"
        and str(submission.get("bidder_name") or "") == f"GT_{suffix}"
    )


def _linked_ground_truth_id(result: Record) -> str:
    raw_payload = result.get("raw_payload")
    if not isinstance(raw_payload, dict):
        return ""
    return str(raw_payload.get("ground_truth_record_id") or "")


def _append_rollback_note(error: BaseException, rollback_error: BaseException) -> None:
    note = (
        "ground-truth delete rollback also failed: "
        f"{type(rollback_error).__name__}: {rollback_error}"
    )
    add_note = getattr(error, "add_note", None)
    if callable(add_note):
        add_note(note)
        return
    notes = list(getattr(error, "__notes__", []))
    notes.append(note)
    error.__notes__ = notes


def delete_ground_truth_cascade(
    project_id: str,
    record_id: str,
    *,
    load_ground_truth: Loader,
    save_ground_truth: Saver,
    load_qingtian_results: Loader,
    save_qingtian_results: Saver,
    load_submissions: Loader,
    save_submissions: Saver,
    load_score_reports: Loader,
    save_score_reports: Saver,
    load_evidence_units: Loader,
    save_evidence_units: Saver,
    load_score_history: Loader,
    save_score_history: Saver,
    load_calibration_samples: Loader,
    save_calibration_samples: Saver,
    load_delta_cases: Loader,
    save_delta_cases: Saver,
    refresh_project_reflection_objects: Refresh,
) -> bool:
    ground_truth = deepcopy(load_ground_truth())
    target = next(
        (
            row
            for row in ground_truth
            if str(row.get("id") or "") == record_id
            and str(row.get("project_id") or "") == project_id
        ),
        None,
    )
    if target is None:
        return False

    originals = {
        "ground_truth": ground_truth,
        "qingtian_results": deepcopy(load_qingtian_results()),
        "submissions": deepcopy(load_submissions()),
        "score_reports": deepcopy(load_score_reports()),
        "evidence_units": deepcopy(load_evidence_units()),
        "score_history": deepcopy(load_score_history()),
        "calibration_samples": deepcopy(load_calibration_samples()),
        "delta_cases": deepcopy(load_delta_cases()),
    }
    synthetic_submission_ids = {
        str(row.get("id") or "")
        for row in originals["submissions"]
        if _is_synthetic_ground_truth_submission(
            row,
            project_id=project_id,
            ground_truth_id=record_id,
        )
        and str(row.get("id") or "")
    }
    target_project_submission_ids = {
        str(row.get("id") or "")
        for row in originals["submissions"]
        if str(row.get("project_id") or "") == project_id and str(row.get("id") or "")
    }

    submissions: Records = []
    for original in originals["submissions"]:
        if str(original.get("id") or "") in synthetic_submission_ids:
            continue
        row = deepcopy(original)
        if (
            str(row.get("project_id") or "") == project_id
            and str(row.get("source_ground_truth_id") or "") == record_id
        ):
            row.pop("source_ground_truth_id", None)
        submissions.append(row)

    updates = {
        "ground_truth": [
            row
            for row in originals["ground_truth"]
            if not (
                str(row.get("id") or "") == record_id
                and str(row.get("project_id") or "") == project_id
            )
        ],
        "qingtian_results": [
            row
            for row in originals["qingtian_results"]
            if not (
                _linked_ground_truth_id(row) == record_id
                and str(row.get("submission_id") or "") in target_project_submission_ids
            )
        ],
        "submissions": submissions,
        "score_reports": [
            row
            for row in originals["score_reports"]
            if str(row.get("submission_id") or "") not in synthetic_submission_ids
        ],
        "evidence_units": [
            row
            for row in originals["evidence_units"]
            if str(row.get("submission_id") or "") not in synthetic_submission_ids
        ],
        "score_history": [
            row
            for row in originals["score_history"]
            if str(row.get("submission_id") or "") not in synthetic_submission_ids
        ],
    }
    savers = {
        "ground_truth": save_ground_truth,
        "qingtian_results": save_qingtian_results,
        "submissions": save_submissions,
        "score_reports": save_score_reports,
        "evidence_units": save_evidence_units,
        "score_history": save_score_history,
        "calibration_samples": save_calibration_samples,
        "delta_cases": save_delta_cases,
    }
    attempted: list[str] = []

    def mark_attempted(name: str) -> None:
        if name not in attempted:
            attempted.append(name)

    def save_if_changed(name: str) -> None:
        if updates[name] == originals[name]:
            return
        mark_attempted(name)
        savers[name](deepcopy(updates[name]))

    try:
        for name in (
            "qingtian_results",
            "submissions",
            "score_reports",
            "evidence_units",
            "score_history",
            "ground_truth",
        ):
            save_if_changed(name)
        for name in ("qingtian_results", "calibration_samples", "delta_cases"):
            mark_attempted(name)
        refresh_project_reflection_objects(project_id)
    except BaseException as error:
        for name in reversed(attempted):
            try:
                savers[name](deepcopy(originals[name]))
            except BaseException as rollback_error:
                _append_rollback_note(error, rollback_error)
        raise

    return True
