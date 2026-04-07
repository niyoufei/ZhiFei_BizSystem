from __future__ import annotations

import io

import pytest

from app.trial_preflight import (
    Document,
    build_system_improvement_overview_report,
    build_trial_preflight_report,
    render_trial_preflight_docx,
    render_trial_preflight_markdown,
)


def test_build_trial_preflight_report_marks_watch_when_trial_ready_but_warnings_exist():
    report = build_trial_preflight_report(
        base_url="http://127.0.0.1:8000",
        project_id="p1",
        project_name="项目一",
        self_check={
            "ok": True,
            "summary": {"failed_required_items": []},
        },
        scoring_readiness={
            "ready": True,
            "gate_passed": True,
            "issues": [],
            "submissions": {"total": 1, "non_empty": 1, "scored": 1},
        },
        mece_audit={
            "overall": {"level": "good", "health_score": 100.0},
            "recommendations": [],
        },
        evolution_health={
            "summary": {
                "ground_truth_count": 2,
                "matched_prediction_count": 2,
                "matched_score_record_count": 2,
                "current_calibrator_version": "calib-1",
                "current_calibrator_degraded": False,
                "evolution_weights_usable": True,
            },
            "drift": {"level": "insufficient_data"},
        },
        scoring_diagnostic={
            "summary": {
                "latest_score_confidence_level": "high",
                "material_conflict_high_severity_count": 2,
            },
            "evidence_trace": {
                "material_conflicts": {
                    "conflicts": [
                        {
                            "severity": "high",
                            "dimension_id": "13",
                            "material_type": "boq",
                            "material_type_label": "清单",
                            "conflict_kind": "numeric_mismatch",
                            "label": "跨资料一致性：施组需体现清单关键约束",
                            "mandatory": True,
                            "term_hit": 0,
                            "term_need": 2,
                            "num_hit": 0,
                            "num_need": 1,
                            "source_mode": "retrieval_chunks",
                        }
                    ],
                    "recommendations": [
                        "清单一致性命中率偏低（0.0%），建议补充明确的量化约束与章节引用。"
                    ],
                }
            },
            "recommendations": ["建议逐条收口资料冲突。"],
        },
        evaluation_summary={
            "total_closure_readiness": {
                "ready": False,
                "failed_gates": ["minimum_ready_projects"],
                "next_step_title": "继续收口第二个项目",
                "next_step_detail": "当前系统仍处于总封关前置阶段。",
            }
        },
        data_hygiene={"orphan_records_total": 0},
    )

    assert report["trial_run_ready"] is True
    assert report["status"] == "watch"
    assert report["signoff"]["decision"] == "approve_with_watch"
    assert report["signoff"]["risk_level"] == "medium"
    assert report["signoff"]["verification_checklist"][0]["name"] == "系统自检"
    assert report["warning_details"]["high_severity_material_conflict_count"] == 1
    assert (
        report["warning_details"]["high_severity_material_conflicts"][0]["summary_label"]
        == "维度13 / 清单 / 数值不一致 / 跨资料一致性：施组需体现清单关键约束"
    )
    assert (
        report["warning_details"]["high_severity_material_conflicts"][0]["entrypoint_label"]
        == "前往「4) 项目施组」上传新版施组"
    )
    assert (
        report["warning_details"]["high_severity_material_conflicts"][0]["entrypoint_reason_label"]
        == "当前项目已有已评分施组；若已按冲突项修改内容，应先上传新版施组，再重新评分。"
    )
    assert (
        report["warning_details"]["high_severity_material_conflicts"][0][
            "material_review_entrypoint_label"
        ]
        == "前往「3) 项目资料」核对清单"
    )
    assert (
        report["warning_details"]["high_severity_material_conflicts"][0][
            "material_review_entrypoint_anchor"
        ]
        == "#uploadMaterialBoq"
    )
    assert (
        report["warning_details"]["high_severity_material_conflicts"][0][
            "material_review_reason_label"
        ]
        == "当前该高严重度冲突来自清单数值不一致，建议先核对对应资料来源文件和量化约束。"
    )
    assert (
        "量化约束"
        in report["warning_details"]["high_severity_material_conflicts"][0]["action_label"]
    )
    assert report["record_draft"]["status"] == "pending_manual_confirmation"
    assert report["record_draft"]["status_label"] == "待人工确认"
    assert report["record_draft"]["recommended_conclusion"] == "建议试车（带警告）"
    assert report["record_draft"]["warning_ack_required"] is True
    assert "系统总封关前置条件尚未全部满足" in " ".join(report["warnings"])
    assert "最新施组仍存在 2 个高严重度资料冲突" in " ".join(report["warnings"])
    assert "继续收口第二个项目：当前系统仍处于总封关前置阶段。" in report["recommendations"]


