from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import HTTPException

from app.application.runtime_facade import RuntimeModuleFacade
from app.application.storage_access import StorageAccess
from app.bootstrap.storage import get_storage_access
from app.domain.learning.feedback_analysis import (
    build_high_consensus_auto_approval as evaluate_high_consensus_auto_approval,
)
from app.domain.learning.feedback_analysis import (
    build_learning_quality_gate_payload,
)
from app.domain.learning.feedback_analysis import (
    resolve_report_predicted_score_100 as resolve_predicted_score_100,
)
from app.domain.learning.feedback_guardrails import (
    build_feedback_guardrail_delta_text,
    extract_feedback_guardrail,
    extract_learning_quality_gate,
    feedback_guardrail_blocks_training,
    normalize_feedback_guardrail_state,
)
from app.domain.learning.feedback_state import (
    normalize_few_shot_distillation_state,
    normalize_learning_quality_gate_state,
)
from app.domain.learning.few_shot_support import (
    collect_dimension_evidence_texts,
    collect_dimension_guidance_texts,
    flatten_ground_truth_qualitative_tags,
    select_ground_truth_few_shot_dimensions,
)
from app.domain.learning.ground_truth_records import (
    DEFAULT_SCORE_SCALE_MAX,
    convert_score_from_100,
    ground_truth_record_for_learning,
    normalize_score_scale_max,
    resolve_project_score_scale_max,
    to_float_or_none,
)
from app.domain.learning.project_feedback_views import summarize_project_feedback_guardrail

CONSENSUS_AUTO_APPROVE_MIN_JUDGES = 7
CONSENSUS_AUTO_APPROVE_MAX_SCORE_SPAN = 0.4
CONSENSUS_AUTO_APPROVE_MAX_SCORE_STDDEV = 0.12
CONSENSUS_AUTO_APPROVE_MAX_FINAL_DELTA = 0.15


def _main(storage: StorageAccess | None = None):
    from app.application import runtime as main_mod

    return RuntimeModuleFacade(main_mod, storage=storage or get_storage_access())


def _persist_ground_truth_record_fields(
    main: RuntimeModuleFacade,
    project_id: str,
    record_id: str,
    *,
    updates: Dict[str, object],
    updated_at: str,
) -> Dict[str, object]:
    records = main.load_ground_truth()
    updated_row = None
    for idx, row in enumerate(records):
        if str(row.get("project_id") or "") != str(project_id):
            continue
        if str(row.get("id") or "") != str(record_id):
            continue
        merged = dict(row)
        merged.update(updates)
        merged["updated_at"] = updated_at
        records[idx] = merged
        updated_row = merged
        break
    if updated_row is None:
        raise HTTPException(status_code=404, detail="真实评标记录不存在")
    main.save_ground_truth(records)
    return updated_row


def auto_update_feature_confidence_on_ground_truth(
    *,
    report: Dict[str, object],
    gt_record: Dict[str, object],
    project_score_scale_max: int,
) -> Dict[str, object]:
    main = _main()
    applied_feature_ids = main._collect_applied_feature_ids_from_report(
        report,
        project_id=str(gt_record.get("project_id") or "").strip(),
    )
    if not applied_feature_ids:
        return {"updated": 0, "retired": 0, "reason": "no_applied_feature_ids"}

    gt_for_learning = ground_truth_record_for_learning(
        gt_record,
        default_score_scale_max=project_score_scale_max,
    )
    actual_score_100 = to_float_or_none(gt_for_learning.get("final_score"))
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
    update_result["current_score_100"] = round(float(pred_score_100), 2)
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
    return resolve_predicted_score_100(
        report,
        project_score_scale_max=project_score_scale_max,
    )


def _build_high_consensus_auto_approval(
    *,
    gt_for_learning: Dict[str, object],
) -> Dict[str, object]:
    return evaluate_high_consensus_auto_approval(
        gt_for_learning=gt_for_learning,
        min_judges=CONSENSUS_AUTO_APPROVE_MIN_JUDGES,
        max_score_span=CONSENSUS_AUTO_APPROVE_MAX_SCORE_SPAN,
        max_score_stddev=CONSENSUS_AUTO_APPROVE_MAX_SCORE_STDDEV,
        max_final_delta=CONSENSUS_AUTO_APPROVE_MAX_FINAL_DELTA,
    )


