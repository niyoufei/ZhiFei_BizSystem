from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

Record = Dict[str, object]
Records = List[Record]
Callback = Callable[..., Any]
TransactionDecorator = Callable[[Callable[[], Any]], Callable[[], Any]]
TransactionFactory = Callable[..., TransactionDecorator]


class UnsupportedCalibrationModelTypeError(ValueError):
    pass


class CalibrationTrainingError(ValueError):
    pass


class CalibrationModelNotFoundError(LookupError):
    pass


class CalibrationProjectBindingError(ValueError):
    pass


def _append_rollback_note(error: BaseException, rollback_error: BaseException) -> None:
    note = (
        "calibration lifecycle rollback also failed: "
        f"{type(rollback_error).__name__}: {rollback_error}"
    )
    add_note = getattr(error, "add_note", None)
    if callable(add_note):
        add_note(note)
        return
    notes = list(getattr(error, "__notes__", []))
    notes.append(note)
    error.__notes__ = notes


def _tracked_saver(
    name: str,
    saver: Callable[[Records], None],
    attempted: List[str],
) -> Callable[[Records], None]:
    def save(rows: Records) -> None:
        if name not in attempted:
            attempted.append(name)
        saver(rows)

    return save


def _restore_attempted(
    error: BaseException,
    *,
    attempted: List[str],
    originals: Dict[str, Records],
    savers: Dict[str, Callable[[Records], None]],
) -> None:
    for name in reversed(attempted):
        try:
            savers[name](deepcopy(originals[name]))
        except BaseException as rollback_error:
            _append_rollback_note(error, rollback_error)


def select_calibrator_model(
    project: Record,
    *,
    load_calibration_models: Callable[[], Records],
) -> Optional[Record]:
    models = sorted(
        load_calibration_models(),
        key=lambda item: str(item.get("created_at", "")),
        reverse=True,
    )
    if not models:
        return None
    project_id = str(project.get("id") or "")
    locked_version = str(project.get("calibrator_version_locked") or "")

    def scope_project_id(model: Record) -> str:
        return str(((model.get("train_filter") or {}).get("project_id") or "")).strip()

    def compatible(model: Record) -> bool:
        return scope_project_id(model) == project_id

    if locked_version:
        for model in models:
            if str(model.get("calibrator_version") or "") == locked_version:
                return model if compatible(model) else None
        return None

    for model in models:
        if bool(model.get("deployed")) and compatible(model):
            return model
    return None


def apply_prediction_to_report(
    report: Record,
    *,
    submission_like: Record,
    project: Record,
    load_calibration_models: Callable[[], Records],
    build_feature_row: Callback,
    predict_with_model: Callback,
    fuse_rule_and_llm_scores: Callback,
    to_float_or_none: Callback,
    clip_score: Callback,
) -> Optional[str]:
    model = select_calibrator_model(
        project,
        load_calibration_models=load_calibration_models,
    )
    if not model:
        report["pred_total_score"] = None
        report["llm_total_score"] = None
        report["pred_confidence"] = None
        report["pred_dim_scores"] = None
        report["score_blend"] = None
        report["total_score"] = float(
            report.get("rule_total_score", report.get("total_score", 0.0))
        )
        submission_like["total_score"] = float(report.get("total_score", 0.0))
        return None

    artifact = model.get("model_artifact") or model.get("artifact") or {}
    if not isinstance(artifact, dict):
        report["pred_total_score"] = None
        report["llm_total_score"] = None
        report["pred_confidence"] = None
        report["pred_dim_scores"] = None
        report["score_blend"] = None
        report["total_score"] = float(
            report.get("rule_total_score", report.get("total_score", 0.0))
        )
        submission_like["total_score"] = float(report.get("total_score", 0.0))
        return None

    row = build_feature_row(report, submission=submission_like)
    try:
        pred, conf = predict_with_model(artifact, row.get("x_features") or {})
    except Exception as exc:
        report["pred_total_score"] = None
        report["llm_total_score"] = None
        report["pred_confidence"] = None
        report["pred_dim_scores"] = None
        report["score_blend"] = None
        report["total_score"] = float(
            report.get("rule_total_score", report.get("total_score", 0.0))
        )
        submission_like["total_score"] = float(report.get("total_score", 0.0))
        report.setdefault("meta", {})
        report["meta"]["calibrator_version"] = model.get("calibrator_version")
        report["meta"]["calibrator_error"] = f"{type(exc).__name__}: {exc}"
        return str(model.get("calibrator_version") or "")

    rule_total = float(report.get("rule_total_score", report.get("total_score", 0.0)))
    fused_total, llm_total, blend_info = fuse_rule_and_llm_scores(
        rule_total=rule_total,
        llm_total_raw=float(pred),
        project=project,
        report=report,
    )
    sigma = float(to_float_or_none(conf.get("sigma")) or 0.0)
    ci95_delta = 1.96 * sigma if sigma > 0 else 0.0
    ci95_lower = clip_score(fused_total - ci95_delta)
    ci95_upper = clip_score(fused_total + ci95_delta)
    report["pred_total_score"] = fused_total
    report["llm_total_score"] = llm_total
    report["pred_confidence"] = {
        **conf,
        "raw_llm_score": float(pred),
        "bounded_llm_score": llm_total,
        "fused_ci95_lower": round(ci95_lower, 2),
        "fused_ci95_upper": round(ci95_upper, 2),
        "fused_sigma": round(sigma, 2),
    }
    report["score_blend"] = blend_info
    report["pred_dim_scores"] = None
    report["total_score"] = float(fused_total)
    submission_like["total_score"] = float(fused_total)
    report.setdefault("meta", {})
    report["meta"]["calibrator_version"] = model.get("calibrator_version")
    return str(model.get("calibrator_version") or "")