def test_build_trial_preflight_report_marks_blocked_when_runtime_or_project_gate_fails():
    report = build_trial_preflight_report(
        base_url="http://127.0.0.1:8000",
        project_id="p2",
        project_name="项目二",
        self_check={
            "ok": False,
            "summary": {"failed_required_items": ["health", "config"]},
        },
        scoring_readiness={
            "ready": False,
            "gate_passed": False,
            "issues": ["缺少必需资料类型：清单"],
            "submissions": {"total": 0, "non_empty": 0, "scored": 0},
        },
        mece_audit={
            "overall": {"level": "critical", "health_score": 25.0},
            "recommendations": ["先补齐清单和图纸。"],
        },
        evolution_health={
            "summary": {
                "ground_truth_count": 1,
                "matched_prediction_count": 0,
                "matched_score_record_count": 0,
                "current_calibrator_version": "",
                "current_calibrator_degraded": False,
                "evolution_weights_usable": False,
            },
            "drift": {"level": "insufficient_data"},
        },
        scoring_diagnostic={
            "summary": {"material_conflict_high_severity_count": 1},
            "evidence_trace": {
                "material_conflicts": {
                    "conflicts": [
                        {
                            "severity": "high",
                            "dimension_id": "02",
                            "material_type": "site_photo",
                            "material_type_label": "现场照片",
                            "conflict_kind": "term_coverage_missing",
                            "label": "跨资料一致性：施组需体现现场照片关键约束",
                            "mandatory": True,
                            "term_hit": 0,
                            "term_need": 1,
                            "num_hit": 1,
                            "num_need": 1,
                            "source_mode": "retrieval_chunks",
                        }
                    ],
                    "recommendations": ["施组与上传资料存在一致性缺口，建议按冲突项逐条补齐。"],
                }
            },
            "recommendations": [],
        },
        evaluation_summary={"total_closure_readiness": {"ready": False, "failed_gates": []}},
        data_hygiene={"orphan_records_total": 2},
    )

    assert report["trial_run_ready"] is False
    assert report["status"] == "blocked"
    assert report["signoff"]["decision"] == "hold"
    assert report["signoff"]["risk_level"] == "high"
    assert report["record_draft"]["recommended_conclusion"] == "暂缓试车"
    joined = " ".join(report["blockers"])
    assert "系统自检未通过" in joined
    assert "当前项目尚未满足评分前置" in joined
    assert "当前项目 MECE 诊断为 critical" in joined
    assert "真实评标样本尚未形成有效评分记录匹配" in joined
    assert (
        report["warning_details"]["high_severity_material_conflicts"][0]["entrypoint_label"]
        == "前往「4) 项目施组」上传施组"
    )
    assert (
        report["warning_details"]["high_severity_material_conflicts"][0]["entrypoint_reason_label"]
        == "当前项目还没有可用施组，需先上传施组后才能进入评分。"
    )
    assert (
        report["warning_details"]["high_severity_material_conflicts"][0][
            "material_review_reason_label"
        ]
        == "当前该高严重度冲突来自现场照片术语覆盖缺失，建议先核对对应资料来源文件和关键术语表述。"
    )


def test_render_trial_preflight_markdown_contains_status_and_sections():
    report = build_trial_preflight_report(
        base_url="http://127.0.0.1:8000",
        project_id="p3",
        project_name="项目三",
        self_check={"ok": True, "summary": {}},
        scoring_readiness={
            "ready": True,
            "gate_passed": True,
            "issues": [],
            "submissions": {"total": 1, "non_empty": 1, "scored": 0},
        },
        mece_audit={"overall": {"level": "good", "health_score": 100.0}, "recommendations": []},
        evolution_health={
            "summary": {
                "ground_truth_count": 0,
                "matched_prediction_count": 0,
                "matched_score_record_count": 0,
                "current_calibrator_version": "",
                "current_calibrator_degraded": False,
                "evolution_weights_usable": False,
            },
            "drift": {"level": "insufficient_data"},
        },
        scoring_diagnostic={
            "summary": {"material_conflict_high_severity_count": 1},
            "evidence_trace": {
                "material_conflicts": {
                    "conflicts": [
                        {
                            "severity": "high",
                            "dimension_id": "02",
                            "material_type": "site_photo",
                            "material_type_label": "现场照片",
                            "conflict_kind": "term_coverage_missing",
                            "label": "跨资料一致性：施组需体现现场照片关键约束",
                            "mandatory": True,
                            "term_hit": 0,
                            "term_need": 1,
                            "num_hit": 1,
                            "num_need": 1,
                            "source_mode": "retrieval_chunks",
                        }
                    ],
                    "recommendations": ["施组与上传资料存在一致性缺口，建议按冲突项逐条补齐。"],
                }
            },
            "recommendations": [],
        },
        evaluation_summary={"total_closure_readiness": {"ready": False, "failed_gates": []}},
        data_hygiene={"orphan_records_total": 0},
    )

    markdown = render_trial_preflight_markdown(report)

    assert "# 试车前综合体检" in markdown
    assert "## 签发摘要" in markdown
    assert "## 核验清单" in markdown
    assert "## 试车记录草案（待确认）" in markdown
    assert "### 需确认警告项" in markdown
    assert "## 重点警告明细" in markdown
    assert "维度02 / 现场照片 / 术语覆盖缺失 / 跨资料一致性：施组需体现现场照片关键约束" in markdown
    assert "推荐入口：`前往「4) 项目施组」评分施组`" in markdown
    assert (
        "推荐入口依据：`当前项目已有待评分施组，且评分前置与资料门禁已满足，可直接评分施组。`"
        in markdown
    )
    assert "资料核对入口：`前往「3) 项目资料」核对现场照片`" in markdown
    assert (
        "资料核对依据：`当前该高严重度冲突来自现场照片术语覆盖缺失，建议先核对对应资料来源文件和关键术语表述。`"
        in markdown
    )
    assert "建议动作：`优先回到施组，补齐与现场照片一致的术语、章节标题或措施表述。`" in markdown
    assert "## 核心指标" in markdown
    assert "## 阻断项" in markdown
    assert "## 警告项" in markdown
    assert "试车结论" in markdown


