from __future__ import annotations

from typing import Dict, List, Optional


def _main():
    import app.main as main_mod

    return main_mod


def auto_update_feature_confidence_on_ground_truth(
    *,
    report: Dict[str, object],
    gt_record: Dict[str, object],
    project_score_scale_max: int,
) -> Dict[str, object]:
    main = _main()
    applied_feature_ids = main._collect_applied_feature_ids_from_report(report)
    if not applied_feature_ids:
        return {"updated": 0, "retired": 0, "reason": "no_applied_feature_ids"}

    gt_for_learning = main._ground_truth_record_for_learning(
        gt_record,
        default_score_scale_max=project_score_scale_max,
    )
    actual_score_100 = main._to_float_or_none(gt_for_learning.get("final_score"))
    if actual_score_100 is None:
        return {"updated": 0, "retired": 0, "reason": "missing_actual_score"}

    pred_score_100 = resolve_report_predicted_score_100(
        report,
        project_score_scale_max=project_score_scale_max,
    )
    if pred_score_100 is None:
        return {"updated": 0, "retired": 0, "reason": "missing_predicted_score"}

    update_result = main.update_feature_confidence(
        applied_feature_ids=applied_feature_ids,
        actual_score=float(actual_score_100),
        predicted_score=float(pred_score_100),
    )
    applied_dimension_ids = main._map_feature_ids_to_dimension_ids(
        applied_feature_ids,
        report=report,
    )
    update_result["applied_feature_ids"] = applied_feature_ids
    update_result["applied_dimension_ids"] = applied_dimension_ids
    update_result["actual_score_100"] = round(float(actual_score_100), 2)
    update_result["predicted_score_100"] = round(float(pred_score_100), 2)
    delta_score_100 = round(float(actual_score_100) - float(pred_score_100), 2)
    feedback_polarity = "neutral"
    if delta_score_100 >= 1.0:
        feedback_polarity = "positive"
    elif delta_score_100 <= -1.0:
        feedback_polarity = "negative"
    update_result["delta_score_100"] = delta_score_100
    update_result["time_decay_weight"] = round(
        main.compute_time_decay_weight(
            record_time=gt_record.get("updated_at") or gt_record.get("created_at"),
            half_life_days=30.0,
            min_decay=0.05,
        ),
        4,
    )
    update_result["positive_or_negative_feedback"] = feedback_polarity
    return update_result


def resolve_report_predicted_score_100(
    report: Dict[str, object],
    *,
    project_score_scale_max: int,
) -> Optional[float]:
    main = _main()
    pred_score_100 = main._to_float_or_none(report.get("pred_total_score"))
    if pred_score_100 is None:
        pred_score_100 = main._to_float_or_none(report.get("total_score"))
    if pred_score_100 is None:
        pred_score_100 = main._to_float_or_none(report.get("rule_total_score"))
    if pred_score_100 is None:
        return None
    if int(project_score_scale_max) == 5 and pred_score_100 <= 5.0:
        pred_score_100 = float(main._convert_score_to_100(pred_score_100, 5) or 0.0)
    return float(pred_score_100)


def build_ground_truth_feedback_guardrail(
    *,
    report: Dict[str, object],
    gt_record: Dict[str, object],
    project_score_scale_max: int,
) -> Dict[str, object]:
    main = _main()
    gt_for_learning = main._ground_truth_record_for_learning(
        gt_record,
        default_score_scale_max=project_score_scale_max,
    )
    actual_score_100 = main._to_float_or_none(gt_for_learning.get("final_score"))
    predicted_score_100 = resolve_report_predicted_score_100(
        report,
        project_score_scale_max=project_score_scale_max,
    )
    if actual_score_100 is None or predicted_score_100 is None:
        return main._normalize_feedback_guardrail_state(
            {
                "blocked": False,
                "status": "insufficient_score_context",
                "requires_manual_confirmation": False,
                "actual_score_100": round(float(actual_score_100 or 0.0), 2),
                "predicted_score_100": (
                    round(float(predicted_score_100), 2)
                    if predicted_score_100 is not None
                    else None
                ),
                "threshold_ratio": round(float(main.DEFAULT_FEEDBACK_EXTREME_DELTA_RATIO), 4),
            }
        )

    abs_delta_100 = abs(float(actual_score_100) - float(predicted_score_100))
    relative_delta_ratio = abs_delta_100 / 100.0
    blocked = relative_delta_ratio > float(main.DEFAULT_FEEDBACK_EXTREME_DELTA_RATIO)
    warning_message = ""
    if blocked:
        warning_message = (
            f"预测与真实总分偏差 {abs_delta_100:.2f} 分（100分口径，{relative_delta_ratio * 100:.1f}%），"
            "已暂停自动调权/自动校准，请人工确认后再执行「学习进化」或「一键闭环执行」。"
        )
    return main._normalize_feedback_guardrail_state(
        {
            "blocked": blocked,
            "threshold_blocked": blocked,
            "status": "blocked" if blocked else "accepted",
            "requires_manual_confirmation": blocked,
            "actual_score_100": round(float(actual_score_100), 2),
            "predicted_score_100": round(float(predicted_score_100), 2),
            "abs_delta_100": round(float(abs_delta_100), 2),
            "relative_delta_ratio": round(float(relative_delta_ratio), 4),
            "threshold_ratio": round(float(main.DEFAULT_FEEDBACK_EXTREME_DELTA_RATIO), 4),
            "warning_message": warning_message or None,
            "manual_override_hint": "confirm_extreme_sample=1" if blocked else None,
        }
    )