def extract_auto_candidates(model_artifact: Dict[str, Any]) -> List[Dict[str, Any]]:
    best_selection = model_artifact.get("best_selection") or {}
    raw_candidates = best_selection.get("candidates") or []
    if not isinstance(raw_candidates, list):
        return []
    normalized: List[Dict[str, Any]] = []
    for item in raw_candidates:
        if not isinstance(item, dict):
            continue
        metrics = item.get("metrics") or {}
        cv_item = item.get("cv") or {}
        normalized.append(
            {
                "model_type": str(item.get("model_type") or ""),
                "ok": bool(item.get("ok")),
                "gate_passed": bool(item.get("gate_passed")),
                "cv_mae": metrics.get("cv_mae"),
                "cv_rmse": metrics.get("cv_rmse"),
                "cv_spearman": metrics.get("cv_spearman"),
                "cv_mode": cv_item.get("mode"),
                "cv_pred_count": cv_item.get("pred_count"),
            }
        )
    return normalized


def build_calibrator_summary(
    *,
    model_type: Optional[str],
    calibrator_version: Optional[str],
    gate_passed: Optional[bool],
    cv_metrics: Optional[Dict[str, Any]] = None,
    baseline_metrics: Optional[Dict[str, Any]] = None,
    improve_threshold: Optional[float] = None,
    spearman_tolerance: Optional[float] = None,
    auto_candidates: Optional[List[Dict[str, Any]]] = None,
    sample_count: Optional[int] = None,
    skipped_reason: Optional[str] = None,
) -> Dict[str, Any]:
    gate_payload: Dict[str, Any] = {}
    if gate_passed is not None:
        gate_payload["passed"] = bool(gate_passed)
    if improve_threshold is not None:
        gate_payload["improve_threshold"] = round(float(improve_threshold), 4)
    if spearman_tolerance is not None:
        gate_payload["spearman_tolerance"] = float(spearman_tolerance)

    summary: Dict[str, Any] = {
        "calibrator_version": calibrator_version,
        "model_type": model_type,
        "gate_passed": gate_passed,
        "cv_metrics": cv_metrics or {},
        "baseline_metrics": baseline_metrics or {},
        "gate": gate_payload,
        "auto_candidates": auto_candidates or [],
    }
    if sample_count is not None:
        summary["sample_count"] = int(sample_count)
    if skipped_reason:
        summary["skipped_reason"] = skipped_reason
    return summary


