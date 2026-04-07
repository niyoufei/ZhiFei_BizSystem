#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def to_markdown(payload: Dict[str, Any]) -> str:
    overall = payload.get("overall") or {}
    settings = payload.get("settings") or {}
    runtime = payload.get("runtime") or {}
    agent_names = payload.get("expected_agent_names") or []
    lines = [
        "# Ops Agents Status",
        "",
        f"- generated_at: `{payload.get('generated_at', '-')}`",
        f"- base_url: `{payload.get('base_url', '-')}`",
        f"- agent_count: `{payload.get('agent_count', '-')}`",
        f"- overall_status: `{overall.get('status', '-')}`",
        f"- duration_ms: `{overall.get('duration_ms', '-')}`",
        "",
        "## Settings",
        f"- auto_repair: `{settings.get('auto_repair')}`",
        f"- auto_evolve: `{settings.get('auto_evolve')}`",
        f"- min_evolve_samples: `{settings.get('min_evolve_samples')}`",
        f"- timeout_seconds: `{settings.get('timeout_seconds')}`",
        "",
        "## Runtime",
        f"- cycle: `{runtime.get('cycle')}`",
        f"- interval_seconds: `{runtime.get('interval_seconds')}`",
        f"- launcher: `{runtime.get('launcher')}`",
        f"- pid: `{runtime.get('pid')}`",
        "",
        "## Agents",
    ]

    agents = payload.get("agents") or {}
    for name in agent_names:
        row = agents.get(name) or {}
        lines.append(
            f"- `{name}`: status={row.get('status', '-')}, duration_ms={row.get('duration_ms', '-')}"
        )
        for rec in (row.get("recommendations") or [])[:3]:
            lines.append(f"  - {rec}")

    lines.append("")
    lines.append("## Recommendations")
    for item in payload.get("recommendations") or []:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def write_outputs(payload: Dict[str, Any], *, output_json: Path, output_md: Path) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    output_md.write_text(to_markdown(payload), encoding="utf-8")


def _first_non_empty_text(candidates: list[object]) -> str:
    for item in candidates:
        text = str(item or "").strip()
        if text:
            return text
    return ""


def _pick_reason_aligned_recommendation(
    *,
    reason_code: str,
    primary_recommendations: list[object],
    fallback_recommendations: list[object],
) -> str:
    keyword_map = {
        "post_verify_failed": ("复验", "未收口"),
        "manual_confirmation_required": ("人工确认", "待审核"),
        "llm_low_quality_pool": ("账号池", "低质量", "弱 key", "降优先级"),
        "bootstrap_monitoring": ("bootstrap", "小样本"),
        "auto_actions_executed": ("自动修复", "自动学习", "自动调权", "自动校准", "自动重启"),
    }

    normalized_primary = [str(item or "").strip() for item in primary_recommendations]
    keywords = keyword_map.get(str(reason_code or "").strip(), ())
    if keywords:
        for item in normalized_primary:
            if item and any(keyword in item for keyword in keywords):
                return item
    return _first_non_empty_text(normalized_primary + [*fallback_recommendations])


def _extract_quality_reason_project_context(
    *, agents: Dict[str, Any], reason_code: str
) -> Dict[str, str]:
    if str(reason_code or "").strip() != "manual_confirmation_required":
        return {}
    learning_calibration = (
        (agents.get("learning_calibration") or {}) if isinstance(agents, dict) else {}
    )
    manual_confirmation_rows = learning_calibration.get("manual_confirmation_rows")
    if isinstance(manual_confirmation_rows, list):
        for item in manual_confirmation_rows:
            if not isinstance(item, dict):
                continue
            project_id = str(item.get("project_id") or "").strip()
            project_name = str(item.get("project_name") or "").strip() or project_id
            detail = str(item.get("detail") or "").strip()
            if project_id or project_name or detail:
                return {
                    "project_id": project_id,
                    "project_name": project_name,
                    "detail": detail,
                }

    checks = learning_calibration.get("checks") or {}
    project_rows = checks.get("projects_health") if isinstance(checks, dict) else None
    if isinstance(project_rows, list):
        for item in project_rows:
            if not isinstance(item, dict):
                continue
            verify_governance = item.get("verify_governance") or {}
            summary = (verify_governance.get("json") or {}).get("summary") or {}
            if not bool(summary.get("manual_confirmation_required")):
                continue
            project_id = str(item.get("project_id") or "").strip()
            project_name = str(item.get("project_name") or "").strip() or project_id
            detail_parts = []
            pending_extreme = int(summary.get("pending_extreme_ground_truth_count") or 0)
            if pending_extreme > 0:
                detail_parts.append(f"待人工确认极端样本 {pending_extreme} 条")
            matched_submission_count = int(
                ((verify_governance.get("json") or {}).get("score_preview") or {}).get(
                    "matched_submission_count"
                )
                or 0
            )
            if matched_submission_count <= 0:
                detail_parts.append("当前暂无可关联预测样本")
            return {
                "project_id": project_id,
                "project_name": project_name,
                "detail": "；".join(detail_parts),
            }
    return {}


