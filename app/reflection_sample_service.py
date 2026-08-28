from __future__ import annotations

from typing import Callable, Dict, List, Tuple

Record = Dict[str, object]
LoadRecords = Callable[[], List[Record]]
SaveRecords = Callable[[List[Record]], None]


def refresh_project_reflection_objects(
    *,
    project_id: str,
    load_submissions: LoadRecords,
    load_score_reports: LoadRecords,
    latest_records_by_submission: Callable[[List[Record]], Dict[str, Record]],
    load_projects: LoadRecords,
    resolve_project_score_scale_max: Callable[[Record], object],
    load_qingtian_results: LoadRecords,
    ground_truth_record_for_learning: Callable[..., Record],
    to_float_or_none: Callable[[object], object],
    save_qingtian_results: SaveRecords,
    build_delta_cases: Callable[..., List[Record]],
    load_delta_cases: LoadRecords,
    save_delta_cases: SaveRecords,
    build_calibration_samples: Callable[..., List[Record]],
    load_calibration_samples: LoadRecords,
    save_calibration_samples: SaveRecords,
) -> Tuple[List[Record], List[Record]]:
    submissions = [s for s in load_submissions() if str(s.get("project_id")) == project_id]
    submissions_by_id = {str(s.get("id")): s for s in submissions}
    latest_reports = latest_records_by_submission(
        [r for r in load_score_reports() if str(r.get("project_id")) == project_id]
    )
    projects = load_projects()
    project = next((p for p in projects if str(p.get("id")) == project_id), {})
    project_score_scale = resolve_project_score_scale_max(project) if project else 100

    qingtian_results = load_qingtian_results()
    qingtian_changed = False
    scoped_qt: List[Record] = []
    for qingtian_result in qingtian_results:
        submission_id = str(qingtian_result.get("submission_id") or "")
        if submission_id not in submissions_by_id:
            continue
        raw_payload = (
            qingtian_result.get("raw_payload")
            if isinstance(qingtian_result.get("raw_payload"), dict)
            else {}
        )
        normalized_record = ground_truth_record_for_learning(
            {
                "final_score": raw_payload.get("final_score"),
                "final_score_raw": raw_payload.get("final_score_raw"),
                "final_score_100": raw_payload.get("final_score_100"),
                "score_scale_max": raw_payload.get("score_scale_max"),
                "judge_scores": raw_payload.get("judge_scores") or [],
            },
            default_score_scale_max=project_score_scale,
        )
        normalized_qt_score = float(normalized_record.get("final_score", 0.0))
        old_qt_score = to_float_or_none(qingtian_result.get("qt_total_score"))
        if old_qt_score is None or abs(float(old_qt_score) - normalized_qt_score) > 1e-6:
            qingtian_result["qt_total_score"] = normalized_qt_score
            qingtian_changed = True
        merged_payload = dict(raw_payload or {})
        merged_payload["final_score_raw"] = normalized_record.get("final_score_raw")
        merged_payload["final_score_100"] = normalized_qt_score
        merged_payload["score_scale_max"] = normalized_record.get("score_scale_max")
        if merged_payload != raw_payload:
            qingtian_result["raw_payload"] = merged_payload
            qingtian_changed = True
        scoped_qt.append(qingtian_result)
    if qingtian_changed:
        save_qingtian_results(qingtian_results)

    latest_qingtian = latest_records_by_submission(scoped_qt)
    delta_cases = build_delta_cases(
        project_id=project_id,
        latest_reports_by_submission=latest_reports,
        latest_qingtian_by_submission=latest_qingtian,
    )
    all_delta_cases = [
        row for row in load_delta_cases() if str(row.get("project_id")) != project_id
    ]
    all_delta_cases.extend(delta_cases)
    save_delta_cases(all_delta_cases)

    calibration_samples = build_calibration_samples(
        project_id=project_id,
        latest_reports_by_submission=latest_reports,
        latest_qingtian_by_submission=latest_qingtian,
        submissions_by_id=submissions_by_id,
    )
    all_calibration_samples = [
        row for row in load_calibration_samples() if str(row.get("project_id")) != project_id
    ]
    all_calibration_samples.extend(calibration_samples)
    save_calibration_samples(all_calibration_samples)
    return delta_cases, calibration_samples


def rebuild_project_delta_cases(
    *,
    project_id: str,
    load_score_reports: LoadRecords,
    latest_records_by_submission: Callable[[List[Record]], Dict[str, Record]],
    load_qingtian_results: LoadRecords,
    build_delta_cases: Callable[..., List[Record]],
    load_delta_cases: LoadRecords,
    save_delta_cases: SaveRecords,
) -> List[Record]:
    reports = [r for r in load_score_reports() if str(r.get("project_id")) == project_id]
    latest_reports = latest_records_by_submission(reports)
    latest_qingtian = latest_records_by_submission(
        [
            qingtian_result
            for qingtian_result in load_qingtian_results()
            if str(qingtian_result.get("submission_id")) in latest_reports
        ]
    )
    new_cases = build_delta_cases(
        project_id=project_id,
        latest_reports_by_submission=latest_reports,
        latest_qingtian_by_submission=latest_qingtian,
    )
    all_cases = [row for row in load_delta_cases() if str(row.get("project_id")) != project_id]
    all_cases.extend(new_cases)
    save_delta_cases(all_cases)
    return new_cases


def rebuild_project_calibration_samples(
    *,
    project_id: str,
    load_submissions: LoadRecords,
    load_score_reports: LoadRecords,
    latest_records_by_submission: Callable[[List[Record]], Dict[str, Record]],
    load_qingtian_results: LoadRecords,
    build_calibration_samples: Callable[..., List[Record]],
    load_calibration_samples: LoadRecords,
    save_calibration_samples: SaveRecords,
) -> List[Record]:
    submissions = [s for s in load_submissions() if str(s.get("project_id")) == project_id]
    submissions_by_id = {str(s.get("id")): s for s in submissions}
    latest_reports = latest_records_by_submission(
        [r for r in load_score_reports() if str(r.get("project_id")) == project_id]
    )
    latest_qingtian = latest_records_by_submission(
        [
            qingtian_result
            for qingtian_result in load_qingtian_results()
            if str(qingtian_result.get("submission_id")) in submissions_by_id
        ]
    )
    samples = build_calibration_samples(
        project_id=project_id,
        latest_reports_by_submission=latest_reports,
        latest_qingtian_by_submission=latest_qingtian,
        submissions_by_id=submissions_by_id,
    )
    all_samples = [
        row for row in load_calibration_samples() if str(row.get("project_id")) != project_id
    ]
    all_samples.extend(samples)
    save_calibration_samples(all_samples)
    return samples