def _train_calibration_model(
    *,
    project_id: Optional[str],
    model_type_raw: object,
    alpha: float,
    auto_deploy: bool,
    load_calibration_samples: Callable[[], Records],
    load_submissions: Callable[[], Records],
    load_score_reports: Callable[[], Records],
    latest_records_by_submission: Callback,
    load_qingtian_results: Callable[[], Records],
    build_calibration_samples: Callback,
    save_calibration_samples: Callable[[Records], None],
    train_best_calibrator_auto: Callback,
    train_offset_calibrator: Callback,
    train_linear1d_calibrator: Callback,
    train_isotonic1d_calibrator: Callback,
    train_ridge_calibrator: Callback,
    cross_validate_calibrator: Callback,
    calc_metrics: Callback,
    load_calibration_models: Callable[[], Records],
    load_projects: Callable[[], Records],
    save_projects: Callable[[Records], None],
    save_calibration_models: Callable[[Records], None],
    now_iso: Callable[[], str],
) -> Record:
    model_type = str(model_type_raw or "ridge").lower().strip()
    if model_type not in {"auto", "ridge", "offset", "linear1d", "isotonic1d"}:
        raise UnsupportedCalibrationModelTypeError(
            "model_type 仅支持 auto/ridge/offset/linear1d/isotonic1d"
        )

    stored_samples = load_calibration_samples()
    if project_id:
        stored_samples = [
            sample for sample in stored_samples if str(sample.get("project_id")) == project_id
        ]

    feature_rows: List[Record] = [
        {
            "feature_schema_version": sample.get("feature_schema_version", "v2"),
            "x_features": sample.get("x_features") or {},
            "y_label": sample.get("y_label"),
            "submission_id": sample.get("submission_id"),
        }
        for sample in stored_samples
    ]

    if len(feature_rows) < 3:
        submissions = load_submissions()
        if project_id:
            submissions = [
                submission
                for submission in submissions
                if str(submission.get("project_id")) == project_id
            ]
        submission_map = {str(submission.get("id")): submission for submission in submissions}

        reports = load_score_reports()
        if project_id:
            reports = [report for report in reports if str(report.get("project_id")) == project_id]
        latest_reports = latest_records_by_submission(reports)
        latest_qingtian = latest_records_by_submission(load_qingtian_results())
        rebuilt_samples = build_calibration_samples(
            project_id=str(project_id or "__all__"),
            latest_reports_by_submission=latest_reports,
            latest_qingtian_by_submission=latest_qingtian,
            submissions_by_id=submission_map,
        )
        if rebuilt_samples:
            saved = load_calibration_samples()
            for row in rebuilt_samples:
                submission_id = str(row.get("submission_id"))
                saved = [item for item in saved if str(item.get("submission_id")) != submission_id]
                saved.append(row)
            save_calibration_samples(saved)
            feature_rows = [
                {
                    "feature_schema_version": sample.get("feature_schema_version", "v2"),
                    "x_features": sample.get("x_features") or {},
                    "y_label": sample.get("y_label"),
                    "submission_id": sample.get("submission_id"),
                }
                for sample in rebuilt_samples
            ]

    try:
        if model_type == "auto":
            model_artifact = train_best_calibrator_auto(feature_rows, alpha=float(alpha))
        elif model_type == "offset":
            model_artifact = train_offset_calibrator(feature_rows)
        elif model_type == "linear1d":
            model_artifact = train_linear1d_calibrator(feature_rows, alpha=float(alpha))
        elif model_type == "isotonic1d":
            model_artifact = train_isotonic1d_calibrator(feature_rows)
        else:
            model_artifact = train_ridge_calibrator(feature_rows, alpha=float(alpha))
    except ValueError as exc:
        raise CalibrationTrainingError(str(exc)) from exc

    selected_type = str(model_artifact.get("model_type") or model_type or "ridge")
    version_prefix = f"calib_{'auto_' if model_type == 'auto' else ''}{selected_type}"
    cv = cross_validate_calibrator(
        model_type=selected_type,
        feature_rows=feature_rows,
        alpha=float(alpha),
        seed=42,
    )
    y_true = [float(row.get("y_label")) for row in feature_rows if row.get("y_label") is not None]
    baseline_pred = [
        max(
            0.0,
            min(
                100.0,
                float(((row.get("x_features") or {}).get("rule_total_score") or 0.0)),
            ),
        )
        for row in feature_rows
        if row.get("y_label") is not None
    ]
    baseline_metrics = calc_metrics(y_true, baseline_pred)
    cv_metrics = (
        (cv.get("metrics") or {})
        if bool(cv.get("ok"))
        else {"mae": 0.0, "rmse": 0.0, "spearman": 0.0}
    )
    improve_threshold = max(0.2, float(baseline_metrics.get("mae") or 0.0) * 0.01)
    spearman_tolerance = 0.02
    gate_passed = (
        bool(cv.get("ok"))
        and float(cv_metrics.get("mae") or 0.0)
        <= float(baseline_metrics.get("mae") or 0.0) - improve_threshold
        and float(cv_metrics.get("spearman") or 0.0)
        >= float(baseline_metrics.get("spearman") or 0.0) - spearman_tolerance
    )
    model_artifact.setdefault("metrics", {})
    model_artifact["metrics"]["cv_mae"] = cv_metrics.get("mae")
    model_artifact["metrics"]["cv_rmse"] = cv_metrics.get("rmse")
    model_artifact["metrics"]["cv_spearman"] = cv_metrics.get("spearman")
    model_artifact["metrics"]["cv_mode"] = cv.get("mode")
    model_artifact["metrics"]["cv_pred_count"] = cv.get("pred_count")
    model_artifact["metrics"]["baseline_mae"] = baseline_metrics.get("mae")
    model_artifact["metrics"]["baseline_rmse"] = baseline_metrics.get("rmse")
    model_artifact["metrics"]["baseline_spearman"] = baseline_metrics.get("spearman")
    model_artifact["metrics"]["gate_improve_threshold"] = round(improve_threshold, 4)
    model_artifact["metrics"]["gate_spearman_tolerance"] = spearman_tolerance
    model_artifact["gate_passed"] = gate_passed

    auto_candidates = extract_auto_candidates(model_artifact)
    version = f"{version_prefix}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    calibrator_summary = build_calibrator_summary(
        model_type=selected_type,
        calibrator_version=version,
        gate_passed=bool(gate_passed),
        cv_metrics={
            "mae": cv_metrics.get("mae"),
            "rmse": cv_metrics.get("rmse"),
            "spearman": cv_metrics.get("spearman"),
            "mode": cv.get("mode"),
            "pred_count": cv.get("pred_count"),
        },
        baseline_metrics={
            "mae": baseline_metrics.get("mae"),
            "rmse": baseline_metrics.get("rmse"),
            "spearman": baseline_metrics.get("spearman"),
        },
        improve_threshold=improve_threshold,
        spearman_tolerance=spearman_tolerance,
        auto_candidates=auto_candidates,
        sample_count=len(feature_rows),
    )
    record: Record = {
        "calibrator_version": version,
        "model_type": selected_type,
        "feature_schema_version": str(model_artifact.get("feature_schema_version", "v2")),
        "train_filter": {"project_id": project_id},
        "metrics": {
            **(model_artifact.get("metrics") or {}),
            "gate_passed": bool(gate_passed),
        },
        "calibrator_summary": calibrator_summary,
        "artifact_uri": f"json://calibration_models/{version}",
        "model_artifact": model_artifact,
        "deployed": False,
        "created_at": now_iso(),
    }

    models = load_calibration_models()
    train_scope_project_id = str(project_id or "").strip()
    if auto_deploy and bool(gate_passed) and train_scope_project_id:
        for model in models:
            if (
                str(((model.get("train_filter") or {}).get("project_id") or ""))
                == train_scope_project_id
            ):
                model["deployed"] = False
        record["deployed"] = True
        projects = load_projects()
        for project in projects:
            if str(project.get("id")) == train_scope_project_id:
                project["calibrator_version_locked"] = version
                project["updated_at"] = now_iso()
        save_projects(projects)
    models.append(record)
    save_calibration_models(models)
    return record