def _build_history_entry(payload: Dict[str, Any]) -> Dict[str, Any]:
    overall = payload.get("overall") or {}
    settings = payload.get("settings") or {}
    agents = payload.get("agents") or {}
    runtime_repair = (agents.get("runtime_repair") or {}) if isinstance(agents, dict) else {}
    data_hygiene = (agents.get("data_hygiene") or {}) if isinstance(agents, dict) else {}
    learning_calibration = (
        (agents.get("learning_calibration") or {}) if isinstance(agents, dict) else {}
    )
    evolution = (agents.get("evolution") or {}) if isinstance(agents, dict) else {}

    runtime_metrics = runtime_repair.get("metrics") or {}
    runtime_actions = runtime_repair.get("actions") or {}
    data_hygiene_actions = data_hygiene.get("actions") or {}
    learning_metrics = learning_calibration.get("metrics") or {}
    evolution_metrics = evolution.get("metrics") or {}

    auto_repair_attempted_count = 0
    auto_repair_success_count = 0
    for action_row in (
        data_hygiene_actions.get("repair") or {},
        runtime_actions.get("repair_data_hygiene") or {},
        runtime_actions.get("restart_runtime") or {},
    ):
        if not isinstance(action_row, dict):
            continue
        if bool(action_row.get("attempted")):
            auto_repair_attempted_count += 1
            if bool(action_row.get("ok")):
                auto_repair_success_count += 1

    evolve_attempted_count = int(learning_metrics.get("evolve_attempted_count") or 0)
    evolve_success_count = int(learning_metrics.get("evolve_success_count") or 0)
    reflection_attempted_count = int(learning_metrics.get("reflection_attempted_count") or 0)
    reflection_success_count = int(learning_metrics.get("reflection_success_count") or 0)
    recommendations = payload.get("recommendations") or []
    data_hygiene_recommendations = data_hygiene.get("recommendations") or []
    runtime_recommendations = runtime_repair.get("recommendations") or []
    learning_recommendations = learning_calibration.get("recommendations") or []
    evolution_recommendations = evolution.get("recommendations") or []
    warn_agent_names = sorted(
        [
            str(name or "")
            for name, row in agents.items()
            if isinstance(row, dict) and str(row.get("status") or "") == "warn"
        ]
    )
    fail_agent_names = sorted(
        [
            str(name or "")
            for name, row in agents.items()
            if isinstance(row, dict) and str(row.get("status") or "") == "fail"
        ]
    )
    manual_confirmation_required_count = int(
        learning_metrics.get("manual_confirmation_required_count") or 0
    )
    post_verify_failed_count = int(learning_metrics.get("post_verify_failed_count") or 0)
    bootstrap_monitoring_count = int(learning_metrics.get("bootstrap_monitoring_count") or 0)
    llm_account_low_quality_pool_count = int(
        learning_metrics.get("llm_account_low_quality_pool_count") or 0
    )
    quality_reason_code = "stable_pass"
    quality_audit_label = "巡检稳定通过"
    quality_reason_detail = ""
    quality_reason_project_id = ""
    quality_reason_project_name = ""
    quality_reason_project_detail = ""
    top_recommendation = ""
    if fail_agent_names:
        quality_reason_code = "failed_agents"
        quality_audit_label = "存在失败智能体"
        quality_reason_detail = "失败智能体：" + "、".join(fail_agent_names)
        failed_agent_row = (
            agents.get(fail_agent_names[0]) if isinstance(agents, dict) and fail_agent_names else {}
        )
        failed_agent_recommendations = (
            failed_agent_row.get("recommendations") or []
            if isinstance(failed_agent_row, dict)
            else []
        )
        top_recommendation = (
            str(failed_agent_recommendations[0] or "").strip()
            if failed_agent_recommendations
            else ""
        )
    elif post_verify_failed_count > 0:
        quality_reason_code = "post_verify_failed"
        quality_audit_label = "自动学习复验未收口"
        quality_reason_detail = f"复验失败 {post_verify_failed_count} 处"
        top_recommendation = _pick_reason_aligned_recommendation(
            reason_code=quality_reason_code,
            primary_recommendations=learning_recommendations,
            fallback_recommendations=recommendations,
        )
    elif manual_confirmation_required_count > 0:
        quality_reason_code = "manual_confirmation_required"
        quality_audit_label = "自动学习需人工确认"
        quality_reason_detail = f"人工确认需求 {manual_confirmation_required_count} 项"
        top_recommendation = _pick_reason_aligned_recommendation(
            reason_code=quality_reason_code,
            primary_recommendations=learning_recommendations,
            fallback_recommendations=recommendations,
        )
    elif llm_account_low_quality_pool_count > 0:
        quality_reason_code = "llm_low_quality_pool"
        quality_audit_label = "LLM 账号池质量偏低"
        quality_reason_detail = f"低质量账号池 {llm_account_low_quality_pool_count} 组"
        top_recommendation = _pick_reason_aligned_recommendation(
            reason_code=quality_reason_code,
            primary_recommendations=learning_recommendations,
            fallback_recommendations=recommendations,
        )
    elif bootstrap_monitoring_count > 0:
        quality_reason_code = "bootstrap_monitoring"
        quality_audit_label = "小样本 bootstrap 监控中"
        quality_reason_detail = f"bootstrap 监控项目 {bootstrap_monitoring_count} 个"
        top_recommendation = _pick_reason_aligned_recommendation(
            reason_code=quality_reason_code,
            primary_recommendations=learning_recommendations,
            fallback_recommendations=recommendations,
        )
    elif auto_repair_attempted_count > 0 or evolve_attempted_count + reflection_attempted_count > 0:
        quality_reason_code = "auto_actions_executed"
        quality_audit_label = "已执行自动动作"
        quality_reason_detail = (
            f"自动修复 {auto_repair_attempted_count}/{auto_repair_success_count}；"
            f"自动学习 {evolve_attempted_count + reflection_attempted_count}/"
            f"{evolve_success_count + reflection_success_count}"
        )
        top_recommendation = _pick_reason_aligned_recommendation(
            reason_code=quality_reason_code,
            primary_recommendations=[
                *runtime_recommendations,
                *learning_recommendations,
            ],
            fallback_recommendations=recommendations,
        )
    elif str(overall.get("status") or "") == "warn":
        quality_reason_code = "warn"
        quality_audit_label = "巡检待关注"
        quality_reason_detail = (
            "告警智能体：" + "、".join(warn_agent_names) if warn_agent_names else "本轮存在待收口项"
        )
        top_recommendation = str(recommendations[0] or "").strip() if recommendations else ""
    else:
        top_recommendation = (
            str(runtime_recommendations[0] or "").strip()
            if runtime_recommendations
            else (
                str(data_hygiene_recommendations[0] or "").strip()
                if data_hygiene_recommendations
                else (
                    str(evolution_recommendations[0] or "").strip()
                    if evolution_recommendations
                    else ""
                )
            )
        )
    reason_project_context = _extract_quality_reason_project_context(
        agents=agents,
        reason_code=quality_reason_code,
    )
    quality_reason_project_id = str(reason_project_context.get("project_id") or "").strip()
    quality_reason_project_name = (
        str(reason_project_context.get("project_name") or "").strip() or quality_reason_project_id
    )
    quality_reason_project_detail = str(reason_project_context.get("detail") or "").strip()

    return {
        "generated_at": payload.get("generated_at"),
        "overall_status": str(overall.get("status") or ""),
        "pass_count": int(overall.get("pass_count") or 0),
        "warn_count": int(overall.get("warn_count") or 0),
        "fail_count": int(overall.get("fail_count") or 0),
        "duration_ms": int(overall.get("duration_ms") or 0),
        "agent_count": int(payload.get("agent_count") or 0),
        "auto_repair_enabled": bool(settings.get("auto_repair")),
        "auto_evolve_enabled": bool(settings.get("auto_evolve")),
        "auto_repair_attempted_count": auto_repair_attempted_count,
        "auto_repair_success_count": auto_repair_success_count,
        "auto_fixed_count": int(runtime_metrics.get("auto_fixed_count") or 0),
        "auto_evolve_attempted_count": evolve_attempted_count + reflection_attempted_count,
        "auto_evolve_success_count": evolve_success_count + reflection_success_count,
        "manual_confirmation_required_count": manual_confirmation_required_count,
        "post_verify_failed_count": post_verify_failed_count,
        "pending_evolve_after": int(evolution_metrics.get("pending_evolve_after") or 0),
        "warn_agent_names": warn_agent_names,
        "fail_agent_names": fail_agent_names,
        "quality_reason_code": quality_reason_code,
        "quality_reason_label": quality_audit_label,
        "quality_reason_detail": quality_reason_detail,
        "quality_reason_project_id": quality_reason_project_id,
        "quality_reason_project_name": quality_reason_project_name,
        "quality_reason_project_detail": quality_reason_project_detail,
        "quality_audit_label": quality_audit_label,
        "top_recommendation": top_recommendation,
    }


