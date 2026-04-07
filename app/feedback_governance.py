from __future__ import annotations

import copy
from typing import Dict, List, Optional

from fastapi import HTTPException


def _main():
    import app.main as main_mod

    return main_mod


def build_feedback_governance_report(
    project_id: str,
    project: Dict[str, object],
    *,
    artifact_payload_overrides: Optional[Dict[str, object]] = None,
    ground_truth_rows_override: Optional[List[Dict[str, object]]] = None,
) -> Dict[str, object]:
    main = _main()
    project_score_scale = main._resolve_project_score_scale_max(project)
    all_rows = main._list_project_ground_truth_records(
        project_id,
        include_guardrail_blocked=True,
        rows_override=ground_truth_rows_override,
    )
    active_rows = main._list_project_ground_truth_records(
        project_id,
        rows_override=ground_truth_rows_override,
    )
    blocked_rows = main._collect_blocked_ground_truth_guardrails(
        project_id,
        rows_override=ground_truth_rows_override,
    )
    summary_guardrail = main._summarize_project_feedback_guardrail(
        project_id,
        rows_override=ground_truth_rows_override,
    )
    approved_extreme_count = 0
    rejected_extreme_count = 0
    pending_extreme_count = 0
    few_shot_adopted_count = 0
    few_shot_ignored_count = 0
    few_shot_pending_review_count = 0
    approved_samples: List[Dict[str, object]] = []
    adopted_few_shot: List[Dict[str, object]] = []

    blocked_samples: List[Dict[str, object]] = []
    for item in blocked_rows[:12]:
        guardrail = main._normalize_feedback_guardrail_state(item.get("feedback_guardrail"))
        record_id = str(item.get("record_id") or "")
        row = next(
            (candidate for candidate in all_rows if str(candidate.get("id") or "") == record_id), {}
        )
        blocked_samples.append(
            {
                "record_id": record_id,
                "created_at": str(row.get("created_at") or ""),
                "source_submission_filename": str(row.get("source_submission_filename") or ""),
                "source": str(row.get("source") or ""),
                "score_scale_max": int(
                    main._to_float_or_none(guardrail.get("score_scale_max")) or project_score_scale
                ),
                "score_scale_label": str(
                    guardrail.get("score_scale_label")
                    or main._score_scale_label(project_score_scale)
                ),
                "actual_score": main._to_float_or_none(guardrail.get("actual_score_raw")),
                "predicted_score": main._to_float_or_none(guardrail.get("predicted_score_raw")),
                "current_score": main._to_float_or_none(guardrail.get("current_score_raw")),
                "abs_delta": main._to_float_or_none(guardrail.get("abs_delta_raw")),
                "actual_score_100": main._to_float_or_none(guardrail.get("actual_score_100")),
                "predicted_score_100": main._to_float_or_none(guardrail.get("predicted_score_100")),
                "current_score_100": main._to_float_or_none(guardrail.get("current_score_100")),
                "abs_delta_100": main._to_float_or_none(guardrail.get("abs_delta_100")),
                "relative_delta_ratio": main._to_float_or_none(
                    guardrail.get("relative_delta_ratio")
                ),
                "warning_message": str(guardrail.get("warning_message") or ""),
                "manual_review_status": str(guardrail.get("manual_review_status") or ""),
                "manual_review_note": str(guardrail.get("manual_review_note") or ""),
                "manual_reviewed_at": str(guardrail.get("manual_reviewed_at") or ""),
            }
        )

    few_shot_recent: List[Dict[str, object]] = []
    captured_recent_count = 0
    for row in all_rows:
        guardrail = main._extract_feedback_guardrail(row)
        if bool(guardrail.get("threshold_blocked")):
            review_status = str(guardrail.get("manual_review_status") or "pending")
            if review_status == "approved":
                approved_extreme_count += 1
                if len(approved_samples) < 10:
                    closed_loop = (
                        row.get("feedback_closed_loop")
                        if isinstance(row.get("feedback_closed_loop"), dict)
                        else {}
                    )
                    auto_run = (
                        closed_loop.get("auto_run")
                        if isinstance(closed_loop.get("auto_run"), dict)
                        else {}
                    )
                    evolution_refresh = (
                        closed_loop.get("evolution_refresh")
                        if isinstance(closed_loop.get("evolution_refresh"), dict)
                        else {}
                    )
                    weight_update = (
                        closed_loop.get("weight_update")
                        if isinstance(closed_loop.get("weight_update"), dict)
                        else {}
                    )
                    approved_samples.append(
                        {
                            "record_id": str(row.get("id") or ""),
                            "source_submission_filename": str(
                                row.get("source_submission_filename") or row.get("source") or ""
                            ),
                            "reviewed_at": str(guardrail.get("manual_reviewed_at") or ""),
                            "review_note": str(guardrail.get("manual_review_note") or ""),
                            "score_scale_max": int(
                                main._to_float_or_none(guardrail.get("score_scale_max"))
                                or project_score_scale
                            ),
                            "score_scale_label": str(
                                guardrail.get("score_scale_label")
                                or main._score_scale_label(project_score_scale)
                            ),
                            "actual_score": main._to_float_or_none(
                                guardrail.get("actual_score_raw")
                            ),
                            "predicted_score": main._to_float_or_none(
                                guardrail.get("predicted_score_raw")
                            ),
                            "current_score": main._to_float_or_none(
                                guardrail.get("current_score_raw")
                            ),
                            "abs_delta": main._to_float_or_none(guardrail.get("abs_delta_raw")),
                            "actual_score_100": main._to_float_or_none(
                                guardrail.get("actual_score_100")
                            ),
                            "predicted_score_100": main._to_float_or_none(
                                guardrail.get("predicted_score_100")
                            ),
                            "current_score_100": main._to_float_or_none(
                                guardrail.get("current_score_100")
                            ),
                            "abs_delta_100": main._to_float_or_none(guardrail.get("abs_delta_100")),
                            "closed_loop_effect": {
                                "weight_updated": bool(weight_update.get("updated")),
                                "delta_case_count": int(
                                    main._to_float_or_none(auto_run.get("delta_cases")) or 0
                                ),
                                "calibration_sample_count": int(
                                    main._to_float_or_none(auto_run.get("calibration_samples")) or 0
                                ),
                                "calibrator_version": str(auto_run.get("calibrator_version") or ""),
                                "evolution_refresh_sample_count": int(
                                    main._to_float_or_none(evolution_refresh.get("sample_count"))
                                    or 0
                                ),
                            },
                        }
                    )
            elif review_status == "rejected":
                rejected_extreme_count += 1
            else:
                pending_extreme_count += 1
    for row in sorted(
        all_rows,
        key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),
        reverse=True,
    ):
        distill = main._normalize_few_shot_distillation_state(row.get("few_shot_distillation"))
        captured = int(main._to_float_or_none(distill.get("captured")) or 0)
        reason_text = str(distill.get("reason") or "").strip()
        if captured > 0:
            captured_recent_count += 1
            review_status = str(distill.get("manual_review_status") or "pending")
            if review_status == "adopted":
                few_shot_adopted_count += 1
                if len(adopted_few_shot) < 10:
                    adopted_few_shot.append(
                        {
                            "record_id": str(row.get("id") or ""),
                            "reviewed_at": str(distill.get("manual_reviewed_at") or ""),
                            "review_note": str(distill.get("manual_review_note") or ""),
                            "captured": captured,
                            "dimension_ids": [
                                main._normalize_dimension_id(item)
                                for item in (distill.get("dimension_ids") or [])
                                if main._normalize_dimension_id(item)
                            ],
                            "feature_ids": [
                                str(item or "").strip()
                                for item in (distill.get("feature_ids") or [])
                                if str(item or "").strip()
                            ][:8],
                        }
                    )
            elif review_status == "ignored":
                few_shot_ignored_count += 1
            else:
                few_shot_pending_review_count += 1
        if len(few_shot_recent) >= 10:
            continue
        if captured <= 0 and not reason_text:
            continue
        normalized_row = main._ground_truth_record_for_learning(
            row if isinstance(row, dict) else {},
            default_score_scale_max=project_score_scale,
        )
        few_shot_recent.append(
            {
                "record_id": str(row.get("id") or ""),
                "created_at": str(row.get("updated_at") or row.get("created_at") or ""),
                "score_scale_max": int(
                    main._to_float_or_none(normalized_row.get("score_scale_max"))
                    or project_score_scale
                ),
                "score_scale_label": str(
                    main._score_scale_label(
                        int(
                            main._to_float_or_none(normalized_row.get("score_scale_max"))
                            or project_score_scale
                        )
                    )
                ),
                "actual_score": main._to_float_or_none(normalized_row.get("final_score_raw")),
                "actual_score_100": main._to_float_or_none(normalized_row.get("final_score")),
                "captured": captured,
                "reason": reason_text,
                "dimension_ids": [
                    main._normalize_dimension_id(item)
                    for item in (distill.get("dimension_ids") or [])
                    if main._normalize_dimension_id(item)
                ],
                "feature_ids": [
                    str(item or "").strip()
                    for item in (distill.get("feature_ids") or [])
                    if str(item or "").strip()
                ][:6],
                "manual_review_status": str(distill.get("manual_review_status") or ""),
                "manual_review_note": str(distill.get("manual_review_note") or ""),
                "manual_reviewed_at": str(distill.get("manual_reviewed_at") or ""),
            }
        )

    version_targets = [
        ("high_score_features", main.HIGH_SCORE_FEATURES_PATH),
        ("evolution_reports", main.EVOLUTION_REPORTS_PATH),
        ("calibration_models", main.CALIBRATION_MODELS_PATH),
        ("expert_profiles", main.EXPERT_PROFILES_PATH),
    ]
    version_history: List[Dict[str, object]] = []
    for artifact, path in version_targets:
        versions = main.list_json_versions(path)
        latest = versions[0] if versions else {}
        version_history.append(
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
    artifact_impacts = main._build_governance_artifact_impacts(
        project_id,
        artifact_payload_overrides=artifact_payload_overrides,
    )
    calibrator_rows = main._load_governance_artifact_payload(
        "calibration_models",
        artifact_payload_overrides=artifact_payload_overrides,
    )
    calibrator_state = main._summarize_project_calibrator_state(
        project,
        calibrator_rows if isinstance(calibrator_rows, list) else [],
    )
    score_preview = main._build_governance_score_preview(
        project_id,
        project,
        artifact_impacts,
        artifact_payload_overrides=artifact_payload_overrides,
        ground_truth_rows_override=ground_truth_rows_override,
    )
    sandbox_preview = main._build_governance_sandbox_preview(
        project_id,
        project,
        artifact_payload_overrides=artifact_payload_overrides,
        ground_truth_rows_override=ground_truth_rows_override,
    )
    evolution_health = main._build_evolution_health_report(project_id, project)
    evolution_summary = (
        evolution_health.get("summary")
        if isinstance(evolution_health, dict) and isinstance(evolution_health.get("summary"), dict)
        else {}
    )

    recommendations: List[str] = []
    blocked_count = int(summary_guardrail.get("blocked_count") or 0)
    if blocked_count > 0:
        recommendations.append(
            f"存在 {blocked_count} 条极端偏差样本，自动调权/自动校准已被暂停；人工确认后再执行学习进化或一键闭环。"
        )
    if captured_recent_count <= 0:
        recommendations.append(
            "近期尚未形成新的 few-shot 蒸馏样本，建议优先补录高分且证据充分的真实评标。"
        )
    if any(int(item.get("version_count") or 0) <= 0 for item in version_history):
        recommendations.append(
            "部分闭环产物尚无历史快照，建议先完成一次真实反馈学习后再观察版本回退能力。"
        )
    if blocked_count <= 0 and captured_recent_count > 0:
        recommendations.append(
            "当前闭环处于可进化状态，可继续观察 few-shot 蒸馏是否带来评分贴近真实结果。"
        )
    if any(bool(item.get("changed_since_latest_snapshot")) for item in artifact_impacts):
        recommendations.append(
            "检测到部分闭环产物与最近一次快照不一致；若刚执行过回滚，请结合“治理影响体检”确认差异是否符合预期。"
        )
    preview_match_count = int(score_preview.get("matched_submission_count") or 0)
    avg_abs_delta_stored = main._to_float_or_none(score_preview.get("avg_abs_delta_stored"))
    avg_abs_delta_preview = main._to_float_or_none(score_preview.get("avg_abs_delta_preview"))
    if preview_match_count <= 0:
        recommendations.append(
            "当前暂无可同时关联最新评分报告与青天结果的样本，治理面板暂不能执行评分偏差试算。"
        )
    elif avg_abs_delta_stored is not None and avg_abs_delta_preview is not None:
        if avg_abs_delta_preview + 1e-6 < avg_abs_delta_stored:
            recommendations.append(
                f"只读试算显示当前校准器可将平均绝对偏差从 {avg_abs_delta_stored:.2f} 分收敛到 {avg_abs_delta_preview:.2f} 分，可在确认后再执行正式重评分。"
            )
        elif avg_abs_delta_preview > avg_abs_delta_stored + 1e-6:
            recommendations.append(
                f"只读试算显示当前校准器可能使平均绝对偏差从 {avg_abs_delta_stored:.2f} 分扩大到 {avg_abs_delta_preview:.2f} 分，建议先回看样本与版本快照。"
            )
    if bool(score_preview.get("requires_rule_rescore")):
        recommendations.append(
            "评分偏差试算当前仅覆盖校准总分层；由于权重、画像或进化逻辑已变化，维度分与完整总分仍需重评分后确认。"
        )
    sandbox_executed_count = int(sandbox_preview.get("executed_row_count") or 0)
    sandbox_avg_abs_delta_stored = main._to_float_or_none(
        sandbox_preview.get("avg_abs_delta_stored")
    )
    sandbox_avg_abs_delta = main._to_float_or_none(sandbox_preview.get("avg_abs_delta_sandbox"))
    sandbox_warning = str(sandbox_preview.get("constraints_warning") or "").strip()
    if sandbox_warning:
        recommendations.append(sandbox_warning)
    elif (
        sandbox_executed_count > 0
        and sandbox_avg_abs_delta_stored is not None
        and sandbox_avg_abs_delta is not None
    ):
        if sandbox_avg_abs_delta + 1e-6 < sandbox_avg_abs_delta_stored:
            recommendations.append(
                f"沙箱重评分显示当前完整体系可将平均绝对偏差从 {sandbox_avg_abs_delta_stored:.2f} 分收敛到 {sandbox_avg_abs_delta:.2f} 分，说明权重/画像/进化逻辑调整具有正向作用。"
            )
        elif sandbox_avg_abs_delta > sandbox_avg_abs_delta_stored + 1e-6:
            recommendations.append(
                f"沙箱重评分显示当前完整体系可能使平均绝对偏差从 {sandbox_avg_abs_delta_stored:.2f} 分扩大到 {sandbox_avg_abs_delta:.2f} 分，建议暂缓落库并先检查治理动作影响。"
            )
    if int(sandbox_preview.get("failed_row_count") or 0) > 0:
        recommendations.append(
            "部分沙箱重评分样本执行失败，请先查看错误明细后再决定是否继续治理操作。"
        )
    latest_project_calibrator_version = str(
        calibrator_state.get("latest_project_calibrator_version") or ""
    ).strip()
    latest_project_calibrator_mode = str(
        calibrator_state.get("latest_project_calibrator_deployment_mode") or ""
    ).strip()
    latest_project_auto_review = main._normalize_calibrator_auto_review_state(
        calibrator_state.get("latest_project_calibrator_auto_review")
    )
    current_calibrator_degraded = bool(evolution_summary.get("current_calibrator_degraded"))
    current_calibrator_rollback_candidate_version = str(
        evolution_summary.get("current_calibrator_rollback_candidate_version") or ""
    ).strip()
    if latest_project_calibrator_version:
        if latest_project_calibrator_mode == "bootstrap_auto_deploy":
            recommendations.append(
                "当前项目级校准器处于小样本 bootstrap 监控态，已可参与校准评分；建议继续补录真实评标样本，尽快升级为完整 CV 校准。"
            )
        elif latest_project_calibrator_mode == "bootstrap_candidate_only":
            if str(latest_project_auto_review.get("action") or "") == "rollback":
                recommendations.append(
                    "最新小样本 bootstrap 校准器在只读偏差复核中表现变差，系统已自动保留为候选未部署，当前仍沿用旧校准器或 prior。"
                )
            else:
                recommendations.append(
                    "最新小样本 bootstrap 校准器尚未正式部署，建议继续补录真实评标样本后再自动复核。"
                )
    if current_calibrator_degraded:
        recommendations.append(
            "当前项目级校准器近期误差已明显劣于规则基线，建议优先执行 V2 一键闭环。"
        )
        if current_calibrator_rollback_candidate_version:
            recommendations.append(
                f"当前项目级校准器存在历史回退候选 {current_calibrator_rollback_candidate_version}，如需保守自救可优先切回该版本。"
            )

    return {
        "project_id": project_id,
        "generated_at": main._now_iso(),
        "summary": {
            "ground_truth_count": len(all_rows),
            "active_ground_truth_count": len(active_rows),
            "blocked_ground_truth_count": blocked_count,
            "approved_extreme_ground_truth_count": approved_extreme_count,
            "rejected_extreme_ground_truth_count": rejected_extreme_count,
            "pending_extreme_ground_truth_count": pending_extreme_count,
            "manual_confirmation_required": bool(summary_guardrail.get("blocked")),
            "few_shot_recent_capture_count": captured_recent_count,
            "few_shot_adopted_count": few_shot_adopted_count,
            "few_shot_ignored_count": few_shot_ignored_count,
            "few_shot_pending_review_count": few_shot_pending_review_count,
            "few_shot_feature_version_count": int(
                next(
                    (
                        item.get("version_count")
                        for item in version_history
                        if str(item.get("artifact") or "") == "high_score_features"
                    ),
                    0,
                )
                or 0
            ),
            "latest_few_shot_version_id": str(
                next(
                    (
                        item.get("latest_version_id")
                        for item in version_history
                        if str(item.get("artifact") or "") == "high_score_features"
                    ),
                    "",
                )
                or ""
            ),
            "manual_override_hint": summary_guardrail.get("manual_override_hint"),
            "current_calibrator_version": calibrator_state.get("current_calibrator_version"),
            "current_calibrator_model_type": calibrator_state.get("current_calibrator_model_type"),
            "current_calibrator_source": calibrator_state.get("current_calibrator_source"),
            "current_calibrator_bootstrap_small_sample": bool(
                calibrator_state.get("current_calibrator_bootstrap_small_sample")
            ),
            "current_calibrator_deployment_mode": calibrator_state.get(
                "current_calibrator_deployment_mode"
            ),
            "current_calibrator_auto_review": calibrator_state.get("current_calibrator_auto_review")
            or {},
            "current_calibrator_degraded": current_calibrator_degraded,
            "current_calibrator_degradation_reason": evolution_summary.get(
                "current_calibrator_degradation_reason"
            ),
            "current_calibrator_recent_mae": evolution_summary.get("current_calibrator_recent_mae"),
            "current_calibrator_recent_rule_mae": evolution_summary.get(
                "current_calibrator_recent_rule_mae"
            ),
            "current_calibrator_recent_mae_delta_vs_rule": evolution_summary.get(
                "current_calibrator_recent_mae_delta_vs_rule"
            ),
            "current_calibrator_has_rollback_candidate": bool(
                evolution_summary.get("current_calibrator_has_rollback_candidate")
            ),
            "current_calibrator_rollback_candidate_version": evolution_summary.get(
                "current_calibrator_rollback_candidate_version"
            ),
            "current_calibrator_rollback_candidate_model_type": evolution_summary.get(
                "current_calibrator_rollback_candidate_model_type"
            ),
            "current_calibrator_rollback_candidate_deployment_mode": evolution_summary.get(
                "current_calibrator_rollback_candidate_deployment_mode"
            ),
            "current_calibrator_rollback_candidate_cv_mae": evolution_summary.get(
                "current_calibrator_rollback_candidate_cv_mae"
            ),
            "latest_project_calibrator_version": calibrator_state.get(
                "latest_project_calibrator_version"
            ),
            "latest_project_calibrator_model_type": calibrator_state.get(
                "latest_project_calibrator_model_type"
            ),
            "latest_project_calibrator_deployed": bool(
                calibrator_state.get("latest_project_calibrator_deployed")
            ),
            "latest_project_calibrator_bootstrap_small_sample": bool(
                calibrator_state.get("latest_project_calibrator_bootstrap_small_sample")
            ),
            "latest_project_calibrator_deployment_mode": calibrator_state.get(
                "latest_project_calibrator_deployment_mode"
            ),
            "latest_project_calibrator_auto_review": calibrator_state.get(
                "latest_project_calibrator_auto_review"
            )
            or {},
        },
        "blocked_samples": blocked_samples,
        "approved_samples": approved_samples,
        "few_shot_recent": few_shot_recent,
        "adopted_few_shot": adopted_few_shot,
        "version_history": version_history,
        "artifact_impacts": artifact_impacts,
        "score_preview": score_preview,
        "sandbox_preview": sandbox_preview,
        "recommendations": recommendations[:12],
    }


def build_feedback_governance_version_preview(
    project_id: str,
    project: Dict[str, object],
    *,
    artifact: str,
    version_id: str,
) -> Dict[str, object]:
    main = _main()
    spec = main._resolve_governance_artifact_spec(artifact)
    path = spec.get("path")
    default_payload = copy.deepcopy(spec.get("default_payload"))
    current_payload = main._load_governance_artifact_payload(artifact)
    preview_payload = main.load_json_version(path, version_id, default_payload)
    current_summary = main._summarize_versioned_artifact_payload(
        artifact,
        current_payload,
        project_id=project_id,
    )
    preview_summary = main._summarize_versioned_artifact_payload(
        artifact,
        preview_payload,
        project_id=project_id,
    )
    versions = main.list_json_versions(path)
    version_meta = next(
        (row for row in versions if str(row.get("version_id") or "") == str(version_id)),
        {},
    )
    governance_payload = main._build_feedback_governance_report(
        project_id,
        project,
        artifact_payload_overrides={artifact: preview_payload},
    )
    delta_vs_current = main._artifact_summary_delta(preview_summary, current_summary)
    matches_current = main._artifact_payload_fingerprint(
        current_payload
    ) == main._artifact_payload_fingerprint(preview_payload)
    recommendations: List[str] = []
    if matches_current:
        recommendations.append("所选历史版本与当前在线产物一致，本次只读预演不会引入变化。")
    else:
        recommendations.append(
            f"当前为只读预演：若把 {artifact} 切换到版本 {version_id}，下方治理面板将展示对应的评分和治理影响。"
        )
    sandbox_preview = (
        governance_payload.get("sandbox_preview")
        if isinstance(governance_payload.get("sandbox_preview"), dict)
        else {}
    )
    if int(main._to_float_or_none(sandbox_preview.get("executed_row_count")) or 0) <= 0:
        recommendations.append(
            "本次预演未形成有效沙箱重评分样本，请结合当前版本快照和治理影响体检一起判断。"
        )
    if bool(sandbox_preview.get("constraints_warning")):
        recommendations.append(str(sandbox_preview.get("constraints_warning") or ""))
    return {
        "ok": True,
        "project_id": project_id,
        "artifact": artifact,
        "version_id": version_id,
        "version_created_at": str(version_meta.get("created_at") or "") or None,
        "generated_at": main._now_iso(),
        "current_summary": current_summary,
        "preview_summary": preview_summary,
        "delta_vs_current": delta_vs_current,
        "matches_current": matches_current,
        "governance": governance_payload,
        "recommendations": recommendations[:8],
    }


def build_feedback_governance_action_preview(
    project_id: str,
    project: Dict[str, object],
    *,
    record_id: str,
    preview_type: str,
    action: str,
    note: str,
    rerun_closed_loop: bool = False,
) -> Dict[str, object]:
    main = _main()
    records = copy.deepcopy(main.load_ground_truth())
    target_record = next(
        (
            row
            for row in records
            if str(row.get("project_id") or "") == str(project_id)
            and str(row.get("id") or "") == str(record_id)
        ),
        None,
    )
    if target_record is None:
        raise HTTPException(status_code=404, detail="真实评标记录不存在")

    action_text = str(action or "").strip().lower()
    note_text = str(note or "").strip()
    if preview_type == "guardrail":
        current_state = main._extract_feedback_guardrail(target_record)
        preview_state = main._apply_feedback_guardrail_review(
            target_record,
            action=action_text,
            note=note_text,
        )
        target_record["feedback_guardrail"] = preview_state
        preview_kind = "guardrail"
    elif preview_type == "few_shot":
        current_state = main._normalize_few_shot_distillation_state(
            target_record.get("few_shot_distillation")
        )
        preview_state = main._apply_few_shot_review(
            target_record,
            action=action_text,
            note=note_text,
        )
        target_record["few_shot_distillation"] = preview_state
        preview_kind = "few_shot"
    else:
        raise HTTPException(status_code=422, detail="preview_type 仅支持 guardrail 或 few_shot")

    governance = main._build_feedback_governance_report(
        project_id,
        project,
        ground_truth_rows_override=records,
    )
    recommendations: List[str] = []
    if preview_kind == "guardrail":
        if action_text == "approve":
            recommendations.append(
                "本次仅为只读预演：正式提交前不会执行真实闭环，也不会写入权重、校准器或 few-shot 特征。"
            )
            if bool(rerun_closed_loop):
                recommendations.append(
                    "即使你勾选了重跑闭环，本次预演也只展示放行后的治理状态；正式执行后才会触发学习进化。"
                )
        else:
            recommendations.append("极端偏差审核预演只会改变治理状态，不会直接改写当前评分产物。")
    else:
        recommendations.append(
            "few-shot 采纳预演只改变治理登记状态，不会直接改写当前 high_score_features；如需评分变化，仍需正式闭环刷新。"
        )
    score_preview = governance.get("score_preview") if isinstance(governance, dict) else {}
    sandbox_preview = governance.get("sandbox_preview") if isinstance(governance, dict) else {}
    if (
        isinstance(score_preview, dict)
        and int(score_preview.get("matched_submission_count") or 0) <= 0
    ):
        recommendations.append(
            "当前预演没有关联到可试算的评分样本，建议结合治理摘要与版本快照一起判断。"
        )
    if (
        isinstance(sandbox_preview, dict)
        and str(sandbox_preview.get("constraints_warning") or "").strip()
    ):
        recommendations.append(str(sandbox_preview.get("constraints_warning") or ""))
    return {
        "ok": True,
        "project_id": project_id,
        "record_id": record_id,
        "preview_type": preview_kind,
        "requested_action": action_text,
        "generated_at": main._now_iso(),
        "current_state": current_state,
        "preview_state": preview_state,
        "governance": governance,
        "recommendations": recommendations[:8],
    }


def execute_feedback_guardrail_review(
    project_id: str,
    record_id: str,
    *,
    action: str,
    note: str,
    rerun_closed_loop: bool,
    locale: str,
) -> Dict[str, object]:
    main = _main()
    records = main.load_ground_truth()
    record = next(
        (
            row
            for row in records
            if str(row.get("project_id") or "") == str(project_id)
            and str(row.get("id") or "") == str(record_id)
        ),
        None,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="真实评标记录不存在")

    updated_guardrail = main._apply_feedback_guardrail_review(
        record,
        action=str(action or "").strip().lower(),
        note=str(note or "").strip(),
    )
    updated_record = main._persist_ground_truth_record_fields(
        project_id,
        record_id,
        updates={"feedback_guardrail": updated_guardrail},
    )
    closed_loop: Dict[str, object] = {}
    if str(updated_guardrail.get("manual_review_status") or "") == "approved" and bool(
        rerun_closed_loop
    ):
        closed_loop = main._run_feedback_closed_loop_safe(
            project_id,
            locale=locale,
            trigger="ground_truth_manual_review",
            ground_truth_record_ids=[record_id],
        )
        updated_record = main._persist_ground_truth_record_fields(
            project_id,
            record_id,
            updates={"feedback_closed_loop": closed_loop},
        )
    else:
        existing_closed_loop = updated_record.get("feedback_closed_loop")
        closed_loop = existing_closed_loop if isinstance(existing_closed_loop, dict) else {}
    return {
        "ok": True,
        "project_id": project_id,
        "record_id": record_id,
        "feedback_guardrail": main._extract_feedback_guardrail(updated_record),
        "feedback_closed_loop": closed_loop,
        "updated_at": str(updated_record.get("updated_at") or main._now_iso()),
    }


def execute_feedback_few_shot_review(
    project_id: str,
    record_id: str,
    *,
    action: str,
    note: str,
) -> Dict[str, object]:
    main = _main()
    records = main.load_ground_truth()
    record = next(
        (
            row
            for row in records
            if str(row.get("project_id") or "") == str(project_id)
            and str(row.get("id") or "") == str(record_id)
        ),
        None,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="真实评标记录不存在")
    updated_distillation = main._apply_few_shot_review(
        record,
        action=str(action or "").strip().lower(),
        note=str(note or "").strip(),
    )
    updated_record = main._persist_ground_truth_record_fields(
        project_id,
        record_id,
        updates={"few_shot_distillation": updated_distillation},
    )
    main._sync_feature_governance_review(
        feature_ids=[
            str(item or "").strip()
            for item in (updated_distillation.get("feature_ids") or [])
            if str(item or "").strip()
        ],
        review_status=str(updated_distillation.get("manual_review_status") or "pending"),
        reviewed_at=str(updated_distillation.get("manual_reviewed_at") or "") or None,
    )
    return {
        "ok": True,
        "project_id": project_id,
        "record_id": record_id,
        "few_shot_distillation": main._normalize_few_shot_distillation_state(
            updated_record.get("few_shot_distillation")
        ),
        "updated_at": str(updated_record.get("updated_at") or main._now_iso()),
    }