def train_calibration_model(
    *,
    atomic_json_transaction: TransactionFactory,
    **kwargs: Any,
) -> Record:
    @atomic_json_transaction(
        "calibration_models",
        "calibration_samples",
        "projects",
        "qingtian_results",
        "score_reports",
        "submissions",
    )
    def commit() -> Record:
        originals = {
            "calibration_models": deepcopy(kwargs["load_calibration_models"]()),
            "calibration_samples": deepcopy(kwargs["load_calibration_samples"]()),
            "projects": deepcopy(kwargs["load_projects"]()),
        }
        savers = {
            "calibration_models": kwargs["save_calibration_models"],
            "calibration_samples": kwargs["save_calibration_samples"],
            "projects": kwargs["save_projects"],
        }
        attempted: List[str] = []
        operation_kwargs = dict(kwargs)
        for name in savers:
            operation_kwargs[f"save_{name}"] = _tracked_saver(
                name,
                savers[name],
                attempted,
            )
        try:
            return _train_calibration_model(**operation_kwargs)
        except BaseException as error:
            _restore_attempted(
                error,
                attempted=attempted,
                originals=originals,
                savers=savers,
            )
            raise

    return commit()


def _deploy_calibration_model(
    *,
    calibrator_version: str,
    project_id: Optional[str],
    load_calibration_models: Callable[[], Records],
    save_calibration_models: Callable[[Records], None],
    load_projects: Callable[[], Records],
    save_projects: Callable[[Records], None],
    now_iso: Callable[[], str],
) -> Record:
    models = load_calibration_models()
    target = next(
        (model for model in models if str(model.get("calibrator_version")) == calibrator_version),
        None,
    )
    if target is None:
        raise CalibrationModelNotFoundError("校准器版本不存在")

    target_scope = str(((target.get("train_filter") or {}).get("project_id") or "")).strip()
    bind_project_id = str(project_id or "").strip()
    if bind_project_id:
        if target_scope and target_scope != bind_project_id:
            raise CalibrationProjectBindingError("校准器与目标项目不匹配，禁止跨项目部署")
        if not target_scope:
            target.setdefault("train_filter", {})
            target["train_filter"]["project_id"] = bind_project_id
            target_scope = bind_project_id

    for model in models:
        model_scope = str(((model.get("train_filter") or {}).get("project_id") or "")).strip()
        if target_scope and model_scope == target_scope:
            model["deployed"] = False
    target["deployed"] = True
    save_calibration_models(models)

    if project_id:
        projects = load_projects()
        for project in projects:
            if str(project.get("id")) == project_id:
                project["calibrator_version_locked"] = calibrator_version
                project["updated_at"] = now_iso()
        save_projects(projects)
    return target