def test_build_system_improvement_overview_report_highlights_global_gaps():
    report = build_system_improvement_overview_report(
        base_url="http://127.0.0.1:8000",
        self_check={
            "ok": True,
            "failed_required_count": 0,
            "failed_optional_count": 1,
            "summary": {},
        },
        data_hygiene={
            "orphan_records_total": 0,
            "datasets": [
                {"name": "score_reports", "orphan_count": 0},
                {"name": "ground_truth", "orphan_count": 0},
            ],
        },
        evaluation_summary={
            "project_count": 3,
            "acceptance_pass_count": {
                "current_display_matches_qt": 1,
                "current_mae_rmse_not_worse_than_v2": 1,
                "current_rank_corr_not_worse_vs_v2": 2,
            },
            "total_closure_readiness": {
                "ready": False,
                "status_label": "暂不可封系统总关",
                "minimum_ready_projects": 2,
                "evaluated_project_count": 2,
                "ready_project_count": 1,
                "not_ready_project_count": 1,
                "candidate_project_count": 1,
                "failed_gates": [
                    "minimum_ready_projects",
                    "all_evaluated_projects_current_display_match_qt",
                ],
                "blocker_kind": "close_not_ready_project",
                "next_priority_project_id": "p-a",
                "next_priority_project_name": "项目A",
                "next_candidate_project_id": "p-b",
                "next_candidate_project_name": "项目B",
                "next_step_title": "优先收口项目“项目A”",
                "next_step_detail": "先处理当前分与青天未完全对齐的问题。",
                "next_step_entrypoint_key": "evaluation_summary",
                "next_step_entrypoint_label": "前往「5) 自我学习与进化」执行“跨项目汇总评估”",
                "next_step_action_label": "查看跨项目汇总",
                "evaluated_project_summaries": [
                    {
                        "project_id": "p-a",
                        "project_name": "项目A",
                        "ready": False,
                        "failed_gates": ["current_display_matches_qt", "drift_low"],
                        "failed_gate_details": [
                            {
                                "id": "current_display_matches_qt",
                                "label": "当前分已与青天结果对齐",
                                "detail": "false",
                                "entrypoint_key": "auto_run_reflection",
                                "entrypoint_label": "前往「5) 自我学习与进化」执行“一键闭环执行”",
                                "entrypoint_detail": "先重跑 V2 一键闭环，收口当前分、校准器和漂移状态。",
                                "action_label": "执行一键闭环",
                            },
                            {
                                "id": "drift_low",
                                "label": "近30天漂移等级为 low",
                                "detail": "watch",
                                "entrypoint_key": "auto_run_reflection",
                                "entrypoint_label": "前往「5) 自我学习与进化」执行“一键闭环执行”",
                                "entrypoint_detail": "先重跑 V2 一键闭环，收口当前分、校准器和漂移状态。",
                                "action_label": "执行一键闭环",
                            },
                            {
                                "id": "minimum_ground_truth_samples",
                                "label": "当前分样本数达到 3 条",
                                "detail": "2/3",
                                "entrypoint_key": "ground_truth",
                                "entrypoint_label": "前往「5) 自我学习与进化」录入真实评标",
                                "entrypoint_detail": "先补齐真实评标样本，再继续推进封关。",
                                "action_label": "录入真实评标",
                            },
                            {
                                "id": "current_mae_rmse_not_worse_than_v2",
                                "label": "当前 MAE/RMSE 不劣于 V2",
                                "detail": "false",
                                "entrypoint_key": "evaluation_summary",
                                "entrypoint_label": "前往「5) 自我学习与进化」执行“跨项目汇总评估”",
                                "entrypoint_detail": "先查看跨项目汇总，再决定是否继续闭环或治理。",
                                "action_label": "查看跨项目汇总",
                            },
                        ],
                        "failed_gate_count": 4,
                        "recommendation": "当前项目尚未满足第一阶段封关条件，优先收口未通过门。",
                        "current_display_matches_qt": False,
                        "current_mae_rmse_not_worse_than_v2": False,
                        "current_rank_corr_not_worse_than_v2": True,
                        "entrypoint_key": "auto_run_reflection",
                        "entrypoint_label": "前往「5) 自我学习与进化」执行“一键闭环执行”",
                        "entrypoint_detail": "先重跑 V2 一键闭环，收口当前分、校准器和漂移状态。",
                        "action_label": "执行一键闭环",
                    },
                    {
                        "project_id": "p-c",
                        "project_name": "项目C",
                        "ready": True,
                        "failed_gates": [],
                        "failed_gate_count": 0,
                        "recommendation": "当前项目已满足第一阶段封关条件，可进入封关核查。",
                        "current_display_matches_qt": True,
                        "current_mae_rmse_not_worse_than_v2": True,
                        "current_rank_corr_not_worse_than_v2": True,
                        "entrypoint_key": "project_evaluation",
                        "entrypoint_label": "前往「5) 自我学习与进化」执行“项目指标评估”",
                        "entrypoint_detail": "先查看该项目未通过门，再针对性收口。",
                        "action_label": "查看项目评估",
                    },
                ],
                "not_ready_project_summaries": [
                    {
                        "project_id": "p-a",
                        "project_name": "项目A",
                        "ready": False,
                        "failed_gates": ["current_display_matches_qt", "drift_low"],
                        "failed_gate_details": [
                            {
                                "id": "current_display_matches_qt",
                                "label": "当前分已与青天结果对齐",
                                "detail": "false",
                                "entrypoint_key": "auto_run_reflection",
                                "entrypoint_label": "前往「5) 自我学习与进化」执行“一键闭环执行”",
                                "entrypoint_detail": "先重跑 V2 一键闭环，收口当前分、校准器和漂移状态。",
                                "action_label": "执行一键闭环",
                            },
                            {
                                "id": "drift_low",
                                "label": "近30天漂移等级为 low",
                                "detail": "watch",
                                "entrypoint_key": "auto_run_reflection",
                                "entrypoint_label": "前往「5) 自我学习与进化」执行“一键闭环执行”",
                                "entrypoint_detail": "先重跑 V2 一键闭环，收口当前分、校准器和漂移状态。",
                                "action_label": "执行一键闭环",
                            },
                            {
                                "id": "minimum_ground_truth_samples",
                                "label": "当前分样本数达到 3 条",
                                "detail": "2/3",
                                "entrypoint_key": "ground_truth",
                                "entrypoint_label": "前往「5) 自我学习与进化」录入真实评标",
                                "entrypoint_detail": "先补齐真实评标样本，再继续推进封关。",
                                "action_label": "录入真实评标",
                            },
                            {
                                "id": "current_mae_rmse_not_worse_than_v2",
                                "label": "当前 MAE/RMSE 不劣于 V2",
                                "detail": "false",
                                "entrypoint_key": "evaluation_summary",
                                "entrypoint_label": "前往「5) 自我学习与进化」执行“跨项目汇总评估”",
                                "entrypoint_detail": "先查看跨项目汇总，再决定是否继续闭环或治理。",
                                "action_label": "查看跨项目汇总",
                            },
                        ],
                        "failed_gate_count": 4,
                        "recommendation": "当前项目尚未满足第一阶段封关条件，优先收口未通过门。",
                        "current_display_matches_qt": False,
                        "current_mae_rmse_not_worse_than_v2": False,
                        "current_rank_corr_not_worse_than_v2": True,
                        "entrypoint_key": "auto_run_reflection",
                        "entrypoint_label": "前往「5) 自我学习与进化」执行“一键闭环执行”",
                        "entrypoint_detail": "先重跑 V2 一键闭环，收口当前分、校准器和漂移状态。",
                        "action_label": "执行一键闭环",
                    }
                ],
            },
        },
        ops_agents_status={
            "snapshot_path": "build/ops_agents_status.json",
            "generated_at": "2026-03-31T10:00:00+08:00",
            "agent_count": 9,
            "settings": {"auto_repair": True, "auto_evolve": True},
            "overall": {
                "status": "warn",
                "pass_count": 8,
                "warn_count": 1,
                "fail_count": 0,
                "duration_ms": 6122,
            },
            "agents": {
                "runtime_repair": {
                    "status": "pass",
                    "metrics": {"auto_fixed_count": 0},
                    "actions": {
                        "repair_data_hygiene": {"attempted": False, "ok": False},
                        "restart_runtime": {"attempted": False, "ok": False},
                    },
                    "recommendations": ["运行态巡检正常，未发现需要自动修复的问题。"],
                },
                "data_hygiene": {
                    "status": "pass",
                    "actions": {"repair": {"attempted": False, "ok": False}},
                    "recommendations": [],
                },
                "evolution": {
                    "status": "pass",
                    "metrics": {"pending_evolve_after": 0},
                    "recommendations": [],
                },
                "learning_calibration": {
                    "status": "warn",
                    "manual_confirmation_rows": [
                        {
                            "project_id": "p-manual",
                            "project_name": "项目手工确认A",
                            "pending_extreme_ground_truth_count": 4,
                            "matched_submission_count": 0,
                            "entrypoint_key": "ground_truth",
                            "entrypoint_label": "前往「5) 自我学习与进化」录入真实评标",
                            "action_label": "录入真实评标并人工确认极端样本",
                            "manual_override_hint": "confirm_extreme_sample=1",
                            "current_calibrator_deployment_mode": "prior_fallback",
                            "detail": "待人工确认极端样本 4 条；当前暂无可关联预测样本",
                            "recommendation": "存在 4 条极端偏差样本，自动调权/自动校准已被暂停；人工确认后再执行学习进化或一键闭环。",
                        }
                    ],
                    "metrics": {
                        "evolve_attempted_count": 0,
                        "evolve_success_count": 0,
                        "reflection_attempted_count": 0,
                        "reflection_success_count": 0,
                        "manual_confirmation_required_count": 1,
                        "post_verify_failed_count": 0,
                        "bootstrap_monitoring_count": 1,
                        "llm_account_low_quality_pool_count": 2,
                    },
                    "recommendations": [
                        "有 1 个项目存在极端偏差样本，需人工确认后才能继续自动学习。"
                    ],
                },
            },
            "recommendations": ["有 1 个项目存在极端偏差样本，需人工确认后才能继续自动学习。"],
        },
        ops_agents_history=[
            {
                "generated_at": "2026-03-30T10:00:00+08:00",
                "overall_status": "pass",
                "pass_count": 9,
                "warn_count": 0,
                "fail_count": 0,
                "auto_repair_attempted_count": 1,
                "auto_repair_success_count": 1,
                "auto_evolve_attempted_count": 1,
                "auto_evolve_success_count": 1,
                "manual_confirmation_required_count": 0,
                "post_verify_failed_count": 0,
                "quality_reason_code": "auto_actions_executed",
                "quality_reason_label": "已执行自动动作",
                "quality_reason_detail": "自动修复 1/1；自动学习 1/1",
                "quality_audit_label": "已执行自动动作",
                "top_recommendation": "",
            },
            {
                "generated_at": "2026-03-31T10:00:00+08:00",
                "overall_status": "warn",
                "pass_count": 8,
                "warn_count": 1,
                "fail_count": 0,
                "auto_repair_attempted_count": 0,
                "auto_repair_success_count": 0,
                "auto_evolve_attempted_count": 0,
                "auto_evolve_success_count": 0,
                "manual_confirmation_required_count": 1,
                "post_verify_failed_count": 0,
                "quality_reason_code": "manual_confirmation_required",
                "quality_reason_label": "自动学习需人工确认",
                "quality_reason_detail": "人工确认需求 1 项",
                "quality_reason_project_id": "p-manual",
                "quality_reason_project_name": "项目手工确认A",
                "quality_reason_project_detail": "待人工确认极端样本 4 条；当前暂无可关联预测样本",
                "quality_audit_label": "自动学习需人工确认",
                "top_recommendation": "有 1 个项目存在极端偏差样本，需人工确认后才能继续自动学习。",
            },
        ],
    )

    assert report["overall_ready"] is False
    assert report["status"] == "watch"
    assert report["metrics"]["project_count"] == 3
    assert report["metrics"]["ready_project_count"] == 1
    assert report["metrics"]["current_display_matches_qt_pass_count"] == 1
    assert report["focus_workstreams"][0]["title"] == "运行稳定性"
    assert report["focus_workstream_status_summaries"][0]["workstream_status"] == "ok"
    assert report["focus_workstream_status_summaries"][0]["count"] == 1
    assert (
        report["focus_workstream_status_summaries"][0]["priority_workstream_id"] == "data_hygiene"
    )
    assert report["focus_workstream_status_summaries"][1]["workstream_status"] == "warn"
    assert report["focus_workstream_status_summaries"][1]["count"] == 4
    assert (
        report["focus_workstream_status_summaries"][1]["priority_workstream_id"]
        == "priority_project"
    )
    assert (
        report["focus_workstream_status_summaries"][1]["priority_entrypoint_key"]
        == "evaluation_summary"
    )
    assert report["focus_workstream_status_summaries"][2]["workstream_status"] == "blocked"
    assert report["focus_workstream_status_summaries"][2]["status"] == "empty"
    assert report["ops_agent_quality_summary"]["overall_status"] == "warn"
    assert report["ops_agent_quality_summary"]["quality_status"] == "watch"
    assert report["ops_agent_quality_summary"]["recent_cycle_count"] == 2
    assert report["ops_agent_quality_summary"]["recent_pass_cycle_count"] == 1
    assert report["ops_agent_quality_summary"]["recent_warn_cycle_count"] == 1
    assert report["ops_agent_quality_summary"]["repair_success_rate"] == 1.0
    assert report["ops_agent_quality_summary"]["evolve_success_rate"] == 1.0
    assert report["ops_agent_quality_summary"]["recent_non_pass_streak_count"] == 1
    assert report["ops_agent_quality_summary"]["recent_manual_gate_cycle_count"] == 1
    assert (
        report["ops_agent_quality_summary"]["recent_audit_rows"][-1]["quality_audit_label"]
        == "自动学习需人工确认"
    )
    assert (
        report["ops_agent_quality_summary"]["latest_quality_reason_label"] == "自动学习需人工确认"
    )
    assert report["ops_agent_quality_summary"]["latest_quality_reason_project_id"] == "p-manual"
    assert (
        report["ops_agent_quality_summary"]["latest_quality_reason_project_name"] == "项目手工确认A"
    )
    assert (
        report["ops_agent_quality_summary"]["latest_quality_reason_project_detail"]
        == "待人工确认极端样本 4 条；当前暂无可关联预测样本"
    )
    assert report["ops_agent_quality_summary"]["recent_same_reason_streak_count"] == 1
    assert (
        report["ops_agent_quality_summary"]["recent_quality_reason_summary_rows"][0][
            "quality_reason_code"
        ]
        == "auto_actions_executed"
    )
    assert report["ops_agent_quality_summary"]["auto_repair_enabled"] is True
    assert report["ops_agent_quality_summary"]["auto_evolve_enabled"] is True
    assert report["ops_agent_quality_summary"]["manual_confirmation_required_count"] == 1
    assert (
        report["ops_agent_quality_summary"]["manual_confirmation_rows"][0]["project_id"]
        == "p-manual"
    )
    assert (
        report["ops_agent_quality_summary"]["manual_confirmation_rows"][0]["entrypoint_key"]
        == "ground_truth"
    )
    assert (
        report["ops_agent_quality_summary"]["manual_confirmation_rows"][0]["manual_override_hint"]
        == "confirm_extreme_sample=1"
    )
    assert report["ops_agent_quality_summary"]["llm_account_low_quality_pool_count"] == 2
    assert report["ops_agent_quality_summary"]["agent_rows"][0]["name"] == "learning_calibration"
    assert any(item["id"] == "system_closure" for item in report["focus_workstreams"])
    assert report["closure_gate_details"][0]["id"] == "minimum_ready_projects"
    assert report["closure_gate_details"][0]["label"] == "达到第一阶段 ready 的项目数达到 2 个"
    assert (
        report["closure_gate_details"][0]["entrypoint_label"]
        == "前往「5) 自我学习与进化」执行“跨项目汇总评估”"
    )
    assert (
        report["closure_gate_details"][1]["id"] == "all_evaluated_projects_current_display_match_qt"
    )
    assert report["project_gap_details"][0]["kind"] == "phase1_ready_gap"
    assert report["project_gap_details"][0]["project_id"] == "p-a"
    assert report["project_gap_details"][0]["entrypoint_key"] == "auto_run_reflection"
    assert report["project_gap_details"][1]["kind"] == "current_display_matches_qt_gap"
    assert "当前展示分尚未完全对齐青天结果" in report["project_gap_details"][1]["detail"]
    assert report["project_gate_gap_details"][0]["kind"] == "project_failed_gate"
    assert report["project_gate_gap_details"][0]["gate_id"] == "current_display_matches_qt"
    assert report["project_gate_gap_details"][0]["entrypoint_key"] == "auto_run_reflection"
    assert report["project_gate_gap_details"][1]["gate_id"] == "drift_low"
    assert report["project_action_gap_details"][0]["kind"] == "project_action_gap"
    assert report["project_action_gap_details"][0]["entrypoint_key"] == "auto_run_reflection"
    assert report["project_action_gap_details"][0]["gate_count"] == 2
    assert (
        "关联未通过门：当前分已与青天结果对齐、近30天漂移等级为 low"
        in report["project_action_gap_details"][0]["detail"]
    )
    assert report["global_action_gap_details"][0]["kind"] == "global_action_gap"
    assert report["global_action_gap_details"][0]["entrypoint_key"] == "auto_run_reflection"
    assert report["global_action_gap_details"][0]["project_count"] == 1
    assert report["global_action_gap_details"][0]["gate_count_total"] == 2
    assert report["global_action_gap_details"][0]["execution_mode"] == "auto"
    assert report["global_action_gap_details"][0]["execution_mode_label"] == "自动闭环优先"
    assert (
        report["global_action_gap_details"][0]["priority_reason_label"]
        == "该项目是当前系统总封关优先收口项目。"
    )
    assert report["global_action_gap_details"][0]["priority_sort_label"] == "系统总封关优先项目。"
    assert report["global_action_gap_details"][0]["action_group"] == "auto"
    assert report["global_action_gap_details"][0]["action_group_label"] == "可自动收口动作"
    assert (
        report["global_action_gap_details"][0]["group_reason_label"]
        == "该动作支持直接执行自动闭环收口。"
    )
    assert report["global_action_group_summaries"][0]["action_group"] == "auto"
    assert report["global_action_group_summaries"][0]["status"] == "active"
    assert report["global_action_group_summaries"][0]["count"] == 1
    assert "可自动收口动作" == report["global_action_group_summaries"][0]["action_group_label"]
    assert "建议优先从“项目A”开始。" in report["global_action_gap_details"][0]["detail"]
    assert report["global_auto_action_gap_details"][0]["entrypoint_key"] == "auto_run_reflection"
    assert report["global_auto_action_gap_details"][0]["action_group"] == "auto"
    assert report["global_readonly_action_gap_details"][0]["entrypoint_key"] == "evaluation_summary"
    assert report["global_readonly_action_gap_details"][0]["action_group"] == "readonly"
    assert report["global_readonly_action_gap_details"][0]["action_group_label"] == "只读诊断动作"
    assert report["global_manual_action_gap_details"][0]["entrypoint_key"] == "ground_truth"
    assert report["global_manual_action_gap_details"][0]["action_group"] == "manual"
    assert report["global_manual_action_gap_details"][0]["action_group_label"] == "必须人工处理动作"
    priority_row = next(
        item for item in report["focus_workstreams"] if item["id"] == "priority_project"
    )
    assert priority_row["project_id"] == "p-a"
    assert priority_row["project_name"] == "项目A"
    closure_row = next(
        item for item in report["focus_workstreams"] if item["id"] == "system_closure"
    )
    assert closure_row["project_id"] == "p-a"
    assert any("系统总封关前置条件尚未全部满足" in item for item in report["warnings"])
    assert any("优先收口项目“项目A”" in item for item in report["recommendations"])