def build_ground_truth_feedback_guardrail(
    *,
    report: Dict[str, object],
    gt_record: Dict[str, object],
    project_score_scale_max: int,
) -> Dict[str, object]:
    main = _main()
    project_score_scale = normalize_score_scale_max(
        project_score_scale_max,
        default=DEFAULT_SCORE_SCALE_MAX,
    )
    gt_for_learning = ground_truth_record_for_learning(
        gt_record,
        default_score_scale_max=project_score_scale_max,
    )
    actual_score_100 = to_float_or_none(gt_for_learning.get("final_score"))
    predicted_score_100 = resolve_report_predicted_score_100(
        report,
        project_score_scale_max=project_score_scale_max,
    )
    if actual_score_100 is None or predicted_score_100 is None:
        return normalize_feedback_guardrail_state(
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
                "current_score_100": (
                    round(float(predicted_score_100), 2)
                    if predicted_score_100 is not None
                    else None
                ),
                "score_scale_max": project_score_scale,
                "actual_score_raw": gt_for_learning.get("final_score_raw"),
                "predicted_score_raw": (
                    convert_score_from_100(predicted_score_100, project_score_scale)
                    if predicted_score_100 is not None
                    else None
                ),
                "current_score_raw": (
                    convert_score_from_100(predicted_score_100, project_score_scale)
                    if predicted_score_100 is not None
                    else None
                ),
            },
            default_score_scale_max=project_score_scale,
            default_threshold_ratio=float(main.DEFAULT_FEEDBACK_EXTREME_DELTA_RATIO),
        )

    actual_score_raw = to_float_or_none(gt_for_learning.get("final_score_raw"))
    if actual_score_raw is None:
        actual_score_raw = convert_score_from_100(actual_score_100, project_score_scale)
    predicted_score_raw = convert_score_from_100(predicted_score_100, project_score_scale)
    abs_delta_100 = abs(float(actual_score_100) - float(predicted_score_100))
    abs_delta_raw = (
        abs(float(actual_score_raw) - float(predicted_score_raw))
        if actual_score_raw is not None and predicted_score_raw is not None
        else convert_score_from_100(abs_delta_100, project_score_scale)
    )
    relative_delta_ratio = abs_delta_100 / 100.0
    blocked = relative_delta_ratio > float(main.DEFAULT_FEEDBACK_EXTREME_DELTA_RATIO)
    consensus_auto_approval = _build_high_consensus_auto_approval(gt_for_learning=gt_for_learning)
    warning_message = ""
    if blocked and bool(consensus_auto_approval.get("eligible")):
        delta_text = build_feedback_guardrail_delta_text(
            {
                "score_scale_max": project_score_scale,
                "abs_delta_raw": abs_delta_raw,
                "relative_delta_ratio": relative_delta_ratio,
            },
            default_score_scale_max=project_score_scale,
        )
        return normalize_feedback_guardrail_state(
            {
                "blocked": False,
                "threshold_blocked": True,
                "status": "auto_approved_consensus",
                "requires_manual_confirmation": False,
                "actual_score_100": round(float(actual_score_100), 2),
                "predicted_score_100": round(float(predicted_score_100), 2),
                "current_score_100": round(float(predicted_score_100), 2),
                "abs_delta_100": round(float(abs_delta_100), 2),
                "actual_score_raw": actual_score_raw,
                "predicted_score_raw": predicted_score_raw,
                "current_score_raw": predicted_score_raw,
                "abs_delta_raw": abs_delta_raw,
                "score_scale_max": project_score_scale,
                "relative_delta_ratio": round(float(relative_delta_ratio), 4),
                "auto_approved_consensus": True,
                "judge_score_avg": consensus_auto_approval.get("avg_score"),
                "judge_score_span": consensus_auto_approval.get("score_span"),
                "judge_score_stddev": consensus_auto_approval.get("score_stddev"),
                "final_vs_judge_avg_abs_delta": consensus_auto_approval.get(
                    "final_vs_avg_abs_delta"
                ),
                "manual_review": {
                    "status": "approved",
                    "note": "high_consensus_auto_approved",
                    "reviewed_at": main._now_iso(),
                },
                "warning_message": (
                    f"预测与真实总分偏差 {delta_text or '未提供'}，"
                    f"但该样本 {int(consensus_auto_approval.get('judge_count') or 0)} "
                    f"位评委打分高度一致（跨度 {float(consensus_auto_approval.get('score_span') or 0.0):.2f}，"
                    f"标准差 {float(consensus_auto_approval.get('score_stddev') or 0.0):.4f}），"
                    "已自动放行进入学习闭环。"
                ),
                "manual_override_hint": None,
            },
            default_score_scale_max=project_score_scale,
            default_threshold_ratio=float(main.DEFAULT_FEEDBACK_EXTREME_DELTA_RATIO),
        )
    if blocked:
        delta_text = build_feedback_guardrail_delta_text(
            {
                "score_scale_max": project_score_scale,
                "abs_delta_raw": abs_delta_raw,
                "relative_delta_ratio": relative_delta_ratio,
            },
            default_score_scale_max=project_score_scale,
        )
        warning_message = (
            f"预测与真实总分偏差 {delta_text or '未提供'}，"
            "已暂停自动调权/自动校准，请人工确认后再执行「学习进化」或「一键闭环执行」。"
        )
    return normalize_feedback_guardrail_state(
        {
            "blocked": blocked,
            "threshold_blocked": blocked,
            "status": "blocked" if blocked else "accepted",
            "requires_manual_confirmation": blocked,
            "actual_score_100": round(float(actual_score_100), 2),
            "predicted_score_100": round(float(predicted_score_100), 2),
            "current_score_100": round(float(predicted_score_100), 2),
            "abs_delta_100": round(float(abs_delta_100), 2),
            "actual_score_raw": actual_score_raw,
            "predicted_score_raw": predicted_score_raw,
            "current_score_raw": predicted_score_raw,
            "abs_delta_raw": abs_delta_raw,
            "score_scale_max": project_score_scale,
            "relative_delta_ratio": round(float(relative_delta_ratio), 4),
            "warning_message": warning_message or None,
            "manual_override_hint": "confirm_extreme_sample=1" if blocked else None,
        },
        default_score_scale_max=project_score_scale,
        default_threshold_ratio=float(main.DEFAULT_FEEDBACK_EXTREME_DELTA_RATIO),
    )