def deploy_calibration_model(
    *,
    atomic_json_transaction: TransactionFactory,
    **kwargs: Any,
) -> Record:
    @atomic_json_transaction("calibration_models", "projects")
    def commit() -> Record:
        originals = {
            "calibration_models": deepcopy(kwargs["load_calibration_models"]()),
            "projects": deepcopy(kwargs["load_projects"]()),
        }
        savers = {
            "calibration_models": kwargs["save_calibration_models"],
            "projects": kwargs["save_projects"],
        }
        attempted: List[str] = []
        operation_kwargs = dict(kwargs)
        operation_kwargs["save_calibration_models"] = _tracked_saver(
            "calibration_models",
            savers["calibration_models"],
            attempted,
        )
        operation_kwargs["save_projects"] = _tracked_saver(
            "projects",
            savers["projects"],
            attempted,
        )
        try:
            return _deploy_calibration_model(**operation_kwargs)
        except BaseException as error:
            _restore_attempted(
                error,
                attempted=attempted,
                originals=originals,
                savers=savers,
            )
            raise

    return commit()


def _apply_calibration_prediction(
    *,
    project_id: str,
    project: Record,
    load_calibration_models: Callable[[], Records],
    load_submissions: Callable[[], Records],
    load_score_reports: Callable[[], Records],
    save_score_reports: Callable[[Records], None],
    save_submissions: Callable[[Records], None],
    build_feature_row: Callback,
    predict_with_model: Callback,
    fuse_rule_and_llm_scores: Callback,
    to_float_or_none: Callback,
    clip_score: Callback,
) -> Record:
    model = select_calibrator_model(
        project,
        load_calibration_models=load_calibration_models,
    )
    if not model:
        return {
            "ok": True,
            "project_id": project_id,
            "model_version": None,
            "updated_reports": 0,
            "updated_submissions": 0,
        }

    submissions = load_submissions()
    submission_map = {
        str(submission.get("id")): submission
        for submission in submissions
        if str(submission.get("project_id")) == project_id
    }
    reports = load_score_reports()
    updated_reports = 0
    for report in reports:
        if str(report.get("project_id")) != project_id:
            continue
        submission_id = str(report.get("submission_id") or "")
        submission = submission_map.get(submission_id)
        if not submission_id or not submission:
            continue
        apply_prediction_to_report(
            report,
            submission_like=submission,
            project=project,
            load_calibration_models=load_calibration_models,
            build_feature_row=build_feature_row,
            predict_with_model=predict_with_model,
            fuse_rule_and_llm_scores=fuse_rule_and_llm_scores,
            to_float_or_none=to_float_or_none,
            clip_score=clip_score,
        )
        updated_reports += 1
    save_score_reports(reports)

    updated_submissions = 0
    for submission in submissions:
        if str(submission.get("project_id")) != project_id:
            continue
        report = submission.get("report")
        if not isinstance(report, dict):
            continue
        apply_prediction_to_report(
            report,
            submission_like=submission,
            project=project,
            load_calibration_models=load_calibration_models,
            build_feature_row=build_feature_row,
            predict_with_model=predict_with_model,
            fuse_rule_and_llm_scores=fuse_rule_and_llm_scores,
            to_float_or_none=to_float_or_none,
            clip_score=clip_score,
        )
        updated_submissions += 1
    save_submissions(submissions)
    return {
        "ok": True,
        "project_id": project_id,
        "model_version": str(model.get("calibrator_version") or ""),
        "updated_reports": updated_reports,
        "updated_submissions": updated_submissions,
    }