def _update_history(*, history_json: Path, entry: Dict[str, Any], history_limit: int = 30) -> None:
    history_json.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    if history_json.exists():
        try:
            raw = json.loads(history_json.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                rows = [item for item in raw if isinstance(item, dict)]
        except Exception:
            rows = []
    rows.append(entry)
    rows = rows[-max(1, int(history_limit)) :]
    history_json.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    from app.engine.ops_agents import run_ops_agents_cycle

    parser = argparse.ArgumentParser(description="Run multi-agent ops cycle for system self-heal.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--auto-repair", type=int, default=1, choices=[0, 1])
    parser.add_argument("--auto-evolve", type=int, default=1, choices=[0, 1])
    parser.add_argument("--min-evolve-samples", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=float, default=8.0)
    parser.add_argument("--max-workers", type=int, default=3)
    parser.add_argument("--interval-seconds", type=float, default=0.0)
    parser.add_argument("--max-cycles", type=int, default=1)
    parser.add_argument(
        "--strict", action="store_true", help="Exit non-zero when overall status is fail."
    )
    parser.add_argument(
        "--output-json",
        default=str(ROOT / "build" / "ops_agents_status.json"),
    )
    parser.add_argument(
        "--output-md",
        default=str(ROOT / "build" / "ops_agents_status.md"),
    )
    parser.add_argument(
        "--history-json",
        default=str(ROOT / "build" / "ops_agents_history.json"),
    )
    parser.add_argument("--history-limit", type=int, default=30)
    args = parser.parse_args()

    output_json = Path(args.output_json)
    output_md = Path(args.output_md)
    history_json = Path(args.history_json)
    cycles = 0
    while True:
        cycles += 1
        payload = run_ops_agents_cycle(
            base_url=args.base_url,
            api_key=(args.api_key or None),
            auto_repair=bool(args.auto_repair),
            auto_evolve=bool(args.auto_evolve),
            min_evolve_samples=max(1, int(args.min_evolve_samples)),
            timeout=max(2.0, float(args.timeout_seconds)),
            max_workers=max(1, int(args.max_workers)),
        )
        payload["runtime"] = {
            "cycle": cycles,
            "interval_seconds": float(args.interval_seconds),
            "max_cycles": int(args.max_cycles),
            "pid": os.getpid(),
            "launcher": os.environ.get("OPS_AGENTS_LAUNCHER", "direct"),
        }
        write_outputs(payload, output_json=output_json, output_md=output_md)
        _update_history(
            history_json=history_json,
            entry=_build_history_entry(payload),
            history_limit=max(1, int(args.history_limit)),
        )
        overall_status = str((payload.get("overall") or {}).get("status") or "fail")
        print(
            f"[ops_agents] cycle={cycles} overall={overall_status} "
            f"json={output_json} md={output_md}",
            flush=True,
        )
        if args.strict and overall_status == "fail":
            return 1
        if args.interval_seconds <= 0:
            return 0
        if args.max_cycles > 0 and cycles >= args.max_cycles:
            return 0
        time.sleep(max(1.0, float(args.interval_seconds)))


if __name__ == "__main__":
    raise SystemExit(main())