def capture_ground_truth_few_shot_features(
    *,
    report: Dict[str, object],
    gt_record: Dict[str, object],
    project_score_scale_max: int,
    feedback_guardrail: Dict[str, object],
    feature_confidence_update: Dict[str, object],
) -> Dict[str, object]:
    main = _main()
    if bool(feedback_guardrail.get("blocked")):
        return {"captured": 0, "reason": "guardrail_blocked"}

    gt_for_learning = main._ground_truth_record_for_learning(
        gt_record,
        default_score_scale_max=project_score_scale_max,
    )
    actual_score_100 = float(main._to_float_or_none(gt_for_learning.get("final_score")) or 0.0)
    if actual_score_100 < float(main.DEFAULT_FEW_SHOT_MIN_HIGH_SCORE_100):
        return {
            "captured": 0,
            "reason": "below_high_score_threshold",
            "actual_score_100": round(actual_score_100, 2),
            "min_high_score_threshold_100": round(
                float(main.DEFAULT_FEW_SHOT_MIN_HIGH_SCORE_100), 2
            ),
        }

    candidate_dimensions = main._select_ground_truth_few_shot_dimensions(
        report=report,
        feature_confidence_update=feature_confidence_update,
    )
    if not candidate_dimensions:
        return {"captured": 0, "reason": "no_candidate_dimensions"}

    tags = main._flatten_ground_truth_qualitative_tags(gt_record)
    distilled_features = []
    feature_ids: List[str] = []
    for dim_id in candidate_dimensions:
        dim_name = ((main.DIMENSIONS.get(dim_id) or {}).get("name") or dim_id).strip()
        evidence_texts = main._collect_dimension_evidence_texts(report, dimension_id=dim_id)
        guidance_texts = main._collect_dimension_guidance_texts(report, dimension_id=dim_id)
        source_parts: List[str] = [f"维度：{dim_name}"]
        if tags:
            source_parts.append("评委反馈：" + "；".join(tags[:6]))
        if evidence_texts:
            source_parts.append("高分证据：" + "；".join(evidence_texts[:3]))
        if guidance_texts:
            source_parts.append("编制提示：" + "；".join(guidance_texts[:2]))
        source_text = "\n".join(part for part in source_parts if part.strip())
        feature = main.distill_feature_from_text(
            dimension_id=dim_id,
            source_text=source_text,
            confidence_score=0.7,
        )
        if feature is None:
            continue
        distilled_features.append(feature)
        feature_ids.append(str(feature.feature_id or ""))

    if not distilled_features:
        return {"captured": 0, "reason": "feature_distillation_empty"}

    upsert_result = main.upsert_distilled_features(distilled_features)
    return {
        "captured": len(distilled_features),
        "reason": "captured",
        "dimension_ids": candidate_dimensions,
        "feature_ids": feature_ids,
        "actual_score_100": round(actual_score_100, 2),
        "min_high_score_threshold_100": round(float(main.DEFAULT_FEW_SHOT_MIN_HIGH_SCORE_100), 2),
        "upsert": upsert_result,
    }