def apply_calibration_prediction(
    *,
    project_id: str,
    atomic_json_transaction: TransactionFactory,
    load_projects: Callable[[], Records],
    find_project: Callable[[str, Records], Record],
    **kwargs: Any,
) -> Record:
    @atomic_json_transaction(
        "calibration_models",
        "projects",
        "score_reports",
        "submissions",
    )
    def commit() -> Record:
        project = find_project(project_id, load_projects())
        originals = {
            "score_reports": deepcopy(kwargs["load_score_reports"]()),
            "submissions": deepcopy(kwargs["load_submissions"]()),
        }
        savers = {
            "score_reports": kwargs["save_score_reports"],
            "submissions": kwargs["save_submissions"],
        }
        attempted: List[str] = []
        operation_kwargs = dict(kwargs)
        operation_kwargs["project_id"] = project_id
        operation_kwargs["project"] = project
        operation_kwargs["save_score_reports"] = _tracked_saver(
            "score_reports",
            savers["score_reports"],
            attempted,
        )
        operation_kwargs["save_submissions"] = _tracked_saver(
            "submissions",
            savers["submissions"],
            attempted,
        )
        try:
            return _apply_calibration_prediction(**operation_kwargs)
        except BaseException as error:
            _restore_attempted(
                error,
                attempted=attempted,
                originals=originals,
                savers=savers,
            )
            raise

    return commit()