def test_build_system_improvement_overview_report_derives_readonly_actions_from_focus_workstreams():
    report = build_system_improvement_overview_report(
        base_url="http://127.0.0.1:8000",
        self_check={
            "ok": True,
            "failed_required_count": 0,
            "failed_optional_count": 0,
            "summary": {},
        },
        data_hygiene={
            "orphan_records_total": 0,
            "datasets": [],
        },
        evaluation_summary={
            "project_count": 1,
            "acceptance_pass_count": {
                "current_display_matches_qt": 0,
                "current_mae_rmse_not_worse_than_v2": 0,
                "current_rank_corr_not_worse_vs_v2": 1,
            },
            "total_closure_readiness": {
                "ready": False,
                "status_label": "暂不可封系统总关",
                "minimum_ready_projects": 1,
                "evaluated_project_count": 1,
                "ready_project_count": 0,
                "not_ready_project_count": 1,
                "candidate_project_count": 0,
                "failed_gates": ["all_evaluated_projects_current_display_match_qt"],
                "blocker_kind": "close_not_ready_project",
                "next_priority_project_id": "p-a",
                "next_priority_project_name": "项目A",
                "next_step_title": "优先收口项目“项目A”",
                "next_step_detail": "先查看跨项目汇总，再决定是否继续闭环或治理。",
                "next_step_entrypoint_key": "evaluation_summary",
                "next_step_entrypoint_label": "前往「5) 自我学习与进化」执行“跨项目汇总评估”",
                "next_step_action_label": "查看跨项目汇总",
                "evaluated_project_summaries": [
                    {
                        "project_id": "p-a",
                        "project_name": "项目A",
                        "ready": False,
                        "failed_gates": [],
                        "failed_gate_count": 0,
                        "recommendation": "先查看跨项目汇总，再决定是否继续闭环或治理。",
                        "current_display_matches_qt": False,
                        "current_mae_rmse_not_worse_than_v2": False,
                        "current_rank_corr_not_worse_than_v2": True,
                        "entrypoint_key": "ground_truth",
                        "entrypoint_label": "前往「5) 自我学习与进化」录入真实评标",
                        "entrypoint_detail": "先补齐真实评标样本，再继续推进封关。",
                        "action_label": "录入真实评标",
                    }
                ],
                "not_ready_project_summaries": [
                    {
                        "project_id": "p-a",
                        "project_name": "项目A",
                        "ready": False,
                        "failed_gates": [],
                        "failed_gate_count": 0,
                        "recommendation": "先查看跨项目汇总，再决定是否继续闭环或治理。",
                        "current_display_matches_qt": False,
                        "current_mae_rmse_not_worse_than_v2": False,
                        "current_rank_corr_not_worse_than_v2": True,
                        "entrypoint_key": "ground_truth",
                        "entrypoint_label": "前往「5) 自我学习与进化」录入真实评标",
                        "entrypoint_detail": "先补齐真实评标样本，再继续推进封关。",
                        "action_label": "录入真实评标",
                    }
                ],
            },
        },
        ops_agents_status={
            "snapshot_path": "build/ops_agents_status.json",
            "generated_at": "2026-03-31T10:00:00+08:00",
            "agent_count": 9,
            "settings": {"auto_repair": True, "auto_evolve": True},
            "overall": {"status": "pass", "pass_count": 9, "warn_count": 0, "fail_count": 0},
            "agents": {},
        },
        ops_agents_history=[
            {
                "generated_at": "2026-03-31T10:00:00+08:00",
                "overall_status": "pass",
                "pass_count": 9,
                "warn_count": 0,
                "fail_count": 0,
                "auto_repair_attempted_count": 0,
                "auto_repair_success_count": 0,
                "auto_evolve_attempted_count": 0,
                "auto_evolve_success_count": 0,
                "manual_confirmation_required_count": 0,
                "post_verify_failed_count": 0,
                "quality_reason_code": "stable_pass",
                "quality_reason_label": "巡检稳定通过",
                "quality_reason_detail": "",
                "quality_audit_label": "巡检稳定通过",
                "top_recommendation": "",
            }
        ],
    )

    readonly_rows = report["global_readonly_action_gap_details"]
    assert len(readonly_rows) == 1
    assert readonly_rows[0]["entrypoint_key"] == "evaluation_summary"
    assert readonly_rows[0]["execution_mode"] == "readonly"
    assert readonly_rows[0]["action_group"] == "readonly"
    assert readonly_rows[0]["project_id"] == "p-a"
    assert (
        readonly_rows[0]["priority_reason_label"]
        == "该动作用于继续诊断当前系统级缺口，建议先从当前优先收口项目开始。"
    )
    assert readonly_rows[0]["priority_sort_label"] == "系统总封关优先项目。"
    assert report["ops_agent_quality_summary"]["quality_status"] == "ready"
    assert report["ops_agent_quality_summary"]["recent_cycle_count"] == 1
    assert report["ops_agent_quality_summary"]["recent_non_pass_streak_count"] == 0
    assert report["ops_agent_quality_summary"]["latest_quality_reason_label"] == "巡检稳定通过"
    assert readonly_rows[0]["group_reason_label"] == "该动作仅用于只读诊断，不直接改写项目状态。"
    group_rows = report["global_action_group_summaries"]
    assert group_rows[0]["action_group"] == "auto"
    assert group_rows[0]["status"] == "empty"
    assert (
        group_rows[0]["empty_reason_label"]
        == "当前未命中可自动收口动作，说明现有系统级缺口主要仍依赖人工处理或只读诊断。"
    )
    assert report["focus_workstream_status_summaries"][0]["workstream_status"] == "ok"
    assert report["focus_workstream_status_summaries"][0]["count"] == 2
    assert (
        report["focus_workstream_status_summaries"][0]["priority_workstream_id"]
        == "runtime_stability"
    )
    assert report["focus_workstream_status_summaries"][1]["workstream_status"] == "warn"
    assert report["focus_workstream_status_summaries"][1]["count"] == 3
    assert (
        report["focus_workstream_status_summaries"][1]["priority_workstream_id"]
        == "priority_project"
    )
    assert (
        report["focus_workstream_status_summaries"][1]["priority_entrypoint_key"]
        == "evaluation_summary"
    )
    assert report["focus_workstream_status_summaries"][2]["workstream_status"] == "blocked"
    assert report["focus_workstream_status_summaries"][2]["status"] == "empty"
    assert group_rows[1]["action_group"] == "readonly"
    assert group_rows[1]["status"] == "active"
    assert group_rows[1]["count"] == 1