def sync_ground_truth_record_to_qingtian(
    project_id: str,
    gt_record: Dict[str, object],
) -> Dict[str, object]:
    main = _main()
    projects = main.load_projects()
    project = main._find_project(project_id, projects)
    config = main.load_config()
    multipliers, profile_snapshot, _ = main._resolve_project_scoring_context(project_id)
    scoring_engine_version = str(project.get("scoring_engine_version_locked") or "v1")
    source_gt_id = str(gt_record.get("id") or "")
    gt_text = str(gt_record.get("shigong_text") or "")

    submissions = main.load_submissions()
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
    now_iso = main._now_iso()
    material_knowledge_snapshot: Optional[Dict[str, object]] = None
    if matched_submission is None:
        matched_submission = {
            "id": str(main.uuid4()),
            "project_id": project_id,
            "filename": f"ground_truth_{source_gt_id[:8]}.txt",
            "total_score": 0.0,
            "report": main._build_pending_submission_report(
                project=project,
                scoring_engine_version=scoring_engine_version,
            ),
            "text": gt_text,
            "created_at": now_iso,
            "updated_at": now_iso,
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

    if not main._submission_is_scored(matched_submission):
        if material_knowledge_snapshot is None:
            material_knowledge_snapshot = main._build_material_knowledge_profile(project_id)
        report, evidence_units_new = main._score_submission_for_project(
            submission_id=str(matched_submission.get("id")),
            text=gt_text,
            project_id=project_id,
            project=project,
            config=config,
            multipliers=multipliers,
            profile_snapshot=profile_snapshot,
            scoring_engine_version=scoring_engine_version,
            material_knowledge_snapshot=material_knowledge_snapshot,
        )
        if not main._report_is_blocked(report):
            main._mark_report_scored(report, trigger="ground_truth_sync")
        matched_submission["report"] = report
        matched_submission["total_score"] = float(
            report.get("total_score", report.get("rule_total_score", 0.0))
        )
        matched_submission["expert_profile_id_used"] = (
            profile_snapshot.get("id") if profile_snapshot else None
        )
        matched_submission["updated_at"] = main._now_iso()
        scored_submission = True
        submission_changed = True

    if submission_changed:
        main.save_submissions(submissions)

    if scored_submission:
        snapshots = main.load_score_reports()
        snapshots.append(
            main._build_score_report_snapshot(
                submission_id=str(matched_submission.get("id")),
                project=project,
                report=matched_submission.get("report") or {},
                profile_snapshot=profile_snapshot,
                scoring_engine_version=scoring_engine_version,
            )
        )
        main.save_score_reports(snapshots)
        if evidence_units_new:
            all_units = main.load_evidence_units()
            all_units = main._replace_submission_evidence_units(
                all_units,
                submission_id=str(matched_submission.get("id")),
                new_units=evidence_units_new,
            )
            main.save_evidence_units(all_units)

        report = matched_submission.get("report") or {}
        dimension_scores = {
            dim_id: (dim.get("score", 0.0) if isinstance(dim, dict) else 0.0)
            for dim_id, dim in (report.get("dimension_scores") or {}).items()
        }
        penalty_count = len(report.get("penalties", []))
        if not main._report_is_blocked(report):
            main.record_history_score(
                project_id=project_id,
                submission_id=str(matched_submission.get("id")),
                filename=str(matched_submission.get("filename", "")),
                total_score=float(report.get("total_score", report.get("rule_total_score", 0.0))),
                dimension_scores=dimension_scores,
                penalty_count=penalty_count,
            )

    qt_results = main.load_qingtian_results()
    matched_qt = next(
        (
            row
            for row in qt_results
            if str((row.get("raw_payload") or {}).get("ground_truth_record_id") or "")
            == source_gt_id
        ),
        None,
    )
    project_score_scale = main._resolve_project_score_scale_max(project)
    gt_for_learning = main._ground_truth_record_for_learning(
        gt_record,
        default_score_scale_max=project_score_scale,
    )
    report_for_feedback = matched_submission.get("report")
    feedback_guardrail: Dict[str, object] = {
        "blocked": False,
        "status": "not_executed",
        "requires_manual_confirmation": False,
    }
    if isinstance(report_for_feedback, dict):
        try:
            feedback_guardrail = build_ground_truth_feedback_guardrail(
                report=report_for_feedback,
                gt_record=gt_record,
                project_score_scale_max=project_score_scale,
            )
        except Exception as exc:
            feedback_guardrail = {
                "blocked": False,
                "status": "guardrail_error",
                "requires_manual_confirmation": False,
                "error": str(exc),
            }
    feature_confidence_update: Dict[str, object] = {
        "updated": 0,
        "retired": 0,
        "reason": "not_executed",
    }
    if bool(feedback_guardrail.get("blocked")):
        feature_confidence_update = {
            "updated": 0,
            "retired": 0,
            "reason": "guardrail_blocked",
            "actual_score_100": feedback_guardrail.get("actual_score_100"),
            "predicted_score_100": feedback_guardrail.get("predicted_score_100"),
        }
    elif isinstance(report_for_feedback, dict):
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
    few_shot_distillation: Dict[str, object] = {"captured": 0, "reason": "not_executed"}
    if bool(feedback_guardrail.get("blocked")):
        few_shot_distillation = {"captured": 0, "reason": "guardrail_blocked"}
    elif isinstance(report_for_feedback, dict):
        try:
            few_shot_distillation = capture_ground_truth_few_shot_features(
                report=report_for_feedback,
                gt_record=gt_record,
                project_score_scale_max=project_score_scale,
                feedback_guardrail=feedback_guardrail,
                feature_confidence_update=feature_confidence_update,
            )
        except Exception as exc:
            few_shot_distillation = {
                "captured": 0,
                "reason": "few_shot_distillation_error",
                "error": str(exc),
            }

    if source_gt_id:
        all_gt_records = main.load_ground_truth()
        changed_gt = False
        for row in all_gt_records:
            if str(row.get("id") or "") != source_gt_id:
                continue
            row["feedback_guardrail"] = main._normalize_feedback_guardrail_state(feedback_guardrail)
            row["feature_confidence_update"] = feature_confidence_update
            row["few_shot_distillation"] = main._normalize_few_shot_distillation_state(
                few_shot_distillation
            )
            row["updated_at"] = main._now_iso()
            changed_gt = True
            break
        if changed_gt:
            main.save_ground_truth(all_gt_records)

    if matched_qt is None:
        qt_results.append(
            {
                "id": str(main.uuid4()),
                "submission_id": str(matched_submission.get("id")),
                "qingtian_model_version": str(
                    project.get("qingtian_model_version") or main.DEFAULT_QINGTIAN_MODEL_VERSION
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
                    "feedback_guardrail": feedback_guardrail,
                    "feature_confidence_update": feature_confidence_update,
                    "few_shot_distillation": few_shot_distillation,
                },
                "created_at": main._now_iso(),
            }
        )
        main.save_qingtian_results(qt_results)
    else:
        raw_payload = matched_qt.get("raw_payload")
        if not isinstance(raw_payload, dict):
            raw_payload = {}
        raw_payload["feedback_guardrail"] = feedback_guardrail
        raw_payload["feature_confidence_update"] = feature_confidence_update
        raw_payload["few_shot_distillation"] = few_shot_distillation
        matched_qt["raw_payload"] = raw_payload
        main.save_qingtian_results(qt_results)

    if str(project.get("status") or "") == "scoring_preparation":
        project["status"] = "submitted_to_qingtian"
        project["updated_at"] = main._now_iso()
        main.save_projects(projects)

    main._refresh_project_reflection_objects(project_id)
    gt_record["feedback_guardrail"] = feedback_guardrail
    gt_record["few_shot_distillation"] = few_shot_distillation
    gt_record["feature_confidence_update"] = feature_confidence_update
    return {
        "feedback_guardrail": feedback_guardrail,
        "feature_confidence_update": feature_confidence_update,
        "few_shot_distillation": few_shot_distillation,
        "submission_id": str(matched_submission.get("id") or ""),
    }


def run_feedback_closed_loop(
    project_id: str,
    *,
    locale: str,
    trigger: str,
    ground_truth_record_ids: Optional[List[str]] = None,
) -> Dict[str, object]:
    main = _main()
    result: Dict[str, object] = {
        "ok": True,
        "project_id": project_id,
        "trigger": trigger,
        "weight_update": {"updated": False},
        "weight_sync_to_evolution": {"synced": False},
        "auto_run": None,
        "evolution_refresh": {"refreshed": False},
    }
    feedback_guardrail = main._summarize_project_feedback_guardrail(
        project_id,
        record_ids=ground_truth_record_ids,
    )
    result["feedback_guardrail"] = feedback_guardrail
    try:
        main._refresh_project_reflection_objects(project_id)
    except Exception as exc:
        result["ok"] = False
        result["refresh_error"] = str(exc)
        return result

    if bool(feedback_guardrail.get("blocked")):
        result["guardrail_triggered"] = True
        result["requires_manual_confirmation"] = True
        result["auto_update_skipped"] = True
        result["weight_update"] = {
            "updated": False,
            "reason": "guardrail_blocked",
            "blocked_record_ids": feedback_guardrail.get("blocked_record_ids") or [],
        }
        result["weight_sync_to_evolution"] = {
            "synced": False,
            "reason": "guardrail_blocked",
        }
        result["auto_run"] = {
            "ok": False,
            "skipped": True,
            "reason": "guardrail_blocked",
            "requires_manual_confirmation": True,
        }
        try:
            result["evolution_refresh"] = main._refresh_evolution_report_from_ground_truth(
                project_id
            )
        except Exception as exc:
            result["evolution_refresh"] = {"refreshed": False, "error": str(exc)}
        return result

    try:
        result["weight_update"] = main._auto_update_project_weights_from_delta_cases(project_id)
    except Exception as exc:
        result["weight_update"] = {"updated": False, "error": str(exc)}
    try:
        result["weight_sync_to_evolution"] = main._sync_feedback_weights_to_evolution(
            project_id, result["weight_update"]
        )
    except Exception as exc:
        result["weight_sync_to_evolution"] = {"synced": False, "error": str(exc)}

    try:
        auto_resp = main.auto_run_reflection_pipeline(
            project_id=project_id, api_key=None, locale=locale
        )
        if hasattr(auto_resp, "model_dump"):
            result["auto_run"] = auto_resp.model_dump()
        else:
            result["auto_run"] = dict(auto_resp)
    except Exception as exc:
        result["auto_run"] = {"ok": False, "error": str(exc)}
        result["ok"] = False
    try:
        result["evolution_refresh"] = main._refresh_evolution_report_from_ground_truth(project_id)
    except Exception as exc:
        result["evolution_refresh"] = {"refreshed": False, "error": str(exc)}
    return result


def run_feedback_closed_loop_safe(
    project_id: str,
    *,
    locale: str,
    trigger: str,
    ground_truth_record_ids: Optional[List[str]] = None,
) -> Dict[str, object]:
    main = _main()
    try:
        if ground_truth_record_ids:
            raw_result = main._run_feedback_closed_loop(
                project_id,
                locale=locale,
                trigger=trigger,
                ground_truth_record_ids=ground_truth_record_ids,
            )
        else:
            raw_result = main._run_feedback_closed_loop(
                project_id,
                locale=locale,
                trigger=trigger,
            )
        if isinstance(raw_result, dict):
            result = dict(raw_result)
        elif hasattr(raw_result, "model_dump"):
            dumped = raw_result.model_dump()
            if isinstance(dumped, dict):
                result = dict(dumped)
            else:
                result = {
                    "ok": bool(getattr(raw_result, "ok", False)),
                    "project_id": project_id,
                    "trigger": trigger,
                    "raw": str(raw_result),
                }
        else:
            result = {
                "ok": bool(getattr(raw_result, "ok", False)),
                "project_id": project_id,
                "trigger": trigger,
                "raw": str(raw_result),
            }
        if not bool(result.get("ok", True)):
            main.logger.warning(
                "feedback_closed_loop_non_ok project_id=%s trigger=%s result=%s",
                project_id,
                trigger,
                result,
            )
        return result
    except Exception as exc:
        main.logger.exception(
            "feedback_closed_loop_exception project_id=%s trigger=%s error=%s",
            project_id,
            trigger,
            exc,
        )
        return {
            "ok": False,
            "project_id": project_id,
            "trigger": trigger,
            "error": f"{type(exc).__name__}: {exc}",
        }


def finalize_ground_truth_learning_record(
    project_id: str,
    record: Dict[str, object],
    *,
    locale: str,
    trigger: str,
) -> Dict[str, object]:
    main = _main()
    sync_result = main._sync_ground_truth_record_to_qingtian(project_id, record)
    sync_payload = sync_result if isinstance(sync_result, dict) else {}
    feedback_guardrail = sync_payload.get("feedback_guardrail")
    few_shot_distillation = sync_payload.get("few_shot_distillation")
    record["feedback_guardrail"] = main._normalize_feedback_guardrail_state(feedback_guardrail)
    record["few_shot_distillation"] = main._normalize_few_shot_distillation_state(
        few_shot_distillation
    )
    record["feedback_closed_loop"] = main._run_feedback_closed_loop_safe(
        project_id,
        locale=locale,
        trigger=trigger,
        ground_truth_record_ids=[str(record.get("id") or "")],
    )
    return main._persist_ground_truth_record_fields(
        project_id,
        str(record.get("id") or ""),
        updates={
            "feedback_guardrail": main._extract_feedback_guardrail(record),
            "few_shot_distillation": main._normalize_few_shot_distillation_state(
                record.get("few_shot_distillation")
            ),
            "feedback_closed_loop": record.get("feedback_closed_loop") or {},
        },
    )