def _run_auto_calibration_lifecycle(
    *,
    project_id: str,
    project: Record,
    projects: Records,
    samples: Records,
    train_best_calibrator_auto: Callback,
    cross_validate_calibrator: Callback,
    calc_metrics: Callback,
    load_calibration_models: Callable[[], Records],
    save_calibration_models: Callable[[Records], None],
    save_projects: Callable[[Records], None],
    load_submissions: Callable[[], Records],
    load_score_reports: Callable[[], Records],
    save_score_reports: Callable[[Records], None],
    save_submissions: Callable[[Records], None],
    build_feature_row: Callback,
    predict_with_model: Callback,
    fuse_rule_and_llm_scores: Callback,
    to_float_or_none: Callback,
    clip_score: Callback,
    now_iso: Callable[[], str],
) -> Dict[str, Any]:
    calibrator_version = None
    calibrator_deployed = False
    calibrator_summary = build_calibrator_summary(
        model_type=None,
        calibrator_version=None,
        gate_passed=None,
        sample_count=len(samples),
        skipped_reason="insufficient_samples" if len(samples) < 3 else None,
    )
    calibrator_model_type = calibrator_summary.get("model_type")
    calibrator_gate_passed = calibrator_summary.get("gate_passed")
    calibrator_cv_metrics: Dict[str, Any] = calibrator_summary.get("cv_metrics") or {}
    calibrator_baseline_metrics: Dict[str, Any] = calibrator_summary.get("baseline_metrics") or {}
    calibrator_gate: Dict[str, Any] = calibrator_summary.get("gate") or {}
    calibrator_auto_candidates: List[Dict[str, Any]] = (
        calibrator_summary.get("auto_candidates") or []
    )

    if len(samples) >= 3:
        feature_rows = [
            {
                "feature_schema_version": sample.get("feature_schema_version", "v2"),
                "x_features": sample.get("x_features") or {},
                "y_label": sample.get("y_label"),
                "submission_id": sample.get("submission_id"),
            }
            for sample in samples
        ]
        model_artifact = train_best_calibrator_auto(feature_rows, alpha=1.0)
        selected_type = str(model_artifact.get("model_type") or "ridge")
        calibrator_model_type = selected_type
        cv = cross_validate_calibrator(
            model_type=selected_type,
            feature_rows=feature_rows,
            alpha=1.0,
            seed=42,
        )
        y_true = [
            float(row.get("y_label")) for row in feature_rows if row.get("y_label") is not None
        ]
        baseline_pred = [
            float(((row.get("x_features") or {}).get("rule_total_score") or 0.0))
            for row in feature_rows
            if row.get("y_label") is not None
        ]
        baseline_metrics = calc_metrics(y_true, baseline_pred)
        cv_metrics = (
            (cv.get("metrics") or {})
            if bool(cv.get("ok"))
            else {"mae": 0.0, "rmse": 0.0, "spearman": 0.0}
        )
        improve_threshold = max(0.2, float(baseline_metrics.get("mae") or 0.0) * 0.01)
        spearman_tolerance = 0.02
        gate_passed = (
            bool(cv.get("ok"))
            and float(cv_metrics.get("mae") or 0.0)
            <= float(baseline_metrics.get("mae") or 0.0) - improve_threshold
            and float(cv_metrics.get("spearman") or 0.0)
            >= float(baseline_metrics.get("spearman") or 0.0) - spearman_tolerance
        )
        model_artifact.setdefault("metrics", {})
        model_artifact["metrics"]["cv_mae"] = cv_metrics.get("mae")
        model_artifact["metrics"]["cv_rmse"] = cv_metrics.get("rmse")
        model_artifact["metrics"]["cv_spearman"] = cv_metrics.get("spearman")
        model_artifact["metrics"]["cv_mode"] = cv.get("mode")
        model_artifact["metrics"]["cv_pred_count"] = cv.get("pred_count")
        model_artifact["metrics"]["baseline_mae"] = baseline_metrics.get("mae")
        model_artifact["metrics"]["baseline_rmse"] = baseline_metrics.get("rmse")
        model_artifact["metrics"]["baseline_spearman"] = baseline_metrics.get("spearman")
        model_artifact["metrics"]["gate_improve_threshold"] = round(improve_threshold, 4)
        model_artifact["metrics"]["gate_spearman_tolerance"] = spearman_tolerance
        model_artifact["gate_passed"] = gate_passed

        auto_candidates = extract_auto_candidates(model_artifact)
        calibrator_version = (
            f"calib_auto_{selected_type}_" f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        )
        calibrator_summary = build_calibrator_summary(
            model_type=selected_type,
            calibrator_version=calibrator_version,
            gate_passed=bool(gate_passed),
            cv_metrics={
                "mae": cv_metrics.get("mae"),
                "rmse": cv_metrics.get("rmse"),
                "spearman": cv_metrics.get("spearman"),
                "mode": cv.get("mode"),
                "pred_count": cv.get("pred_count"),
            },
            baseline_metrics={
                "mae": baseline_metrics.get("mae"),
                "rmse": baseline_metrics.get("rmse"),
                "spearman": baseline_metrics.get("spearman"),
            },
            improve_threshold=improve_threshold,
            spearman_tolerance=spearman_tolerance,
            auto_candidates=auto_candidates,
            sample_count=len(feature_rows),
        )
        calibrator_model_type = calibrator_summary.get("model_type")
        calibrator_gate_passed = calibrator_summary.get("gate_passed")
        calibrator_cv_metrics = calibrator_summary.get("cv_metrics") or {}
        calibrator_baseline_metrics = calibrator_summary.get("baseline_metrics") or {}
        calibrator_gate = calibrator_summary.get("gate") or {}
        calibrator_auto_candidates = calibrator_summary.get("auto_candidates") or []
        record: Record = {
            "calibrator_version": calibrator_version,
            "model_type": selected_type,
            "feature_schema_version": str(model_artifact.get("feature_schema_version", "v2")),
            "train_filter": {"project_id": project_id, "mode": "auto_run"},
            "metrics": {
                **(model_artifact.get("metrics") or {}),
                "gate_passed": bool(gate_passed),
            },
            "calibrator_summary": calibrator_summary,
            "artifact_uri": f"json://calibration_models/{calibrator_version}",
            "model_artifact": model_artifact,
            "deployed": bool(gate_passed),
            "created_at": now_iso(),
        }
        models = load_calibration_models()
        if record["deployed"]:
            for model in models:
                if str(((model.get("train_filter") or {}).get("project_id") or "")) == project_id:
                    model["deployed"] = False
            project["calibrator_version_locked"] = calibrator_version
            project["updated_at"] = now_iso()
            save_projects(projects)
            calibrator_deployed = True
        models.append(record)
        save_calibration_models(models)

    updated_reports = 0
    updated_submissions = 0
    if calibrator_deployed:
        submissions = load_submissions()
        submission_map = {
            str(submission.get("id")): submission
            for submission in submissions
            if str(submission.get("project_id")) == project_id
        }
        reports = load_score_reports()
        for report in reports:
            if str(report.get("project_id")) != project_id:
                continue
            submission_id = str(report.get("submission_id") or "")
            submission = submission_map.get(submission_id)
            if not submission:
                continue
            apply_prediction_to_report(
                report,
                submission_like=submission,
                project=project,
                load_calibration_models=load_calibration_models,
                build_feature_row=build_feature_row,
                predict_with_model=predict_with_model,
                fuse_rule_and_llm_scores=fuse_rule_and_llm_scores,
                to_float_or_none=to_float_or_none,
                clip_score=clip_score,
            )
            updated_reports += 1
        save_score_reports(reports)

        for submission in submissions:
            if str(submission.get("project_id")) != project_id:
                continue
            report = submission.get("report")
            if not isinstance(report, dict):
                continue
            apply_prediction_to_report(
                report,
                submission_like=submission,
                project=project,
                load_calibration_models=load_calibration_models,
                build_feature_row=build_feature_row,
                predict_with_model=predict_with_model,
                fuse_rule_and_llm_scores=fuse_rule_and_llm_scores,
                to_float_or_none=to_float_or_none,
                clip_score=clip_score,
            )
            updated_submissions += 1
        save_submissions(submissions)

    return {
        "calibrator_version": calibrator_version,
        "calibrator_deployed": calibrator_deployed,
        "calibrator_summary": calibrator_summary,
        "calibrator_model_type": calibrator_model_type,
        "calibrator_gate_passed": calibrator_gate_passed,
        "calibrator_cv_metrics": calibrator_cv_metrics,
        "calibrator_baseline_metrics": calibrator_baseline_metrics,
        "calibrator_gate": calibrator_gate,
        "calibrator_auto_candidates": calibrator_auto_candidates,
        "prediction_updated_reports": updated_reports,
        "prediction_updated_submissions": updated_submissions,
    }