@pytest.mark.skipif(Document is None, reason="python-docx 不可用")
def test_render_trial_preflight_docx_contains_core_sections():
    report = build_trial_preflight_report(
        base_url="http://127.0.0.1:8000",
        project_id="p4",
        project_name="项目四",
        self_check={"ok": True, "summary": {}},
        scoring_readiness={
            "ready": True,
            "gate_passed": True,
            "issues": [],
            "submissions": {"total": 1, "non_empty": 1, "scored": 0},
        },
        mece_audit={"overall": {"level": "good", "health_score": 100.0}, "recommendations": []},
        evolution_health={
            "summary": {
                "ground_truth_count": 2,
                "matched_prediction_count": 2,
                "matched_score_record_count": 2,
                "current_calibrator_version": "calib-1",
                "current_calibrator_degraded": False,
                "evolution_weights_usable": True,
            },
            "drift": {"level": "low"},
        },
        scoring_diagnostic={
            "summary": {
                "latest_score_confidence_level": "high",
                "material_conflict_high_severity_count": 0,
            },
            "evidence_trace": {
                "material_conflicts": {
                    "conflicts": [
                        {
                            "severity": "high",
                            "dimension_id": "14",
                            "material_type": "drawing",
                            "material_type_label": "图纸",
                            "conflict_kind": "numeric_mismatch",
                            "label": "跨资料一致性：施组需体现图纸关键约束",
                            "mandatory": True,
                            "term_hit": 1,
                            "term_need": 1,
                            "num_hit": 0,
                            "num_need": 1,
                            "source_mode": "retrieval_chunks",
                        }
                    ],
                    "recommendations": ["施组与上传资料存在一致性缺口，建议按冲突项逐条补齐。"],
                }
            },
            "recommendations": ["继续保持。"],
        },
        evaluation_summary={"total_closure_readiness": {"ready": True, "failed_gates": []}},
        data_hygiene={"orphan_records_total": 0},
    )

    docx_bytes = render_trial_preflight_docx(report)
    assert docx_bytes.startswith(b"PK")

    doc = Document(io.BytesIO(docx_bytes))
    text = "\n".join(p.text for p in doc.paragraphs if p.text)
    assert "试车前综合体检" in text
    assert "签发摘要" in text
    assert "核验清单" in text
    assert "试车记录草案（待确认）" in text
    assert "确认提示：试车完成后请补记执行人与结论确认。" in text
    assert "重点警告明细" in text
    assert "高严重度资料冲突清单" in text
    assert "维度14 / 图纸 / 数值不一致 / 跨资料一致性：施组需体现图纸关键约束" in text
    assert "推荐入口：前往「4) 项目施组」评分施组" in text
    assert (
        "推荐入口依据：当前项目已有待评分施组，且评分前置与资料门禁已满足，可直接评分施组。" in text
    )
    assert "资料核对入口：前往「3) 项目资料」核对图纸" in text
    assert (
        "资料核对依据：当前该高严重度冲突来自图纸数值不一致，建议先核对对应资料来源文件和量化约束。"
        in text
    )
    assert "建议动作：优先回到施组，补齐与图纸一致的量化约束、工程量或参数。" in text
    assert "核心指标" in text
    assert "优势项" in text
    assert "建议动作" in text