def build_ground_truth_learning_quality_gate(
    *,
    report: Dict[str, object],
    gt_record: Dict[str, object],
    project_score_scale_max: int,
) -> Dict[str, object]:
    del gt_record, project_score_scale_max
    main = _main()
    return normalize_learning_quality_gate_state(
        build_learning_quality_gate_payload(
            report,
            min_awareness_score=float(main.DEFAULT_LEARNING_MIN_AWARENESS_SCORE),
            min_evidence_hits=int(main.DEFAULT_LEARNING_MIN_EVIDENCE_HITS),
        ),
        default_min_awareness_score=float(main.DEFAULT_LEARNING_MIN_AWARENESS_SCORE),
        default_min_evidence_hits=int(main.DEFAULT_LEARNING_MIN_EVIDENCE_HITS),
    )


def capture_ground_truth_few_shot_features(
    *,
    report: Dict[str, object],
    gt_record: Dict[str, object],
    project_score_scale_max: int,
    feedback_guardrail: Dict[str, object],
    learning_quality_gate: Dict[str, object],
    feature_confidence_update: Dict[str, object],
) -> Dict[str, object]:
    main = _main()
    normalized_guardrail = normalize_feedback_guardrail_state(
        feedback_guardrail,
        default_score_scale_max=DEFAULT_SCORE_SCALE_MAX,
        default_threshold_ratio=float(main.DEFAULT_FEEDBACK_EXTREME_DELTA_RATIO),
    )
    if (
        bool(normalized_guardrail.get("threshold_blocked"))
        and str(normalized_guardrail.get("manual_review_status") or "").strip().lower()
        != "approved"
    ):
        return {"captured": 0, "reason": "guardrail_blocked"}
    if bool(learning_quality_gate.get("blocked")):
        return {
            "captured": 0,
            "reason": "learning_quality_blocked",
            "learning_quality_gate": normalize_learning_quality_gate_state(
                learning_quality_gate,
                default_min_awareness_score=float(main.DEFAULT_LEARNING_MIN_AWARENESS_SCORE),
                default_min_evidence_hits=int(main.DEFAULT_LEARNING_MIN_EVIDENCE_HITS),
            ),
        }

    gt_for_learning = ground_truth_record_for_learning(
        gt_record,
        default_score_scale_max=project_score_scale_max,
    )
    actual_score_100 = float(to_float_or_none(gt_for_learning.get("final_score")) or 0.0)
    if actual_score_100 < float(main.DEFAULT_FEW_SHOT_MIN_HIGH_SCORE_100):
        return {
            "captured": 0,
            "reason": "below_high_score_threshold",
            "actual_score_100": round(actual_score_100, 2),
            "min_high_score_threshold_100": round(
                float(main.DEFAULT_FEW_SHOT_MIN_HIGH_SCORE_100), 2
            ),
        }

    candidate_dimensions = select_ground_truth_few_shot_dimensions(
        report=report,
        feature_confidence_update=feature_confidence_update,
    )
    if not candidate_dimensions:
        return {"captured": 0, "reason": "no_candidate_dimensions"}

    tags = flatten_ground_truth_qualitative_tags(gt_record)
    existing_distillation = normalize_few_shot_distillation_state(
        gt_record.get("few_shot_distillation")
    )
    existing_review_status = (
        str(existing_distillation.get("manual_review_status") or "").strip().lower()
    )
    consensus_auto_approval = _build_high_consensus_auto_approval(gt_for_learning=gt_for_learning)
    feature_governance_status = "pending"
    if existing_review_status in {"adopted", "ignored"}:
        feature_governance_status = existing_review_status
    elif bool(consensus_auto_approval.get("eligible")):
        feature_governance_status = "auto_adopted"
    distilled_features = []
    feature_ids: List[str] = []
    for dim_id in candidate_dimensions:
        dim_name = ((main.DIMENSIONS.get(dim_id) or {}).get("name") or dim_id).strip()
        evidence_texts = collect_dimension_evidence_texts(report, dimension_id=dim_id)
        guidance_texts = collect_dimension_guidance_texts(report, dimension_id=dim_id)
        source_highlights = [*tags[:3], *evidence_texts[:3], *guidance_texts[:2]]
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
            governance_status=feature_governance_status,
            source_record_ids=[str(gt_record.get("id") or "").strip()],
            source_project_ids=[str(gt_record.get("project_id") or "").strip()],
            source_highlights=source_highlights,
        )
        if feature is None:
            continue
        distilled_features.append(feature)
        feature_ids.append(str(feature.feature_id or ""))

    if not distilled_features:
        return {"captured": 0, "reason": "feature_distillation_empty"}

    upsert_result = main.upsert_distilled_features(distilled_features)
    resolved_feature_ids = [
        str(item or "").strip()
        for item in (upsert_result.get("resolved_feature_ids") or [])
        if str(item or "").strip()
    ]
    if len(resolved_feature_ids) == len(feature_ids):
        feature_ids = resolved_feature_ids
    response: Dict[str, object] = {
        "captured": len(distilled_features),
        "reason": "captured",
        "dimension_ids": candidate_dimensions,
        "feature_ids": feature_ids,
        "actual_score_100": round(actual_score_100, 2),
        "min_high_score_threshold_100": round(float(main.DEFAULT_FEW_SHOT_MIN_HIGH_SCORE_100), 2),
        "upsert": upsert_result,
    }
    if existing_review_status in {"adopted", "ignored"}:
        response["manual_review"] = dict(existing_distillation.get("manual_review") or {})
    elif bool(consensus_auto_approval.get("eligible")):
        response["auto_adopted_consensus"] = True
        response["judge_score_avg"] = consensus_auto_approval.get("avg_score")
        response["judge_score_span"] = consensus_auto_approval.get("score_span")
        response["judge_score_stddev"] = consensus_auto_approval.get("score_stddev")
        response["final_vs_judge_avg_abs_delta"] = consensus_auto_approval.get(
            "final_vs_avg_abs_delta"
        )
        response["manual_review"] = {
            "status": "adopted",
            "note": "high_consensus_auto_adopted",
            "reviewed_at": main._now_iso(),
        }
    return response