def run_auto_calibration_lifecycle(
    *,
    project_id: str,
    atomic_json_transaction: TransactionFactory,
    load_projects: Callable[[], Records],
    find_project: Callable[[str, Records], Record],
    **kwargs: Any,
) -> Dict[str, Any]:
    @atomic_json_transaction(
        "calibration_models",
        "projects",
        "score_reports",
        "submissions",
    )
    def commit() -> Dict[str, Any]:
        original_projects = deepcopy(load_projects())
        projects = deepcopy(original_projects)
        project = find_project(project_id, projects)
        originals = {
            "calibration_models": deepcopy(kwargs["load_calibration_models"]()),
            "projects": original_projects,
            "score_reports": deepcopy(kwargs["load_score_reports"]()),
            "submissions": deepcopy(kwargs["load_submissions"]()),
        }
        savers = {
            "calibration_models": kwargs["save_calibration_models"],
            "projects": kwargs["save_projects"],
            "score_reports": kwargs["save_score_reports"],
            "submissions": kwargs["save_submissions"],
        }
        attempted: List[str] = []
        operation_kwargs = dict(kwargs)
        operation_kwargs["project_id"] = project_id
        operation_kwargs["project"] = project
        operation_kwargs["projects"] = projects
        for name in savers:
            operation_kwargs[f"save_{name}"] = _tracked_saver(
                name,
                savers[name],
                attempted,
            )
        try:
            return _run_auto_calibration_lifecycle(**operation_kwargs)
        except BaseException as error:
            _restore_attempted(
                error,
                attempted=attempted,
                originals=originals,
                savers=savers,
            )
            raise

    return commit()