def sync_ground_truth_record_to_qingtian(
    project_id: str,
    gt_record: Dict[str, object],
    *,
    storage: StorageAccess | None = None,
) -> Dict[str, object]:
    main = _main(storage)
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
    project_score_scale = resolve_project_score_scale_max(project)
    gt_for_learning = ground_truth_record_for_learning(
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
            existing_guardrail = normalize_feedback_guardrail_state(
                gt_record.get("feedback_guardrail"),
                default_score_scale_max=project_score_scale,
                default_threshold_ratio=float(main.DEFAULT_FEEDBACK_EXTREME_DELTA_RATIO),
            )
            if bool(feedback_guardrail.get("threshold_blocked")) and str(
                existing_guardrail.get("manual_review_status") or ""
            ).strip().lower() in {"pending", "approved", "rejected"}:
                feedback_guardrail["manual_review"] = dict(
                    existing_guardrail.get("manual_review") or {}
                )
        except Exception as exc:
            feedback_guardrail = {
                "blocked": False,
                "status": "guardrail_error",
                "requires_manual_confirmation": False,
                "error": str(exc),
            }
    learning_quality_gate: Dict[str, object] = {"blocked": False, "status": "not_executed"}
    if isinstance(report_for_feedback, dict):
        try:
            learning_quality_gate = build_ground_truth_learning_quality_gate(
                report=report_for_feedback,
                gt_record=gt_record,
                project_score_scale_max=project_score_scale,
            )
        except Exception as exc:
            learning_quality_gate = {
                "blocked": False,
                "status": "learning_quality_gate_error",
                "warning_message": str(exc),
            }
    feature_confidence_update: Dict[str, object] = {
        "updated": 0,
        "retired": 0,
        "reason": "not_executed",
    }
    if bool(
        feedback_guardrail_blocks_training(
            feedback_guardrail,
            default_score_scale_max=project_score_scale,
            default_threshold_ratio=float(main.DEFAULT_FEEDBACK_EXTREME_DELTA_RATIO),
        )
    ):
        feature_confidence_update = {
            "updated": 0,
            "retired": 0,
            "reason": "training_guard_blocked",
            "actual_score_100": feedback_guardrail.get("actual_score_100"),
            "predicted_score_100": feedback_guardrail.get("predicted_score_100"),
            "current_score_100": feedback_guardrail.get("current_score_100"),
        }
    elif bool(learning_quality_gate.get("blocked")):
        feature_confidence_update = {
            "updated": 0,
            "retired": 0,
            "reason": "learning_quality_blocked",
            "learning_quality_gate": normalize_learning_quality_gate_state(
                learning_quality_gate,
                default_min_awareness_score=float(main.DEFAULT_LEARNING_MIN_AWARENESS_SCORE),
                default_min_evidence_hits=int(main.DEFAULT_LEARNING_MIN_EVIDENCE_HITS),
            ),
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
    if (
        bool(feedback_guardrail.get("threshold_blocked"))
        and str(feedback_guardrail.get("manual_review_status") or "").strip().lower() != "approved"
    ):
        few_shot_distillation = {"captured": 0, "reason": "guardrail_blocked"}
    elif bool(learning_quality_gate.get("blocked")):
        few_shot_distillation = {
            "captured": 0,
            "reason": "learning_quality_blocked",
            "learning_quality_gate": normalize_learning_quality_gate_state(
                learning_quality_gate,
                default_min_awareness_score=float(main.DEFAULT_LEARNING_MIN_AWARENESS_SCORE),
                default_min_evidence_hits=int(main.DEFAULT_LEARNING_MIN_EVIDENCE_HITS),
            ),
        }
    elif isinstance(report_for_feedback, dict):
        try:
            few_shot_distillation = capture_ground_truth_few_shot_features(
                report=report_for_feedback,
                gt_record=gt_record,
                project_score_scale_max=project_score_scale,
                feedback_guardrail=feedback_guardrail,
                learning_quality_gate=learning_quality_gate,
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
            row["feedback_guardrail"] = normalize_feedback_guardrail_state(
                feedback_guardrail,
                default_score_scale_max=project_score_scale,
                default_threshold_ratio=float(main.DEFAULT_FEEDBACK_EXTREME_DELTA_RATIO),
            )
            row["learning_quality_gate"] = normalize_learning_quality_gate_state(
                learning_quality_gate,
                default_min_awareness_score=float(main.DEFAULT_LEARNING_MIN_AWARENESS_SCORE),
                default_min_evidence_hits=int(main.DEFAULT_LEARNING_MIN_EVIDENCE_HITS),
            )
            row["feature_confidence_update"] = feature_confidence_update
            row["few_shot_distillation"] = normalize_few_shot_distillation_state(
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
                    "project_id": project_id,
                    "source": gt_record.get("source"),
                    "judge_scores": gt_record.get("judge_scores"),
                    "final_score": gt_record.get("final_score"),
                    "final_score_raw": gt_for_learning.get("final_score_raw"),
                    "final_score_100": gt_for_learning.get("final_score"),
                    "score_scale_max": gt_for_learning.get("score_scale_max"),
                    "feedback_guardrail": feedback_guardrail,
                    "learning_quality_gate": learning_quality_gate,
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
        matched_qt["qingtian_model_version"] = str(
            project.get("qingtian_model_version") or main.DEFAULT_QINGTIAN_MODEL_VERSION
        )
        matched_qt["qt_total_score"] = float(gt_for_learning.get("final_score", 0.0))
        matched_qt["qt_dim_scores"] = None
        matched_qt["qt_reasons"] = [
            {
                "kind": "ground_truth",
                "text": f"评委分: {gt_record.get('judge_scores')}",
            }
        ]
        raw_payload["ground_truth_record_id"] = source_gt_id
        raw_payload["project_id"] = project_id
        raw_payload["source"] = gt_record.get("source")
        raw_payload["judge_scores"] = gt_record.get("judge_scores")
        raw_payload["final_score"] = gt_record.get("final_score")
        raw_payload["final_score_raw"] = gt_for_learning.get("final_score_raw")
        raw_payload["final_score_100"] = gt_for_learning.get("final_score")
        raw_payload["score_scale_max"] = gt_for_learning.get("score_scale_max")
        raw_payload["feedback_guardrail"] = feedback_guardrail
        raw_payload["learning_quality_gate"] = learning_quality_gate
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
    gt_record["learning_quality_gate"] = learning_quality_gate
    gt_record["few_shot_distillation"] = few_shot_distillation
    gt_record["feature_confidence_update"] = feature_confidence_update
    return {
        "feedback_guardrail": feedback_guardrail,
        "learning_quality_gate": learning_quality_gate,
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
    storage: StorageAccess | None = None,
) -> Dict[str, object]:
    main = _main(storage)
    result: Dict[str, object] = {
        "ok": True,
        "project_id": project_id,
        "trigger": trigger,
        "weight_update": {"updated": False},
        "weight_sync_to_evolution": {"synced": False},
        "auto_rescore": {"ok": False, "skipped": True, "reason": "weights_not_synced"},
        "auto_run": None,
        "evolution_refresh": {"refreshed": False},
    }
    feedback_guardrail = summarize_project_feedback_guardrail(
        project_id,
        record_ids=ground_truth_record_ids,
        rows=main.load_ground_truth(),
        default_score_scale_max=DEFAULT_SCORE_SCALE_MAX,
        default_threshold_ratio=float(main.DEFAULT_FEEDBACK_EXTREME_DELTA_RATIO),
    )
    result["feedback_guardrail"] = feedback_guardrail
    selected_rows = [
        row
        for row in main.load_ground_truth()
        if str(row.get("project_id") or "") == str(project_id)
        and (
            not ground_truth_record_ids
            or str(row.get("id") or "").strip()
            in {str(item or "").strip() for item in ground_truth_record_ids}
        )
    ]
    training_blocked_rows = [
        row
        for row in selected_rows
        if feedback_guardrail_blocks_training(
            row,
            default_score_scale_max=DEFAULT_SCORE_SCALE_MAX,
            default_threshold_ratio=float(main.DEFAULT_FEEDBACK_EXTREME_DELTA_RATIO),
        )
    ]
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

    if training_blocked_rows:
        blocked_record_ids = [str(row.get("id") or "").strip() for row in training_blocked_rows]
        deltas = [
            float(
                to_float_or_none(
                    extract_feedback_guardrail(
                        row,
                        default_score_scale_max=DEFAULT_SCORE_SCALE_MAX,
                        default_threshold_ratio=float(main.DEFAULT_FEEDBACK_EXTREME_DELTA_RATIO),
                    ).get("abs_delta_100")
                )
                or 0.0
            )
            for row in training_blocked_rows
        ]
        max_abs_delta_100 = round(max(deltas) if deltas else 0.0, 2)
        result["training_blocked"] = True
        result["auto_update_skipped"] = True
        result["anomaly_warning"] = {
            "code": "extreme_delta_training_blocked",
            "blocked_record_ids": blocked_record_ids,
            "message": "检测到异常偏差警告，已拒绝自动更新底层权重与校准器。",
            "max_abs_delta_100": max_abs_delta_100,
        }
        main.logger.warning(
            "feedback_training_blocked project_id=%s record_ids=%s max_abs_delta_100=%.2f trigger=%s",
            project_id,
            blocked_record_ids,
            max_abs_delta_100,
            trigger,
        )
        result["weight_update"] = {
            "updated": False,
            "reason": "extreme_delta_training_blocked",
            "blocked_record_ids": blocked_record_ids,
        }
        result["weight_sync_to_evolution"] = {
            "synced": False,
            "reason": "extreme_delta_training_blocked",
        }
        result["auto_run"] = {
            "ok": False,
            "skipped": True,
            "reason": "extreme_delta_training_blocked",
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

    project = next(
        (row for row in main.load_projects() if str(row.get("id") or "") == str(project_id)),
        None,
    )
    project_submissions = [
        row
        for row in main.load_submissions()
        if str(row.get("project_id") or "") == str(project_id)
    ]
    if bool((result.get("weight_sync_to_evolution") or {}).get("synced")) and project_submissions:
        try:
            rescore_resp = main._rescore_project_submissions_internal(
                project_id,
                main.RescoreRequest(
                    scoring_engine_version=str(
                        (project or {}).get("scoring_engine_version_locked") or "v2"
                    ),
                    scope="project",
                    score_scale_max=resolve_project_score_scale_max(project or {}),
                    force_unlock=True,
                ),
                locale=locale,
                run_feedback_closed_loop=False,
                history_trigger="feedback_closed_loop_rescore",
            )
            if hasattr(rescore_resp, "model_dump"):
                result["auto_rescore"] = rescore_resp.model_dump()
            elif isinstance(rescore_resp, dict):
                result["auto_rescore"] = dict(rescore_resp)
            else:
                result["auto_rescore"] = {
                    "ok": bool(getattr(rescore_resp, "ok", False)),
                    "project_id": project_id,
                }
        except Exception as exc:
            result["auto_rescore"] = {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
            result["ok"] = False
    elif bool((result.get("weight_sync_to_evolution") or {}).get("synced")):
        result["auto_rescore"] = {
            "ok": False,
            "skipped": True,
            "reason": "no_project_submissions",
        }

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
    storage: StorageAccess | None = None,
) -> Dict[str, object]:
    main = _main(storage)
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
    storage: StorageAccess | None = None,
) -> Dict[str, object]:
    main = _main(storage)
    sync_result = main._sync_ground_truth_record_to_qingtian(project_id, record)
    sync_payload = sync_result if isinstance(sync_result, dict) else {}
    feedback_guardrail = sync_payload.get("feedback_guardrail")
    learning_quality_gate = sync_payload.get("learning_quality_gate")
    few_shot_distillation = sync_payload.get("few_shot_distillation")
    record["feedback_guardrail"] = normalize_feedback_guardrail_state(
        feedback_guardrail,
        default_score_scale_max=DEFAULT_SCORE_SCALE_MAX,
        default_threshold_ratio=float(main.DEFAULT_FEEDBACK_EXTREME_DELTA_RATIO),
    )
    record["learning_quality_gate"] = normalize_learning_quality_gate_state(
        learning_quality_gate,
        default_min_awareness_score=float(main.DEFAULT_LEARNING_MIN_AWARENESS_SCORE),
        default_min_evidence_hits=int(main.DEFAULT_LEARNING_MIN_EVIDENCE_HITS),
    )
    record["few_shot_distillation"] = normalize_few_shot_distillation_state(few_shot_distillation)
    record["feedback_closed_loop"] = main._run_feedback_closed_loop_safe(
        project_id,
        locale=locale,
        trigger=trigger,
        ground_truth_record_ids=[str(record.get("id") or "")],
    )
    return _persist_ground_truth_record_fields(
        main,
        project_id,
        str(record.get("id") or ""),
        updates={
            "feedback_guardrail": extract_feedback_guardrail(
                record,
                default_score_scale_max=DEFAULT_SCORE_SCALE_MAX,
                default_threshold_ratio=float(main.DEFAULT_FEEDBACK_EXTREME_DELTA_RATIO),
            ),
            "learning_quality_gate": extract_learning_quality_gate(
                record,
                default_min_awareness_score=float(main.DEFAULT_LEARNING_MIN_AWARENESS_SCORE),
                default_min_evidence_hits=int(main.DEFAULT_LEARNING_MIN_EVIDENCE_HITS),
            ),
            "few_shot_distillation": normalize_few_shot_distillation_state(
                record.get("few_shot_distillation")
            ),
            "feedback_closed_loop": record.get("feedback_closed_loop") or {},
        },
        updated_at=main._now_iso(),
    )


def refresh_project_ground_truth_learning_records(
    project_id: str,
    *,
    storage: StorageAccess | None = None,
) -> Dict[str, object]:
    main = _main(storage)

    def _needs_refresh(row: Dict[str, object]) -> bool:
        guardrail = normalize_feedback_guardrail_state(
            row.get("feedback_guardrail"),
            default_score_scale_max=DEFAULT_SCORE_SCALE_MAX,
            default_threshold_ratio=float(main.DEFAULT_FEEDBACK_EXTREME_DELTA_RATIO),
        )
        guardrail_review_status = str(guardrail.get("manual_review_status") or "").strip().lower()
        if bool(guardrail.get("threshold_blocked")) or guardrail_review_status in {
            "pending",
            "approved",
            "rejected",
        }:
            return True
        distillation = normalize_few_shot_distillation_state(row.get("few_shot_distillation"))
        if int(to_float_or_none(distillation.get("captured")) or 0) <= 0:
            return False
        few_shot_review_status = str(distillation.get("manual_review_status") or "").strip().lower()
        return few_shot_review_status == "pending"

    rows = [
        row
        for row in main.load_ground_truth()
        if str(row.get("project_id") or "") == project_id and _needs_refresh(row)
    ]
    refreshed = 0
    blocked_after = 0
    auto_approved_after = 0
    errors: List[Dict[str, object]] = []
    for row in rows:
        try:
            sync_result = main._sync_ground_truth_record_to_qingtian(project_id, row)
            guardrail = normalize_feedback_guardrail_state(
                (sync_result or {}).get("feedback_guardrail"),
                default_score_scale_max=DEFAULT_SCORE_SCALE_MAX,
                default_threshold_ratio=float(main.DEFAULT_FEEDBACK_EXTREME_DELTA_RATIO),
            )
            refreshed += 1
            if bool(guardrail.get("blocked")):
                blocked_after += 1
            if str(guardrail.get("manual_review_status") or "").strip().lower() == "approved":
                auto_approved_after += 1
        except Exception as exc:
            errors.append(
                {
                    "record_id": str(row.get("id") or ""),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return {
        "refreshed": refreshed,
        "blocked_after": blocked_after,
        "auto_approved_after": auto_approved_after,
        "errors": errors,
    }


def finalize_ground_truth_batch_learning_records(
    project_id: str,
    items: List[Dict[str, object]],
    *,
    locale: str,
    trigger: str,
    storage: StorageAccess | None = None,
) -> List[Dict[str, object]]:
    main = _main(storage)
    success_record_ids: List[str] = []
    for item in items:
        record = item.get("record")
        if not item.get("ok") or not isinstance(record, dict):
            continue
        try:
            sync_result = main._sync_ground_truth_record_to_qingtian(project_id, record)
            sync_payload = sync_result if isinstance(sync_result, dict) else {}
            feedback_guardrail = sync_payload.get("feedback_guardrail")
            learning_quality_gate = sync_payload.get("learning_quality_gate")
            few_shot_distillation = sync_payload.get("few_shot_distillation")
            record["feedback_guardrail"] = normalize_feedback_guardrail_state(
                feedback_guardrail,
                default_score_scale_max=DEFAULT_SCORE_SCALE_MAX,
                default_threshold_ratio=float(main.DEFAULT_FEEDBACK_EXTREME_DELTA_RATIO),
            )
            record["learning_quality_gate"] = normalize_learning_quality_gate_state(
                learning_quality_gate,
                default_min_awareness_score=float(main.DEFAULT_LEARNING_MIN_AWARENESS_SCORE),
                default_min_evidence_hits=int(main.DEFAULT_LEARNING_MIN_EVIDENCE_HITS),
            )
            record["few_shot_distillation"] = normalize_few_shot_distillation_state(
                few_shot_distillation
            )
            success_record_ids.append(str(record.get("id") or ""))
        except Exception as exc:
            item["detail"] = f"已保存，但同步青天失败：{exc}"

    closed_loop_result = main._run_feedback_closed_loop_safe(
        project_id,
        locale=locale,
        trigger=trigger,
        ground_truth_record_ids=success_record_ids,
    )
    persisted_records = main.load_ground_truth()
    changed_records = False
    for item in items:
        record = item.get("record")
        if not item.get("ok") or not isinstance(record, dict):
            continue
        record["feedback_closed_loop"] = closed_loop_result
        record_id = str(record.get("id") or "")
        for idx, stored_row in enumerate(persisted_records):
            if str(stored_row.get("project_id") or "") != str(project_id):
                continue
            if str(stored_row.get("id") or "") != record_id:
                continue
            merged = dict(stored_row)
            merged["feedback_guardrail"] = extract_feedback_guardrail(
                record,
                default_score_scale_max=DEFAULT_SCORE_SCALE_MAX,
                default_threshold_ratio=float(main.DEFAULT_FEEDBACK_EXTREME_DELTA_RATIO),
            )
            merged["learning_quality_gate"] = extract_learning_quality_gate(
                record,
                default_min_awareness_score=float(main.DEFAULT_LEARNING_MIN_AWARENESS_SCORE),
                default_min_evidence_hits=int(main.DEFAULT_LEARNING_MIN_EVIDENCE_HITS),
            )
            merged["few_shot_distillation"] = normalize_few_shot_distillation_state(
                record.get("few_shot_distillation")
            )
            merged["feedback_closed_loop"] = closed_loop_result
            merged["updated_at"] = main._now_iso()
            persisted_records[idx] = merged
            item["record"] = merged
            changed_records = True
            break
    if changed_records:
        main.save_ground_truth(persisted_records)
    return items
