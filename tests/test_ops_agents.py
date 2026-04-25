from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import scripts.ops_agents as soa
from app.engine import ops_agents as oa


def _ok_llm_status_response(
    *,
    provider_health: dict[str, str] | None = None,
    provider_quality: dict[str, str] | None = None,
    provider_review_stats: dict[str, dict] | None = None,
    provider_quality_score: dict[str, float] | None = None,
    provider_chain: list[str] | None = None,
    openai_pool_health: dict[str, int] | None = None,
    gemini_pool_health: dict[str, int] | None = None,
    openai_pool_quality: dict[str, float] | None = None,
    gemini_pool_quality: dict[str, float] | None = None,
) -> dict:
    chain = provider_chain if provider_chain is not None else ["openai", "gemini"]
    health = (
        provider_health
        if provider_health is not None
        else {"openai": "healthy", "gemini": "healthy"}
    )
    quality = (
        provider_quality
        if provider_quality is not None
        else {"openai": "stable", "gemini": "stable"}
    )
    review_stats = (
        provider_review_stats
        if provider_review_stats is not None
        else {
            "openai": {
                "confirmed_count": 0,
                "diverged_count": 0,
                "unavailable_count": 0,
                "fallback_only_count": 0,
                "last_status": None,
                "last_at": None,
            },
            "gemini": {
                "confirmed_count": 0,
                "diverged_count": 0,
                "unavailable_count": 0,
                "fallback_only_count": 0,
                "last_status": None,
                "last_at": None,
            },
        }
    )
    quality_score = (
        provider_quality_score
        if provider_quality_score is not None
        else {"openai": 50.0, "gemini": 50.0}
    )
    openai_pool = (
        openai_pool_health
        if openai_pool_health is not None
        else {"total_accounts": 4, "healthy_accounts": 4, "cooling_accounts": 0}
    )
    gemini_pool = (
        gemini_pool_health
        if gemini_pool_health is not None
        else {"total_accounts": 2, "healthy_accounts": 2, "cooling_accounts": 0}
    )
    openai_quality = (
        openai_pool_quality
        if openai_pool_quality is not None
        else {
            "total_accounts": 4.0,
            "rated_accounts": 0.0,
            "sufficiently_rated_accounts": 0.0,
            "average_quality_score": 50.0,
            "best_quality_score": 50.0,
            "worst_quality_score": 50.0,
            "low_quality_accounts": 0.0,
        }
    )
    gemini_quality = (
        gemini_pool_quality
        if gemini_pool_quality is not None
        else {
            "total_accounts": 2.0,
            "rated_accounts": 0.0,
            "sufficiently_rated_accounts": 0.0,
            "average_quality_score": 50.0,
            "best_quality_score": 50.0,
            "worst_quality_score": 50.0,
            "low_quality_accounts": 0.0,
        }
    )
    return {
        "ok": True,
        "status_code": 200,
        "elapsed_ms": 1,
        "json": {
            "evolution_backend": chain[0] if chain else "rules",
            "requested_backend": "auto",
            "provider_chain": chain,
            "provider_health": health,
            "provider_quality": quality,
            "provider_review_stats": review_stats,
            "provider_quality_score": quality_score,
            "openai_account_count": 4,
            "openai_pool_health": openai_pool,
            "openai_pool_quality": openai_quality,
            "gemini_account_count": 2,
            "gemini_pool_health": gemini_pool,
            "gemini_pool_quality": gemini_quality,
        },
        "error": None,
    }


def _ops_agents_status_payload(
    *,
    overall_status: str = "warn",
    learning_metrics: dict | None = None,
    runtime_metrics: dict | None = None,
    runtime_actions: dict | None = None,
    learning_recommendations: list[str] | None = None,
    runtime_recommendations: list[str] | None = None,
    recommendations: list[str] | None = None,
    manual_confirmation_rows: list[dict] | None = None,
) -> dict:
    return {
        "generated_at": "2026-04-01T00:00:00+00:00",
        "overall": {
            "status": overall_status,
            "pass_count": 0,
            "warn_count": 1 if overall_status == "warn" else 0,
            "fail_count": 1 if overall_status == "fail" else 0,
            "duration_ms": 12,
        },
        "settings": {
            "auto_repair": True,
            "auto_evolve": True,
        },
        "agent_count": 4,
        "agents": {
            "runtime_repair": {
                "status": "pass",
                "metrics": runtime_metrics or {},
                "actions": runtime_actions or {},
                "recommendations": runtime_recommendations or [],
            },
            "data_hygiene": {
                "status": "pass",
                "actions": {},
                "recommendations": [],
            },
            "learning_calibration": {
                "status": "warn",
                "metrics": learning_metrics or {},
                "manual_confirmation_rows": manual_confirmation_rows or [],
                "recommendations": learning_recommendations or [],
            },
            "evolution": {
                "status": "pass",
                "metrics": {},
                "recommendations": [],
            },
        },
        "recommendations": recommendations or [],
    }


def test_run_ops_agents_cycle_short_circuit_on_sre_fail(monkeypatch):
    monkeypatch.setattr(
        oa,
        "_run_sre_watchdog",
        lambda **kwargs: {
            "name": "sre_watchdog",
            "status": "fail",
            "duration_ms": 1,
            "checks": {},
            "actions": {},
            "recommendations": ["sre failed"],
        },
    )

    result = oa.run_ops_agents_cycle(base_url="http://127.0.0.1:8000", max_workers=1)
    assert result["overall"]["status"] == "fail"
    assert result["overall"]["fail_count"] >= 1
    assert result["agents"]["sre_watchdog"]["status"] == "fail"
    assert result["agents"]["data_hygiene"]["status"] == "fail"
    assert result["agents"]["runtime_repair"]["status"] == "fail"
    assert result["agents"]["project_flow"]["status"] == "fail"
    assert result["agents"]["tender_project_flow"]["status"] == "fail"
    assert result["agents"]["upload_flow"]["status"] == "fail"
    assert result["agents"]["scoring_quality"]["status"] == "fail"
    assert result["agents"]["evolution"]["status"] == "fail"
    assert result["agents"]["learning_calibration"]["status"] == "fail"
    assert "triage" in result


def test_run_ops_agents_cycle_warn_from_sub_agents(monkeypatch):
    monkeypatch.setattr(
        oa,
        "_run_sre_watchdog",
        lambda **kwargs: {
            "name": "sre_watchdog",
            "status": "pass",
            "duration_ms": 1,
            "checks": {},
            "actions": {},
            "recommendations": [],
        },
    )
    monkeypatch.setattr(
        oa,
        "_run_data_hygiene_agent",
        lambda **kwargs: {
            "name": "data_hygiene",
            "status": "pass",
            "duration_ms": 1,
            "checks": {},
            "actions": {},
            "metrics": {},
            "recommendations": [],
        },
    )
    monkeypatch.setattr(
        oa,
        "_run_runtime_repair_agent",
        lambda **kwargs: {
            "name": "runtime_repair",
            "status": "pass",
            "duration_ms": 1,
            "checks": {},
            "actions": {},
            "metrics": {},
            "recommendations": [],
        },
    )
    monkeypatch.setattr(
        oa,
        "_run_project_flow_agent",
        lambda **kwargs: {
            "name": "project_flow",
            "status": "pass",
            "duration_ms": 1,
            "checks": {},
            "actions": {},
            "metrics": {},
            "recommendations": [],
        },
    )
    monkeypatch.setattr(
        oa,
        "_run_scoring_quality_agent",
        lambda **kwargs: {
            "name": "scoring_quality",
            "status": "warn",
            "duration_ms": 1,
            "checks": {},
            "actions": {},
            "metrics": {},
            "recommendations": ["quality watch"],
        },
    )
    monkeypatch.setattr(
        oa,
        "_run_tender_project_flow_agent",
        lambda **kwargs: {
            "name": "tender_project_flow",
            "status": "pass",
            "duration_ms": 1,
            "checks": {},
            "actions": {},
            "metrics": {},
            "recommendations": [],
        },
    )
    monkeypatch.setattr(
        oa,
        "_run_upload_flow_agent",
        lambda **kwargs: {
            "name": "upload_flow",
            "status": "pass",
            "duration_ms": 1,
            "checks": {},
            "actions": {},
            "metrics": {},
            "recommendations": [],
        },
    )
    monkeypatch.setattr(
        oa,
        "_run_evolution_agent",
        lambda **kwargs: {
            "name": "evolution",
            "status": "pass",
            "duration_ms": 1,
            "checks": {},
            "actions": {},
            "metrics": {},
            "recommendations": [],
        },
    )
    monkeypatch.setattr(
        oa,
        "_run_learning_calibration_agent",
        lambda **kwargs: {
            "name": "learning_calibration",
            "status": "pass",
            "duration_ms": 1,
            "checks": {},
            "actions": {},
            "metrics": {},
            "recommendations": [],
        },
    )

    result = oa.run_ops_agents_cycle(base_url="http://127.0.0.1:8000", max_workers=3)
    assert result["overall"]["status"] == "warn"
    assert result["overall"]["warn_count"] == 1
    assert result["overall"]["fail_count"] == 0
    assert "quality watch" in result["recommendations"]
    assert result["agent_count"] == len(oa.OPS_AGENT_NAMES)
    assert result["expected_agent_names"] == list(oa.OPS_AGENT_NAMES)
    assert result["missing_agent_names"] == []


def test_runtime_repair_agent_auto_repairs_data_hygiene_and_async_parse(monkeypatch):
    calls = {"self_check": 0}

    def fake_requester(**kwargs):
        method = str(kwargs.get("method") or "")
        url = str(kwargs.get("url") or "")
        if method == "GET" and url.endswith("/api/v1/system/self_check"):
            calls["self_check"] += 1
            if calls["self_check"] == 1:
                return {
                    "ok": True,
                    "status_code": 200,
                    "elapsed_ms": 1,
                    "json": {
                        "ok": True,
                        "degraded": True,
                        "items": [
                            {"name": "health", "ok": True, "required": True},
                            {
                                "name": "data_hygiene",
                                "ok": False,
                                "required": False,
                            },
                            {
                                "name": "vision_parse_queue_healthy",
                                "ok": False,
                                "required": False,
                            },
                        ],
                    },
                    "error": None,
                }
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "ok": True,
                    "degraded": False,
                    "items": [{"name": "health", "ok": True, "required": True}],
                },
                "error": None,
            }
        if method == "POST" and url.endswith("/api/v1/system/data_hygiene/repair"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {"repaired": True},
                "error": None,
            }
        raise AssertionError(f"unexpected request: {method} {url}")

    monkeypatch.setattr(
        oa,
        "_run_restart_command",
        lambda restart_cmd: {
            "attempted": True,
            "ok": True,
            "returncode": 0,
            "error": None,
        },
    )

    result = oa._run_runtime_repair_agent(
        base_url="http://127.0.0.1:8000",
        api_key=None,
        timeout=5.0,
        auto_repair=True,
        restart_cmd=["./scripts/restart_server.sh"],
        requester=fake_requester,
    )

    assert result["status"] == "pass"
    assert result["actions"]["repair_data_hygiene"]["attempted"] is True
    assert result["actions"]["restart_runtime"]["attempted"] is True
    assert result["metrics"]["auto_fixed_count"] == 2
    assert any("已自动修复" in row for row in result["recommendations"])


def test_runtime_repair_agent_skips_restart_for_busy_parse_queue(monkeypatch):
    def fake_requester(**kwargs):
        method = str(kwargs.get("method") or "")
        url = str(kwargs.get("url") or "")
        if method == "GET" and url.endswith("/api/v1/system/self_check"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "ok": True,
                    "degraded": True,
                    "summary": {
                        "parse_job_summary": {
                            "backlog": 57,
                            "status_counts": {
                                "processing": 20,
                                "queued": 37,
                            },
                        }
                    },
                    "items": [
                        {"name": "health", "ok": True, "required": True},
                        {
                            "name": "vision_parse_queue_healthy",
                            "ok": False,
                            "required": False,
                            "detail": "worker=True, backlog=57",
                        },
                        {
                            "name": "material_parse_backlog_ok",
                            "ok": False,
                            "required": False,
                            "detail": "backlog=57",
                        },
                    ],
                },
                "error": None,
            }
        raise AssertionError(f"unexpected request: {method} {url}")

    monkeypatch.setattr(
        oa,
        "_run_restart_command",
        lambda restart_cmd: {
            "attempted": True,
            "ok": True,
            "returncode": 0,
            "error": None,
        },
    )

    result = oa._run_runtime_repair_agent(
        base_url="http://127.0.0.1:8000",
        api_key=None,
        timeout=5.0,
        auto_repair=True,
        restart_cmd=["./scripts/restart_server.sh"],
        requester=fake_requester,
    )

    assert result["status"] == "warn"
    assert result["actions"]["restart_runtime"]["attempted"] is False
    assert result["metrics"]["repairable_after_count"] == 2
    assert result["metrics"]["restartable_after_count"] == 0
    assert result["metrics"]["async_parse_busy_after_count"] == 1
    assert any("繁忙处理态" in row for row in result["recommendations"])


def test_runtime_repair_agent_warns_on_non_repairable_optional_failures():
    def fake_requester(**kwargs):
        method = str(kwargs.get("method") or "")
        url = str(kwargs.get("url") or "")
        if method == "GET" and url.endswith("/api/v1/system/self_check"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "ok": True,
                    "degraded": True,
                    "items": [
                        {"name": "health", "ok": True, "required": True},
                        {
                            "name": "parser_ocr",
                            "ok": False,
                            "required": False,
                        },
                    ],
                },
                "error": None,
            }
        raise AssertionError(f"unexpected request: {method} {url}")

    result = oa._run_runtime_repair_agent(
        base_url="http://127.0.0.1:8000",
        api_key=None,
        timeout=5.0,
        auto_repair=True,
        restart_cmd=["./scripts/restart_server.sh"],
        requester=fake_requester,
    )

    assert result["status"] == "warn"
    assert result["actions"]["repair_data_hygiene"]["attempted"] is False
    assert result["actions"]["restart_runtime"]["attempted"] is False
    assert result["metrics"]["non_repairable_after_count"] == 1
    assert result["metrics"]["optional_after_count"] == 1
    assert result["metrics"]["optional_parser_after_count"] == 1
    assert any("仅告警项" in row for row in result["recommendations"])


def test_run_ops_agents_cycle_retries_smoke_after_restart(monkeypatch, tmp_path: Path):
    project_calls = {"count": 0}
    restart_calls = {"count": 0}
    monkeypatch.setattr(
        oa,
        "OPS_SMOKE_RUNTIME_RETRY_STATE_PATH",
        tmp_path / "ops_agents_smoke_retry_state.json",
    )

    monkeypatch.setattr(
        oa,
        "_run_sre_watchdog",
        lambda **kwargs: {
            "name": "sre_watchdog",
            "status": "pass",
            "duration_ms": 1,
            "checks": {},
            "actions": {},
            "recommendations": [],
        },
    )
    monkeypatch.setattr(
        oa,
        "_run_data_hygiene_agent",
        lambda **kwargs: {
            "name": "data_hygiene",
            "status": "pass",
            "duration_ms": 1,
            "checks": {},
            "actions": {},
            "metrics": {},
            "recommendations": [],
        },
    )
    monkeypatch.setattr(
        oa,
        "_run_runtime_repair_agent",
        lambda **kwargs: {
            "name": "runtime_repair",
            "status": "pass",
            "duration_ms": 1,
            "checks": {},
            "actions": {},
            "metrics": {},
            "recommendations": [],
        },
    )

    def fake_project_flow(**kwargs):
        project_calls["count"] += 1
        if project_calls["count"] == 1:
            return {
                "name": "project_flow",
                "status": "fail",
                "duration_ms": 1,
                "checks": {},
                "actions": {},
                "metrics": {},
                "recommendations": ["first smoke failed"],
            }
        return {
            "name": "project_flow",
            "status": "pass",
            "duration_ms": 1,
            "checks": {},
            "actions": {},
            "metrics": {},
            "recommendations": ["retry smoke passed"],
        }

    monkeypatch.setattr(oa, "_run_project_flow_agent", fake_project_flow)
    monkeypatch.setattr(
        oa,
        "_run_tender_project_flow_agent",
        lambda **kwargs: {
            "name": "tender_project_flow",
            "status": "pass",
            "duration_ms": 1,
            "checks": {},
            "actions": {},
            "metrics": {},
            "recommendations": [],
        },
    )
    monkeypatch.setattr(
        oa,
        "_run_upload_flow_agent",
        lambda **kwargs: {
            "name": "upload_flow",
            "status": "pass",
            "duration_ms": 1,
            "checks": {},
            "actions": {},
            "metrics": {},
            "recommendations": [],
        },
    )
    monkeypatch.setattr(
        oa,
        "_run_scoring_quality_agent",
        lambda **kwargs: {
            "name": "scoring_quality",
            "status": "pass",
            "duration_ms": 1,
            "checks": {},
            "actions": {},
            "metrics": {},
            "recommendations": [],
        },
    )
    monkeypatch.setattr(
        oa,
        "_run_evolution_agent",
        lambda **kwargs: {
            "name": "evolution",
            "status": "pass",
            "duration_ms": 1,
            "checks": {},
            "actions": {},
            "metrics": {},
            "recommendations": [],
        },
    )
    monkeypatch.setattr(
        oa,
        "_run_learning_calibration_agent",
        lambda **kwargs: {
            "name": "learning_calibration",
            "status": "pass",
            "duration_ms": 1,
            "checks": {},
            "actions": {},
            "metrics": {},
            "recommendations": [],
        },
    )
    monkeypatch.setattr(
        oa,
        "_run_restart_command",
        lambda restart_cmd: (
            restart_calls.__setitem__("count", restart_calls["count"] + 1)
            or {
                "attempted": True,
                "ok": True,
                "returncode": 0,
                "error": None,
            }
        ),
    )

    result = oa.run_ops_agents_cycle(base_url="http://127.0.0.1:8000", max_workers=2)

    assert result["overall"]["status"] == "pass"
    assert project_calls["count"] == 2
    assert restart_calls["count"] == 1
    assert result["agents"]["project_flow"]["status"] == "pass"
    assert result["agents"]["project_flow"]["actions"]["runtime_retry"]["attempted"] is True
    assert result["agents"]["project_flow"]["actions"]["runtime_retry"]["recovered"] is True
    assert any(
        "自动重启并完成 smoke 重试恢复" in row
        for row in result["agents"]["project_flow"]["recommendations"]
    )


def test_run_ops_agents_cycle_skips_smoke_restart_during_cooldown(
    monkeypatch,
    tmp_path: Path,
):
    project_calls = {"count": 0}
    restart_calls = {"count": 0}
    retry_state_path = tmp_path / "ops_agents_smoke_retry_state.json"
    monkeypatch.setattr(oa, "OPS_SMOKE_RUNTIME_RETRY_STATE_PATH", retry_state_path)
    oa._save_smoke_runtime_retry_state(
        {
            "project_flow": {
                "attempted_at": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
                "outcome": "restart_ok",
            }
        },
        retry_state_path,
    )

    monkeypatch.setattr(
        oa,
        "_run_sre_watchdog",
        lambda **kwargs: {
            "name": "sre_watchdog",
            "status": "pass",
            "duration_ms": 1,
            "checks": {},
            "actions": {},
            "recommendations": [],
        },
    )
    monkeypatch.setattr(
        oa,
        "_run_data_hygiene_agent",
        lambda **kwargs: {
            "name": "data_hygiene",
            "status": "pass",
            "duration_ms": 1,
            "checks": {},
            "actions": {},
            "metrics": {},
            "recommendations": [],
        },
    )
    monkeypatch.setattr(
        oa,
        "_run_runtime_repair_agent",
        lambda **kwargs: {
            "name": "runtime_repair",
            "status": "pass",
            "duration_ms": 1,
            "checks": {},
            "actions": {},
            "metrics": {},
            "recommendations": [],
        },
    )

    def fake_project_flow(**kwargs):
        project_calls["count"] += 1
        return {
            "name": "project_flow",
            "status": "fail",
            "duration_ms": 1,
            "checks": {},
            "actions": {},
            "metrics": {},
            "recommendations": ["first smoke failed"],
        }

    monkeypatch.setattr(oa, "_run_project_flow_agent", fake_project_flow)
    monkeypatch.setattr(
        oa,
        "_run_tender_project_flow_agent",
        lambda **kwargs: {
            "name": "tender_project_flow",
            "status": "pass",
            "duration_ms": 1,
            "checks": {},
            "actions": {},
            "metrics": {},
            "recommendations": [],
        },
    )
    monkeypatch.setattr(
        oa,
        "_run_upload_flow_agent",
        lambda **kwargs: {
            "name": "upload_flow",
            "status": "pass",
            "duration_ms": 1,
            "checks": {},
            "actions": {},
            "metrics": {},
            "recommendations": [],
        },
    )
    monkeypatch.setattr(
        oa,
        "_run_scoring_quality_agent",
        lambda **kwargs: {
            "name": "scoring_quality",
            "status": "pass",
            "duration_ms": 1,
            "checks": {},
            "actions": {},
            "metrics": {},
            "recommendations": [],
        },
    )
    monkeypatch.setattr(
        oa,
        "_run_evolution_agent",
        lambda **kwargs: {
            "name": "evolution",
            "status": "pass",
            "duration_ms": 1,
            "checks": {},
            "actions": {},
            "metrics": {},
            "recommendations": [],
        },
    )
    monkeypatch.setattr(
        oa,
        "_run_learning_calibration_agent",
        lambda **kwargs: {
            "name": "learning_calibration",
            "status": "pass",
            "duration_ms": 1,
            "checks": {},
            "actions": {},
            "metrics": {},
            "recommendations": [],
        },
    )
    monkeypatch.setattr(
        oa,
        "_run_restart_command",
        lambda restart_cmd: (
            restart_calls.__setitem__("count", restart_calls["count"] + 1)
            or {
                "attempted": True,
                "ok": True,
                "returncode": 0,
                "error": None,
            }
        ),
    )

    result = oa.run_ops_agents_cycle(base_url="http://127.0.0.1:8000", max_workers=2)

    assert result["overall"]["status"] == "fail"
    assert project_calls["count"] == 1
    assert restart_calls["count"] == 0
    runtime_retry = result["agents"]["project_flow"]["actions"]["runtime_retry"]
    assert runtime_retry["attempted"] is False
    assert runtime_retry["cooldown_skipped"] is True
    assert runtime_retry["cooldown_remaining_seconds"] > 0
    assert any("冷却期" in row for row in result["agents"]["project_flow"]["recommendations"])


def test_smoke_runtime_retry_state_uses_storage_helpers(monkeypatch, tmp_path: Path):
    retry_state_path = tmp_path / "ops_agents_smoke_retry_state.json"
    load_calls: dict[str, object] = {}
    save_calls: dict[str, object] = {}

    def fake_load_json(path, default):
        load_calls["path"] = path
        load_calls["default"] = default
        return {"project_flow": {"attempted_at": "2026-04-03T00:00:00+00:00"}}

    def fake_save_json(path, payload):
        save_calls["path"] = path
        save_calls["payload"] = payload

    monkeypatch.setattr(oa, "load_json", fake_load_json)
    monkeypatch.setattr(oa, "save_json", fake_save_json)

    state = oa._load_smoke_runtime_retry_state(retry_state_path)
    oa._save_smoke_runtime_retry_state(
        {"project_flow": {"outcome": "restart_ok"}}, retry_state_path
    )

    assert load_calls["path"] == retry_state_path
    assert load_calls["default"] == {}
    assert state["project_flow"]["attempted_at"] == "2026-04-03T00:00:00+00:00"
    assert save_calls["path"] == retry_state_path
    assert save_calls["payload"] == {"project_flow": {"outcome": "restart_ok"}}


def test_learning_calibration_agent_auto_runs_evolve_and_reflection():
    recent_iso = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    calls = {"health": 0, "governance": 0}

    def fake_requester(**kwargs):
        method = str(kwargs.get("method") or "")
        url = str(kwargs.get("url") or "")
        if method == "GET" and url.endswith("/api/v1/config/llm_status"):
            return _ok_llm_status_response()
        if method == "GET" and url.endswith("/api/v1/projects"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": [
                    {
                        "id": "p1",
                        "name": "真实项目A",
                        "status": "submitted_to_qingtian",
                        "updated_at": recent_iso,
                    }
                ],
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects/p1/evolution/health"):
            calls["health"] += 1
            summary = {
                "ground_truth_count": 4,
                "eligible_learning_ground_truth_count": 3,
                "matched_prediction_count": 3,
                "guardrail_blocked_count": 0,
                "learning_quality_blocked_count": 0,
                "has_evolved_multipliers": calls["health"] > 1,
                "evolution_weights_usable": calls["health"] > 1,
                "last_evolution_updated_at": "",
            }
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "summary": summary,
                    "drift": {"level": "watch"},
                },
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects/p1/feedback/governance"):
            calls["governance"] += 1
            current_version = "prior_five_scale_global_offset_v1"
            if calls["governance"] > 1:
                current_version = "calib_auto_ridge_1"
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "summary": {
                        "manual_confirmation_required": False,
                        "few_shot_pending_review_count": 0,
                    },
                    "score_preview": {
                        "current_calibrator_version": current_version,
                    },
                    "version_history": [
                        {
                            "artifact": "calibration_models",
                            "latest_created_at": "",
                        }
                    ],
                },
                "error": None,
            }
        if method == "POST" and url.endswith("/api/v1/projects/p1/evolve"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {"ok": True},
                "error": None,
            }
        if method == "POST" and url.endswith("/api/v1/projects/p1/reflection/auto_run"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "ok": True,
                    "calibrator_deployed": True,
                    "patch_deployed": False,
                    "patch_auto_govern": {"action": "rollback"},
                },
                "error": None,
            }
        raise AssertionError(f"unexpected request: {method} {url}")

    result = oa._run_learning_calibration_agent(
        base_url="http://127.0.0.1:8000",
        api_key=None,
        timeout=5.0,
        auto_evolve=True,
        min_samples=3,
        requester=fake_requester,
    )

    assert result["status"] == "pass"
    assert result["metrics"]["mature_projects"] == 1
    assert result["metrics"]["pending_evolve_before"] == 1
    assert result["metrics"]["pending_evolve_after"] == 0
    assert result["metrics"]["pending_calibration_before"] == 1
    assert result["metrics"]["pending_calibration_after"] == 0
    assert result["metrics"]["calibrator_deployed_count"] == 1
    assert result["metrics"]["patch_rollback_count"] == 1
    assert result["metrics"]["post_verify_failed_count"] == 0
    assert result["actions"]["evolve"][0]["attempted"] is True
    assert result["actions"]["reflection_auto_run"][0]["attempted"] is True


def test_learning_calibration_agent_respects_project_single_sample_threshold():
    recent_iso = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    calls = {"health": 0, "governance": 0}

    def fake_requester(**kwargs):
        method = str(kwargs.get("method") or "")
        url = str(kwargs.get("url") or "")
        if method == "GET" and url.endswith("/api/v1/config/llm_status"):
            return _ok_llm_status_response()
        if method == "GET" and url.endswith("/api/v1/projects"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": [
                    {
                        "id": "p1",
                        "name": "真实项目A",
                        "status": "submitted_to_qingtian",
                        "updated_at": recent_iso,
                    }
                ],
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects/p1/evolution/health"):
            calls["health"] += 1
            summary = {
                "ground_truth_count": 1,
                "eligible_learning_ground_truth_count": 1,
                "matched_prediction_count": 1,
                "guardrail_blocked_count": 0,
                "learning_quality_blocked_count": 0,
                "evolution_weight_min_samples": 1,
                "has_evolved_multipliers": calls["health"] > 1,
                "evolution_weights_usable": calls["health"] > 1,
                "last_evolution_updated_at": "",
            }
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {"summary": summary, "drift": {"level": "low"}},
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects/p1/feedback/governance"):
            calls["governance"] += 1
            current_version = "prior_five_scale_global_offset_v1"
            if calls["governance"] > 1:
                current_version = "calib_auto_offset_1"
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "summary": {
                        "manual_confirmation_required": False,
                        "few_shot_pending_review_count": 0,
                    },
                    "score_preview": {
                        "current_calibrator_version": current_version,
                    },
                    "version_history": [
                        {"artifact": "calibration_models", "latest_created_at": ""}
                    ],
                },
                "error": None,
            }
        if method == "POST" and url.endswith("/api/v1/projects/p1/evolve"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {"ok": True},
                "error": None,
            }
        if method == "POST" and url.endswith("/api/v1/projects/p1/reflection/auto_run"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "ok": True,
                    "calibrator_deployed": True,
                    "patch_deployed": False,
                    "patch_auto_govern": {"action": "skip"},
                },
                "error": None,
            }
        raise AssertionError(f"unexpected request: {method} {url}")

    result = oa._run_learning_calibration_agent(
        base_url="http://127.0.0.1:8000",
        api_key=None,
        timeout=5.0,
        auto_evolve=True,
        min_samples=3,
        requester=fake_requester,
    )

    assert result["status"] == "pass"
    assert result["metrics"]["mature_projects"] == 1
    assert result["metrics"]["reflection_ready_projects"] == 1
    assert result["metrics"]["pending_evolve_after"] == 0
    assert result["metrics"]["pending_calibration_after"] == 0
    assert result["metrics"]["evolve_attempted_count"] == 1
    assert result["metrics"]["reflection_attempted_count"] == 1


def test_learning_calibration_agent_warns_when_manual_confirmation_required():
    recent_iso = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    def fake_requester(**kwargs):
        method = str(kwargs.get("method") or "")
        url = str(kwargs.get("url") or "")
        if method == "GET" and url.endswith("/api/v1/config/llm_status"):
            return _ok_llm_status_response()
        if method == "GET" and url.endswith("/api/v1/projects"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": [
                    {
                        "id": "p1",
                        "name": "真实项目A",
                        "status": "submitted_to_qingtian",
                        "updated_at": recent_iso,
                    }
                ],
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects/p1/evolution/health"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "summary": {
                        "ground_truth_count": 4,
                        "eligible_learning_ground_truth_count": 3,
                        "matched_prediction_count": 3,
                        "guardrail_blocked_count": 1,
                        "has_evolved_multipliers": True,
                        "evolution_weights_usable": True,
                    },
                    "drift": {"level": "low"},
                },
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects/p1/feedback/governance"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "summary": {
                        "manual_confirmation_required": True,
                        "pending_extreme_ground_truth_count": 4,
                        "manual_override_hint": "confirm_extreme_sample=1",
                        "current_calibrator_deployment_mode": "prior_fallback",
                        "few_shot_pending_review_count": 0,
                    },
                    "score_preview": {
                        "current_calibrator_version": "prior_five_scale_global_offset_v1",
                        "matched_submission_count": 0,
                    },
                    "version_history": [],
                    "recommendations": [
                        "存在 4 条极端偏差样本，自动调权/自动校准已被暂停；人工确认后再执行学习进化或一键闭环。"
                    ],
                },
                "error": None,
            }
        raise AssertionError(f"unexpected request: {method} {url}")

    result = oa._run_learning_calibration_agent(
        base_url="http://127.0.0.1:8000",
        api_key=None,
        timeout=5.0,
        auto_evolve=True,
        min_samples=3,
        requester=fake_requester,
    )

    assert result["status"] == "warn"
    assert result["metrics"]["manual_confirmation_required_count"] == 1
    assert result["metrics"]["pending_calibration_after"] == 1
    assert result["metrics"]["reflection_attempted_count"] == 0
    assert result["manual_confirmation_rows"][0]["project_id"] == "p1"
    assert result["manual_confirmation_rows"][0]["project_name"] == "真实项目A"
    assert result["manual_confirmation_rows"][0]["pending_extreme_ground_truth_count"] == 4
    assert (
        result["manual_confirmation_rows"][0]["manual_override_hint"] == "confirm_extreme_sample=1"
    )
    assert result["manual_confirmation_rows"][0]["matched_submission_count"] == 0
    assert result["manual_confirmation_rows"][0]["entrypoint_key"] == "ground_truth"
    assert (
        result["manual_confirmation_rows"][0]["entrypoint_label"]
        == "前往「5) 自我学习与进化」录入真实评标"
    )
    assert any("极端偏差样本" in row for row in result["recommendations"])


def test_learning_calibration_agent_respects_recent_reflection_cooldown():
    recent_iso = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    calibration_latest = datetime.now(timezone.utc).isoformat()

    def fake_requester(**kwargs):
        method = str(kwargs.get("method") or "")
        url = str(kwargs.get("url") or "")
        if method == "GET" and url.endswith("/api/v1/config/llm_status"):
            return _ok_llm_status_response()
        if method == "GET" and url.endswith("/api/v1/projects"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": [
                    {
                        "id": "p1",
                        "name": "真实项目A",
                        "status": "submitted_to_qingtian",
                        "updated_at": recent_iso,
                    }
                ],
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects/p1/evolution/health"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "summary": {
                        "ground_truth_count": 4,
                        "eligible_learning_ground_truth_count": 3,
                        "matched_prediction_count": 3,
                        "guardrail_blocked_count": 0,
                        "has_evolved_multipliers": True,
                        "evolution_weights_usable": True,
                    },
                    "drift": {"level": "watch"},
                },
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects/p1/feedback/governance"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "summary": {
                        "manual_confirmation_required": False,
                        "few_shot_pending_review_count": 0,
                    },
                    "score_preview": {
                        "current_calibrator_version": "calib_auto_existing",
                    },
                    "version_history": [
                        {
                            "artifact": "calibration_models",
                            "latest_created_at": calibration_latest,
                        }
                    ],
                },
                "error": None,
            }
        if method == "POST":
            raise AssertionError(f"unexpected post request: {method} {url}")
        raise AssertionError(f"unexpected request: {method} {url}")

    result = oa._run_learning_calibration_agent(
        base_url="http://127.0.0.1:8000",
        api_key=None,
        timeout=5.0,
        auto_evolve=True,
        min_samples=3,
        requester=fake_requester,
    )

    assert result["status"] == "warn"
    assert result["metrics"]["reflection_attempted_count"] == 0
    assert result["metrics"]["reflection_cooldown_skipped_count"] == 1
    assert any("60 分钟" in row for row in result["recommendations"])


def test_learning_calibration_agent_reports_bootstrap_review_failures():
    recent_iso = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    calibration_latest = datetime.now(timezone.utc).isoformat()

    def fake_requester(**kwargs):
        method = str(kwargs.get("method") or "")
        url = str(kwargs.get("url") or "")
        if method == "GET" and url.endswith("/api/v1/config/llm_status"):
            return _ok_llm_status_response()
        if method == "GET" and url.endswith("/api/v1/projects"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": [
                    {
                        "id": "p1",
                        "name": "真实项目A",
                        "status": "submitted_to_qingtian",
                        "updated_at": recent_iso,
                    }
                ],
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects/p1/evolution/health"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "summary": {
                        "ground_truth_count": 1,
                        "eligible_learning_ground_truth_count": 1,
                        "matched_prediction_count": 1,
                        "guardrail_blocked_count": 0,
                        "evolution_weight_min_samples": 1,
                        "has_evolved_multipliers": True,
                        "evolution_weights_usable": True,
                    },
                    "drift": {"level": "low"},
                },
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects/p1/feedback/governance"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "summary": {
                        "manual_confirmation_required": False,
                        "few_shot_pending_review_count": 0,
                        "current_calibrator_bootstrap_small_sample": False,
                        "current_calibrator_deployment_mode": "prior_fallback",
                        "latest_project_calibrator_deployment_mode": "bootstrap_candidate_only",
                        "latest_project_calibrator_auto_review": {
                            "checked": True,
                            "passed": False,
                            "action": "rollback",
                            "reason": "preview_worsened_beyond_tolerance",
                        },
                    },
                    "score_preview": {
                        "current_calibrator_version": "prior_five_scale_global_offset_v1",
                    },
                    "version_history": [
                        {
                            "artifact": "calibration_models",
                            "latest_created_at": calibration_latest,
                        }
                    ],
                },
                "error": None,
            }
        raise AssertionError(f"unexpected request: {method} {url}")

    result = oa._run_learning_calibration_agent(
        base_url="http://127.0.0.1:8000",
        api_key=None,
        timeout=5.0,
        auto_evolve=True,
        min_samples=1,
        requester=fake_requester,
    )

    assert result["status"] == "warn"
    assert result["metrics"]["pending_calibration_after"] == 1
    assert result["metrics"]["bootstrap_review_failed_count"] == 1
    assert any("自动阻止部署" in row for row in result["recommendations"])


def test_learning_calibration_agent_retries_when_current_calibrator_is_degraded():
    recent_iso = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    calls = {"health": 0, "governance": 0, "reflection": 0}

    def fake_requester(**kwargs):
        method = str(kwargs.get("method") or "")
        url = str(kwargs.get("url") or "")
        if method == "GET" and url.endswith("/api/v1/config/llm_status"):
            return _ok_llm_status_response()
        if method == "GET" and url.endswith("/api/v1/projects"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": [
                    {
                        "id": "p1",
                        "name": "真实项目A",
                        "status": "submitted_to_qingtian",
                        "updated_at": recent_iso,
                    }
                ],
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects/p1/evolution/health"):
            calls["health"] += 1
            degraded = calls["health"] == 1
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "summary": {
                        "ground_truth_count": 4,
                        "eligible_learning_ground_truth_count": 3,
                        "matched_prediction_count": 3,
                        "matched_score_record_count": 3,
                        "guardrail_blocked_count": 0,
                        "has_evolved_multipliers": True,
                        "evolution_weights_usable": True,
                        "current_calibrator_degraded": degraded,
                        "current_calibrator_has_rollback_candidate": degraded,
                    },
                    "drift": {"level": "low"},
                },
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects/p1/feedback/governance"):
            calls["governance"] += 1
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "summary": {
                        "manual_confirmation_required": False,
                        "few_shot_pending_review_count": 0,
                    },
                    "score_preview": {
                        "current_calibrator_version": "calib_auto_existing",
                    },
                    "version_history": [
                        {"artifact": "calibration_models", "latest_created_at": ""}
                    ],
                },
                "error": None,
            }
        if method == "POST" and url.endswith("/api/v1/projects/p1/reflection/auto_run"):
            calls["reflection"] += 1
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "ok": True,
                    "calibrator_deployed": True,
                    "calibrator_runtime_governance": {
                        "action": "rollback",
                        "updated_reports": 7,
                        "updated_submissions": 7,
                        "degraded_after": False,
                        "recovered_after": True,
                    },
                    "patch_deployed": False,
                    "patch_auto_govern": {"action": "skip"},
                },
                "error": None,
            }
        raise AssertionError(f"unexpected request: {method} {url}")

    result = oa._run_learning_calibration_agent(
        base_url="http://127.0.0.1:8000",
        api_key=None,
        timeout=5.0,
        auto_evolve=True,
        min_samples=3,
        requester=fake_requester,
    )

    assert result["status"] == "pass"
    assert calls["reflection"] == 1
    assert result["metrics"]["reflection_attempted_count"] == 1
    assert result["metrics"]["calibrator_degraded_before_count"] == 1
    assert result["metrics"]["calibrator_degraded_after_count"] == 0
    assert result["metrics"]["calibrator_rollback_candidate_count"] == 1
    assert result["metrics"]["calibrator_runtime_rollback_count"] == 1
    assert result["metrics"]["calibrator_runtime_rollback_success_count"] == 1
    assert result["metrics"]["calibrator_runtime_rollback_recovered_count"] == 1
    assert result["metrics"]["calibrator_runtime_rollback_unrecovered_count"] == 0


def test_learning_calibration_agent_warns_when_runtime_rollback_does_not_recover():
    recent_iso = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    calls = {"health": 0, "governance": 0, "reflection": 0}

    def fake_requester(**kwargs):
        method = str(kwargs.get("method") or "")
        url = str(kwargs.get("url") or "")
        if method == "GET" and url.endswith("/api/v1/config/llm_status"):
            return _ok_llm_status_response()
        if method == "GET" and url.endswith("/api/v1/projects"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": [
                    {
                        "id": "p1",
                        "name": "真实项目A",
                        "status": "submitted_to_qingtian",
                        "updated_at": recent_iso,
                    }
                ],
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects/p1/evolution/health"):
            calls["health"] += 1
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "summary": {
                        "ground_truth_count": 4,
                        "eligible_learning_ground_truth_count": 3,
                        "matched_prediction_count": 3,
                        "matched_score_record_count": 3,
                        "guardrail_blocked_count": 0,
                        "has_evolved_multipliers": True,
                        "evolution_weights_usable": True,
                        "current_calibrator_degraded": True,
                        "current_calibrator_has_rollback_candidate": True,
                    },
                    "drift": {"level": "low"},
                },
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects/p1/feedback/governance"):
            calls["governance"] += 1
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "summary": {
                        "manual_confirmation_required": False,
                        "few_shot_pending_review_count": 0,
                    },
                    "score_preview": {
                        "current_calibrator_version": "calib_auto_existing",
                    },
                    "version_history": [
                        {"artifact": "calibration_models", "latest_created_at": ""}
                    ],
                },
                "error": None,
            }
        if method == "POST" and url.endswith("/api/v1/projects/p1/reflection/auto_run"):
            calls["reflection"] += 1
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "ok": True,
                    "calibrator_deployed": True,
                    "calibrator_runtime_governance": {
                        "action": "rollback",
                        "updated_reports": 7,
                        "updated_submissions": 7,
                        "degraded_after": True,
                        "recovered_after": False,
                    },
                    "patch_deployed": False,
                    "patch_auto_govern": {"action": "skip"},
                },
                "error": None,
            }
        raise AssertionError(f"unexpected request: {method} {url}")

    result = oa._run_learning_calibration_agent(
        base_url="http://127.0.0.1:8000",
        api_key=None,
        timeout=5.0,
        auto_evolve=True,
        min_samples=3,
        requester=fake_requester,
    )

    assert result["status"] == "warn"
    assert calls["reflection"] == 1
    assert result["metrics"]["calibrator_runtime_rollback_count"] == 1
    assert result["metrics"]["calibrator_runtime_rollback_success_count"] == 1
    assert result["metrics"]["calibrator_runtime_rollback_recovered_count"] == 0
    assert result["metrics"]["calibrator_runtime_rollback_unrecovered_count"] == 1
    assert any("运行时回退后依旧显示退化" in item for item in result["recommendations"])


def test_learning_calibration_agent_warns_when_enhancement_review_diverged():
    recent_iso = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    def fake_requester(**kwargs):
        method = str(kwargs.get("method") or "")
        url = str(kwargs.get("url") or "")
        if method == "GET" and url.endswith("/api/v1/config/llm_status"):
            return _ok_llm_status_response()
        if method == "GET" and url.endswith("/api/v1/projects"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": [
                    {
                        "id": "p1",
                        "name": "真实项目A",
                        "status": "submitted_to_qingtian",
                        "updated_at": recent_iso,
                    }
                ],
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects/p1/evolution/health"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "summary": {
                        "ground_truth_count": 3,
                        "eligible_learning_ground_truth_count": 3,
                        "matched_prediction_count": 3,
                        "guardrail_blocked_count": 0,
                        "has_evolved_multipliers": True,
                        "evolution_weights_usable": True,
                        "enhancement_review_status": "diverged",
                        "enhancement_governed": True,
                    },
                    "drift": {"level": "low"},
                },
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects/p1/feedback/governance"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "summary": {
                        "manual_confirmation_required": False,
                        "few_shot_pending_review_count": 0,
                    },
                    "score_preview": {
                        "current_calibrator_version": "calib_auto_existing",
                    },
                    "version_history": [],
                },
                "error": None,
            }
        raise AssertionError(f"unexpected request: {method} {url}")

    result = oa._run_learning_calibration_agent(
        base_url="http://127.0.0.1:8000",
        api_key=None,
        timeout=5.0,
        auto_evolve=True,
        min_samples=1,
        requester=fake_requester,
    )

    assert result["status"] == "warn"
    assert result["metrics"]["enhancement_review_diverged_count"] == 1
    assert result["metrics"]["enhancement_governed_count"] == 1
    assert any("双模型分歧" in row for row in result["recommendations"])


def test_learning_calibration_agent_warns_when_llm_pool_is_degraded():
    recent_iso = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    def fake_requester(**kwargs):
        method = str(kwargs.get("method") or "")
        url = str(kwargs.get("url") or "")
        if method == "GET" and url.endswith("/api/v1/config/llm_status"):
            return _ok_llm_status_response(
                provider_health={"openai": "cooldown", "gemini": "healthy"},
                provider_chain=["gemini", "openai"],
            )
        if method == "GET" and url.endswith("/api/v1/projects"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": [
                    {
                        "id": "p1",
                        "name": "真实项目A",
                        "status": "submitted_to_qingtian",
                        "updated_at": recent_iso,
                    }
                ],
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects/p1/evolution/health"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "summary": {
                        "ground_truth_count": 3,
                        "eligible_learning_ground_truth_count": 3,
                        "matched_prediction_count": 3,
                        "guardrail_blocked_count": 0,
                        "has_evolved_multipliers": True,
                        "evolution_weights_usable": True,
                    },
                    "drift": {"level": "low"},
                },
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects/p1/feedback/governance"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "summary": {
                        "manual_confirmation_required": False,
                        "few_shot_pending_review_count": 0,
                    },
                    "score_preview": {
                        "current_calibrator_version": "calib_auto_existing",
                    },
                    "version_history": [],
                },
                "error": None,
            }
        raise AssertionError(f"unexpected request: {method} {url}")

    result = oa._run_learning_calibration_agent(
        base_url="http://127.0.0.1:8000",
        api_key=None,
        timeout=5.0,
        auto_evolve=True,
        min_samples=1,
        requester=fake_requester,
    )

    assert result["status"] == "warn"
    assert result["metrics"]["llm_provider_degraded_count"] == 1
    assert result["metrics"]["llm_fallback_unavailable_count"] == 0
    assert any("服务提供方当前处于冷却期" in row for row in result["recommendations"])


def test_learning_calibration_agent_warns_when_llm_account_pool_is_thin():
    recent_iso = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    def fake_requester(**kwargs):
        method = str(kwargs.get("method") or "")
        url = str(kwargs.get("url") or "")
        if method == "GET" and url.endswith("/api/v1/config/llm_status"):
            return _ok_llm_status_response(
                openai_pool_health={
                    "total_accounts": 4,
                    "healthy_accounts": 1,
                    "cooling_accounts": 3,
                },
                gemini_pool_health={
                    "total_accounts": 2,
                    "healthy_accounts": 2,
                    "cooling_accounts": 0,
                },
            )
        if method == "GET" and url.endswith("/api/v1/projects"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": [
                    {
                        "id": "p1",
                        "name": "真实项目A",
                        "status": "submitted_to_qingtian",
                        "updated_at": recent_iso,
                    }
                ],
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects/p1/evolution/health"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "summary": {
                        "ground_truth_count": 3,
                        "eligible_learning_ground_truth_count": 3,
                        "matched_prediction_count": 3,
                        "guardrail_blocked_count": 0,
                        "has_evolved_multipliers": True,
                        "evolution_weights_usable": True,
                    },
                    "drift": {"level": "low"},
                },
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects/p1/feedback/governance"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "summary": {
                        "manual_confirmation_required": False,
                        "few_shot_pending_review_count": 0,
                    },
                    "score_preview": {
                        "current_calibrator_version": "calib_auto_existing",
                    },
                    "version_history": [],
                },
                "error": None,
            }
        raise AssertionError(f"unexpected request: {method} {url}")

    result = oa._run_learning_calibration_agent(
        base_url="http://127.0.0.1:8000",
        api_key=None,
        timeout=5.0,
        auto_evolve=True,
        min_samples=1,
        requester=fake_requester,
    )

    assert result["status"] == "warn"
    assert result["metrics"]["llm_account_cooldown_count"] == 3
    assert result["metrics"]["llm_provider_thin_pool_count"] == 1
    assert any("当前共有 3 个 LLM 账号处于冷却期" in row for row in result["recommendations"])
    assert any("仅剩 1 个健康账号" in row for row in result["recommendations"])


def test_learning_calibration_agent_warns_when_llm_provider_quality_is_degraded():
    recent_iso = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    def fake_requester(**kwargs):
        method = str(kwargs.get("method") or "")
        url = str(kwargs.get("url") or "")
        if method == "GET" and url.endswith("/api/v1/config/llm_status"):
            return _ok_llm_status_response(
                provider_quality={"openai": "degraded", "gemini": "stable"},
                provider_chain=["gemini", "openai"],
            )
        if method == "GET" and url.endswith("/api/v1/projects"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": [
                    {
                        "id": "p1",
                        "name": "真实项目A",
                        "status": "submitted_to_qingtian",
                        "updated_at": recent_iso,
                    }
                ],
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects/p1/evolution/health"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "summary": {
                        "ground_truth_count": 3,
                        "eligible_learning_ground_truth_count": 3,
                        "matched_prediction_count": 3,
                        "guardrail_blocked_count": 0,
                        "has_evolved_multipliers": True,
                        "evolution_weights_usable": True,
                    },
                    "drift": {"level": "low"},
                },
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects/p1/feedback/governance"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "summary": {
                        "manual_confirmation_required": False,
                        "few_shot_pending_review_count": 0,
                    },
                    "score_preview": {
                        "current_calibrator_version": "calib_auto_existing",
                    },
                    "version_history": [],
                },
                "error": None,
            }
        raise AssertionError(f"unexpected request: {method} {url}")

    result = oa._run_learning_calibration_agent(
        base_url="http://127.0.0.1:8000",
        api_key=None,
        timeout=5.0,
        auto_evolve=True,
        min_samples=1,
        requester=fake_requester,
    )

    assert result["status"] == "warn"
    assert result["metrics"]["llm_provider_quality_degraded_count"] == 1
    assert any("最近复核分歧偏高" in row for row in result["recommendations"])


def test_learning_calibration_agent_warns_when_llm_provider_review_regresses():
    recent_iso = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    def fake_requester(**kwargs):
        method = str(kwargs.get("method") or "")
        url = str(kwargs.get("url") or "")
        if method == "GET" and url.endswith("/api/v1/config/llm_status"):
            return _ok_llm_status_response(
                provider_review_stats={
                    "openai": {
                        "confirmed_count": 1,
                        "diverged_count": 3,
                        "unavailable_count": 0,
                        "fallback_only_count": 0,
                        "last_status": "diverged",
                        "last_at": 1.0,
                    },
                    "gemini": {
                        "confirmed_count": 4,
                        "diverged_count": 0,
                        "unavailable_count": 0,
                        "fallback_only_count": 0,
                        "last_status": "confirmed",
                        "last_at": 1.0,
                    },
                }
            )
        if method == "GET" and url.endswith("/api/v1/projects"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": [
                    {
                        "id": "p1",
                        "name": "真实项目A",
                        "status": "submitted_to_qingtian",
                        "updated_at": recent_iso,
                    }
                ],
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects/p1/evolution/health"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "summary": {
                        "ground_truth_count": 3,
                        "eligible_learning_ground_truth_count": 3,
                        "matched_prediction_count": 3,
                        "guardrail_blocked_count": 0,
                        "has_evolved_multipliers": True,
                        "evolution_weights_usable": True,
                    },
                    "drift": {"level": "low"},
                },
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects/p1/feedback/governance"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "summary": {
                        "manual_confirmation_required": False,
                        "few_shot_pending_review_count": 0,
                    },
                    "score_preview": {"current_calibrator_version": "calib_auto_existing"},
                    "version_history": [],
                },
                "error": None,
            }
        raise AssertionError(f"unexpected request: {method} {url}")

    result = oa._run_learning_calibration_agent(
        base_url="http://127.0.0.1:8000",
        api_key=None,
        timeout=5.0,
        auto_evolve=True,
        min_samples=1,
        requester=fake_requester,
    )

    assert result["status"] == "warn"
    assert result["metrics"]["llm_provider_review_regression_count"] == 1
    assert any("累计复核分歧已超过确认次数" in row for row in result["recommendations"])


def test_learning_calibration_agent_warns_when_llm_provider_quality_score_is_low():
    recent_iso = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    def fake_requester(**kwargs):
        method = str(kwargs.get("method") or "")
        url = str(kwargs.get("url") or "")
        if method == "GET" and url.endswith("/api/v1/config/llm_status"):
            return _ok_llm_status_response(
                provider_quality_score={"openai": 22.0, "gemini": 78.0},
                provider_review_stats={
                    "openai": {
                        "confirmed_count": 1,
                        "diverged_count": 2,
                        "unavailable_count": 0,
                        "fallback_only_count": 0,
                        "last_status": "diverged",
                        "last_at": 1.0,
                    },
                    "gemini": {
                        "confirmed_count": 3,
                        "diverged_count": 0,
                        "unavailable_count": 0,
                        "fallback_only_count": 0,
                        "last_status": "confirmed",
                        "last_at": 1.0,
                    },
                },
            )
        if method == "GET" and url.endswith("/api/v1/projects"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": [
                    {
                        "id": "p1",
                        "name": "真实项目A",
                        "status": "submitted_to_qingtian",
                        "updated_at": recent_iso,
                    }
                ],
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects/p1/evolution/health"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "summary": {
                        "ground_truth_count": 3,
                        "eligible_learning_ground_truth_count": 3,
                        "matched_prediction_count": 3,
                        "guardrail_blocked_count": 0,
                        "has_evolved_multipliers": True,
                        "evolution_weights_usable": True,
                    },
                    "drift": {"level": "low"},
                },
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects/p1/feedback/governance"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "summary": {
                        "manual_confirmation_required": False,
                        "few_shot_pending_review_count": 0,
                    },
                    "score_preview": {"current_calibrator_version": "calib_auto_existing"},
                    "version_history": [],
                },
                "error": None,
            }
        raise AssertionError(f"unexpected request: {method} {url}")

    result = oa._run_learning_calibration_agent(
        base_url="http://127.0.0.1:8000",
        api_key=None,
        timeout=5.0,
        auto_evolve=True,
        min_samples=1,
        requester=fake_requester,
    )

    assert result["status"] == "warn"
    assert result["metrics"]["llm_provider_low_quality_score_count"] == 1
    assert any("历史质量分偏低" in row for row in result["recommendations"])


def test_learning_calibration_agent_warns_when_llm_account_pool_quality_is_low():
    recent_iso = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    def fake_requester(**kwargs):
        method = str(kwargs.get("method") or "")
        url = str(kwargs.get("url") or "")
        if method == "GET" and url.endswith("/api/v1/config/llm_status"):
            return _ok_llm_status_response(
                openai_pool_quality={
                    "total_accounts": 4.0,
                    "rated_accounts": 3.0,
                    "sufficiently_rated_accounts": 3.0,
                    "average_quality_score": 32.0,
                    "best_quality_score": 54.0,
                    "worst_quality_score": 18.0,
                }
            )
        if method == "GET" and url.endswith("/api/v1/projects"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": [
                    {
                        "id": "p1",
                        "name": "真实项目A",
                        "status": "submitted_to_qingtian",
                        "updated_at": recent_iso,
                    }
                ],
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects/p1/evolution/health"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "summary": {
                        "ground_truth_count": 3,
                        "eligible_learning_ground_truth_count": 3,
                        "matched_prediction_count": 3,
                        "guardrail_blocked_count": 0,
                        "has_evolved_multipliers": True,
                        "evolution_weights_usable": True,
                    },
                    "drift": {"level": "low"},
                },
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects/p1/feedback/governance"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "summary": {
                        "manual_confirmation_required": False,
                        "few_shot_pending_review_count": 0,
                    },
                    "score_preview": {"current_calibrator_version": "calib_auto_existing"},
                    "version_history": [],
                },
                "error": None,
            }
        raise AssertionError(f"unexpected request: {method} {url}")

    result = oa._run_learning_calibration_agent(
        base_url="http://127.0.0.1:8000",
        api_key=None,
        timeout=5.0,
        auto_evolve=True,
        min_samples=1,
        requester=fake_requester,
    )

    assert result["status"] == "warn"
    assert result["metrics"]["llm_account_low_quality_pool_count"] == 1
    assert any("账号池历史质量分偏低" in row for row in result["recommendations"])


def test_learning_calibration_agent_does_not_warn_on_low_sample_pool_scores():
    recent_iso = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    def fake_requester(**kwargs):
        method = str(kwargs.get("method") or "")
        url = str(kwargs.get("url") or "")
        if method == "GET" and url.endswith("/api/v1/config/llm_status"):
            return _ok_llm_status_response(
                openai_pool_quality={
                    "total_accounts": 4.0,
                    "rated_accounts": 3.0,
                    "sufficiently_rated_accounts": 0.0,
                    "average_quality_score": 30.0,
                    "best_quality_score": 30.0,
                    "worst_quality_score": 30.0,
                    "low_quality_accounts": 0.0,
                }
            )
        if method == "GET" and url.endswith("/api/v1/projects"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": [
                    {
                        "id": "p1",
                        "name": "真实项目A",
                        "status": "submitted_to_qingtian",
                        "updated_at": recent_iso,
                    }
                ],
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects/p1/evolution/health"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "summary": {
                        "ground_truth_count": 3,
                        "eligible_learning_ground_truth_count": 3,
                        "matched_prediction_count": 3,
                        "guardrail_blocked_count": 0,
                        "has_evolved_multipliers": True,
                        "evolution_weights_usable": True,
                    },
                    "drift": {"level": "low"},
                },
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects/p1/feedback/governance"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "summary": {
                        "manual_confirmation_required": False,
                        "few_shot_pending_review_count": 0,
                    },
                    "score_preview": {"current_calibrator_version": "calib_auto_existing"},
                    "version_history": [],
                },
                "error": None,
            }
        raise AssertionError(f"unexpected request: {method} {url}")

    result = oa._run_learning_calibration_agent(
        base_url="http://127.0.0.1:8000",
        api_key=None,
        timeout=5.0,
        auto_evolve=True,
        min_samples=1,
        requester=fake_requester,
    )

    assert result["status"] == "pass"
    assert result["metrics"]["llm_account_low_quality_pool_count"] == 0


def test_learning_calibration_agent_warns_when_llm_accounts_are_deprioritized():
    recent_iso = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    def fake_requester(**kwargs):
        method = str(kwargs.get("method") or "")
        url = str(kwargs.get("url") or "")
        if method == "GET" and url.endswith("/api/v1/config/llm_status"):
            return _ok_llm_status_response(
                openai_pool_quality={
                    "total_accounts": 4.0,
                    "rated_accounts": 3.0,
                    "sufficiently_rated_accounts": 3.0,
                    "average_quality_score": 61.0,
                    "best_quality_score": 92.0,
                    "worst_quality_score": 21.0,
                    "low_quality_accounts": 2.0,
                }
            )
        if method == "GET" and url.endswith("/api/v1/projects"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": [
                    {
                        "id": "p1",
                        "name": "真实项目A",
                        "status": "submitted_to_qingtian",
                        "updated_at": recent_iso,
                    }
                ],
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects/p1/evolution/health"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "summary": {
                        "ground_truth_count": 3,
                        "eligible_learning_ground_truth_count": 3,
                        "matched_prediction_count": 3,
                        "guardrail_blocked_count": 0,
                        "has_evolved_multipliers": True,
                        "evolution_weights_usable": True,
                    },
                    "drift": {"level": "low"},
                },
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects/p1/feedback/governance"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "summary": {
                        "manual_confirmation_required": False,
                        "few_shot_pending_review_count": 0,
                    },
                    "score_preview": {"current_calibrator_version": "calib_auto_existing"},
                    "version_history": [],
                },
                "error": None,
            }
        raise AssertionError(f"unexpected request: {method} {url}")

    result = oa._run_learning_calibration_agent(
        base_url="http://127.0.0.1:8000",
        api_key=None,
        timeout=5.0,
        auto_evolve=True,
        min_samples=1,
        requester=fake_requester,
    )

    assert result["status"] == "warn"
    assert result["metrics"]["llm_deprioritized_account_count"] == 2
    assert any("已被系统自动降优先级" in row for row in result["recommendations"])


def test_ensure_agent_coverage_backfills_missing_agents():
    agents = {
        "sre_watchdog": {
            "name": "sre_watchdog",
            "status": "pass",
            "duration_ms": 1,
            "checks": {},
            "actions": {},
            "metrics": {},
            "recommendations": [],
        }
    }

    missing = oa._ensure_agent_coverage(agents, reason="coverage gap")

    assert "sre_watchdog" not in missing
    assert set(missing) == set(oa.OPS_AGENT_NAMES[1:])
    for name in oa.OPS_AGENT_NAMES[1:]:
        assert agents[name]["status"] == "fail"
        assert agents[name]["recommendations"] == ["coverage gap"]


def test_ops_agents_snapshot_is_stale_uses_interval_window():
    now = datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc)
    fresh = (now - timedelta(seconds=100)).isoformat()
    stale = (now - timedelta(seconds=181)).isoformat()

    assert (
        oa.ops_agents_snapshot_is_stale(
            fresh,
            now=now,
            interval_seconds=90,
            grace_seconds=30,
        )
        is False
    )
    assert (
        oa.ops_agents_snapshot_is_stale(
            stale,
            now=now,
            interval_seconds=90,
            grace_seconds=30,
        )
        is True
    )


def test_scoring_quality_treats_preparation_critical_as_non_failure():
    recent_iso = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    def fake_requester(**kwargs):
        url = str(kwargs.get("url") or "")
        if url.endswith("/api/v1/projects"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": [
                    {
                        "id": "p1",
                        "name": "项目1",
                        "status": "scoring_preparation",
                        "updated_at": recent_iso,
                    }
                ],
                "error": None,
            }
        if url.endswith("/api/v1/projects/p1/mece_audit"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "overall": {"level": "critical"},
                    "summary": {"submission_total": 0, "submission_scored": 0},
                },
                "error": None,
            }
        raise AssertionError(f"unexpected url: {url}")

    result = oa._run_scoring_quality_agent(
        base_url="http://127.0.0.1:8000",
        api_key=None,
        timeout=5.0,
        requester=fake_requester,
    )
    assert result["status"] == "pass"
    assert result["metrics"]["critical_count"] == 0
    assert result["metrics"]["preparation_critical_count"] == 1


def test_scoring_quality_treats_preparation_pending_submission_as_non_failure():
    recent_iso = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    def fake_requester(**kwargs):
        url = str(kwargs.get("url") or "")
        if url.endswith("/api/v1/projects"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": [
                    {
                        "id": "p1",
                        "name": "项目1",
                        "status": "scoring_preparation",
                        "updated_at": recent_iso,
                    }
                ],
                "error": None,
            }
        if url.endswith("/api/v1/projects/p1/mece_audit"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "overall": {"level": "critical"},
                    "summary": {"submission_total": 1, "submission_scored": 0},
                },
                "error": None,
            }
        raise AssertionError(f"unexpected url: {url}")

    result = oa._run_scoring_quality_agent(
        base_url="http://127.0.0.1:8000",
        api_key=None,
        timeout=5.0,
        requester=fake_requester,
    )
    assert result["status"] == "pass"
    assert result["metrics"]["critical_count"] == 0
    assert result["metrics"]["preparation_critical_count"] == 1


def test_scoring_quality_ignores_evolution_only_watch_for_scored_project():
    recent_iso = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    def fake_requester(**kwargs):
        url = str(kwargs.get("url") or "")
        if url.endswith("/api/v1/projects"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": [
                    {
                        "id": "p1",
                        "name": "项目1",
                        "status": "scoring_preparation",
                        "updated_at": recent_iso,
                    }
                ],
                "error": None,
            }
        if url.endswith("/api/v1/projects/p1/mece_audit"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "overall": {"level": "watch"},
                    "dimensions": [
                        {"key": "input_chain", "status": "pass"},
                        {"key": "scoring_validity", "status": "pass"},
                        {"key": "self_evolution_loop", "status": "fail"},
                        {"key": "runtime_stability", "status": "pass"},
                    ],
                    "summary": {"submission_total": 4, "submission_scored": 1},
                },
                "error": None,
            }
        raise AssertionError(f"unexpected url: {url}")

    result = oa._run_scoring_quality_agent(
        base_url="http://127.0.0.1:8000",
        api_key=None,
        timeout=5.0,
        requester=fake_requester,
    )
    assert result["status"] == "pass"
    assert result["metrics"]["watch_count"] == 0
    assert result["metrics"]["critical_count"] == 0
    assert result["metrics"]["ignored_non_scoring_issue_count"] == 1


def test_select_projects_for_ops_audit_excludes_synthetic_and_stale_preparation():
    now = datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc)
    projects = [
        {
            "id": "synthetic",
            "name": "OPS_SMOKE_1",
            "status": "submitted_to_qingtian",
            "updated_at": (now - timedelta(hours=1)).isoformat(),
        },
        {
            "id": "recent-real",
            "name": "真实项目A",
            "status": "scoring_preparation",
            "updated_at": (now - timedelta(hours=2)).isoformat(),
        },
        {
            "id": "stale-prep",
            "name": "真实项目C",
            "status": "scoring_preparation",
            "updated_at": (now - timedelta(days=10)).isoformat(),
        },
    ]

    selected = oa._select_projects_for_ops_audit(
        projects, now=now, recent_hours=72, max_projects=10
    )
    assert [row["id"] for row in selected] == ["recent-real"]


def test_request_json_omits_api_key_for_localhost(monkeypatch):
    captured_headers = {}

    class _FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b"{}"

    def fake_urlopen(req, timeout):
        captured_headers["headers"] = dict(req.header_items())
        return _FakeResponse()

    monkeypatch.setattr(oa.request, "urlopen", fake_urlopen)

    oa._request_json(
        method="GET",
        url="http://127.0.0.1:8000/health",
        api_key="secret",
        timeout=1.0,
    )
    assert "X-api-key" not in captured_headers["headers"]

    oa._request_json(
        method="GET",
        url="http://example.com/health",
        api_key="secret",
        timeout=1.0,
    )
    assert captured_headers["headers"]["X-api-key"] == "secret"


def test_scoring_quality_ignores_synthetic_and_stale_history():
    now = datetime.now(timezone.utc)
    recent_iso = (now - timedelta(hours=1)).isoformat()
    stale_iso = (now - timedelta(days=10)).isoformat()

    def fake_requester(**kwargs):
        url = str(kwargs.get("url") or "")
        if url.endswith("/api/v1/projects"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": [
                    {
                        "id": "synthetic",
                        "name": "E2E_1",
                        "status": "submitted_to_qingtian",
                        "updated_at": recent_iso,
                    },
                    {
                        "id": "stale",
                        "name": "历史项目",
                        "status": "submitted_to_qingtian",
                        "updated_at": stale_iso,
                    },
                    {
                        "id": "recent-real",
                        "name": "真实项目A",
                        "status": "scoring_preparation",
                        "updated_at": recent_iso,
                    },
                ],
                "error": None,
            }
        if url.endswith("/api/v1/projects/recent-real/mece_audit"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "overall": {"level": "critical"},
                    "summary": {"submission_total": 0, "submission_scored": 0},
                },
                "error": None,
            }
        raise AssertionError(f"unexpected url: {url}")

    result = oa._run_scoring_quality_agent(
        base_url="http://127.0.0.1:8000",
        api_key=None,
        timeout=5.0,
        requester=fake_requester,
    )
    assert result["status"] == "pass"
    assert result["metrics"]["project_count"] == 3
    assert result["metrics"]["monitored_project_count"] == 1
    assert result["metrics"]["preparation_critical_count"] == 1


def test_evolution_treats_preparation_without_ground_truth_as_pass():
    recent_iso = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    def fake_requester(**kwargs):
        url = str(kwargs.get("url") or "")
        if url.endswith("/api/v1/projects"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": [
                    {
                        "id": "recent-real",
                        "name": "真实项目A",
                        "status": "scoring_preparation",
                        "updated_at": recent_iso,
                    }
                ],
                "error": None,
            }
        if url.endswith("/api/v1/projects/recent-real/evolution/health"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {
                    "summary": {
                        "ground_truth_count": 0,
                        "has_evolved_multipliers": False,
                    }
                },
                "error": None,
            }
        raise AssertionError(f"unexpected url: {url}")

    result = oa._run_evolution_agent(
        base_url="http://127.0.0.1:8000",
        api_key=None,
        timeout=5.0,
        auto_evolve=True,
        min_samples=3,
        requester=fake_requester,
    )
    assert result["status"] == "pass"
    assert result["metrics"]["monitored_project_count"] == 1
    assert result["metrics"]["preparation_insufficient_count"] == 1
    assert result["metrics"]["started_but_insufficient_count"] == 0


def test_project_flow_agent_smoke_success():
    state = {"created": False, "deleted": False}

    deleted_ids: list[str] = []

    def fake_requester(**kwargs):
        method = str(kwargs.get("method") or "")
        url = str(kwargs.get("url") or "")
        payload = kwargs.get("payload") or {}
        if method == "GET" and url.endswith("/api/v1/projects"):
            rows = [{"id": "p1", "name": "项目1"}]
            if state["created"] and not state["deleted"]:
                rows.append({"id": "ops-p1", "name": "OPS_SMOKE_TEST"})
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": rows,
                "error": None,
            }
        if method == "POST" and url.endswith("/api/v1/projects"):
            state["created"] = True
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {"id": "ops-p1", "name": payload.get("name")},
                "error": None,
            }
        if method == "DELETE" and url.endswith("/api/v1/projects/ops-p1"):
            deleted_ids.append("ops-p1")
            state["deleted"] = True
            return {
                "ok": True,
                "status_code": 204,
                "elapsed_ms": 1,
                "json": {},
                "error": None,
            }
        raise AssertionError(f"unexpected request: {method} {url}")

    result = oa._run_project_flow_agent(
        base_url="http://127.0.0.1:8000",
        api_key=None,
        timeout=5.0,
        requester=fake_requester,
    )
    assert result["status"] == "pass"
    assert result["metrics"]["created_ok"] == 1
    assert result["metrics"]["listed_after_create"] == 1
    assert result["metrics"]["delete_ok"] == 1
    assert result["metrics"]["projects_after_delete_ok"] == 1
    assert result["metrics"]["removed_after_delete"] == 1


def test_project_flow_agent_fails_when_projects_unreachable_after_delete():
    state = {"created": False, "deleted": False}

    def fake_requester(**kwargs):
        method = str(kwargs.get("method") or "")
        url = str(kwargs.get("url") or "")
        payload = kwargs.get("payload") or {}
        if method == "GET" and url.endswith("/api/v1/projects"):
            if state["deleted"]:
                return {
                    "ok": False,
                    "status_code": 0,
                    "elapsed_ms": 0,
                    "json": {},
                    "error": "URLError: <urlopen error [Errno 61] Connection refused>",
                }
            rows = [{"id": "p1", "name": "项目1"}]
            if state["created"]:
                rows.append({"id": "ops-p1", "name": "OPS_SMOKE_TEST"})
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": rows,
                "error": None,
            }
        if method == "POST" and url.endswith("/api/v1/projects"):
            state["created"] = True
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {"id": "ops-p1", "name": payload.get("name")},
                "error": None,
            }
        if method == "DELETE" and url.endswith("/api/v1/projects/ops-p1"):
            state["deleted"] = True
            return {
                "ok": True,
                "status_code": 204,
                "elapsed_ms": 1,
                "json": {},
                "error": None,
            }
        raise AssertionError(f"unexpected request: {method} {url}")

    result = oa._run_project_flow_agent(
        base_url="http://127.0.0.1:8000",
        api_key=None,
        timeout=5.0,
        requester=fake_requester,
    )
    assert result["status"] == "fail"
    assert result["metrics"]["delete_ok"] == 1
    assert result["metrics"]["projects_after_delete_ok"] == 0
    assert result["metrics"]["removed_after_delete"] == 0
    assert "项目列表不可达" in result["recommendations"][0]


def test_request_with_local_read_retry_recovers_connection_refused_once():
    calls = {"count": 0}

    def fake_requester(**kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return {
                "ok": False,
                "status_code": 0,
                "elapsed_ms": 0,
                "json": {},
                "error": "URLError: <urlopen error [Errno 61] Connection refused>",
            }
        return {
            "ok": True,
            "status_code": 200,
            "elapsed_ms": 1,
            "json": [{"id": "p1"}],
            "error": None,
        }

    original_sleep = oa.time.sleep
    oa.time.sleep = lambda _seconds: None
    try:
        result = oa._request_with_local_read_retry(
            requester=fake_requester,
            method="GET",
            url="http://127.0.0.1:8000/api/v1/projects",
            timeout=5.0,
        )
    finally:
        oa.time.sleep = original_sleep
    assert calls["count"] == 2
    assert result["status_code"] == 200


def test_request_with_local_read_retry_does_not_retry_non_local_post():
    calls = {"count": 0}

    def fake_requester(**kwargs):
        calls["count"] += 1
        return {
            "ok": False,
            "status_code": 0,
            "elapsed_ms": 0,
            "json": {},
            "error": "URLError: <urlopen error [Errno 61] Connection refused>",
        }

    result = oa._request_with_local_read_retry(
        requester=fake_requester,
        method="POST",
        url="https://example.com/api/v1/projects",
        timeout=5.0,
    )
    assert calls["count"] == 1
    assert result["status_code"] == 0


def test_tender_project_flow_agent_smoke_success():
    state = {"created": False, "deleted": False}

    def fake_requester(**kwargs):
        method = str(kwargs.get("method") or "")
        url = str(kwargs.get("url") or "")
        files = kwargs.get("files") or []
        if method == "POST" and url.endswith("/api/v1/projects/create_from_tender"):
            assert files and files[0]["filename"] == "ops_tender_smoke.txt"
            assert "项目名称：OPS招标项目1工程" in str(files[0]["content"])
            state["created"] = True
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1200,
                "json": {
                    "project": {"id": "ops-tender-p1", "name": "OPS招标项目1工程"},
                    "inferred_name": "OPS招标项目1工程",
                },
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects"):
            rows = [{"id": "p1", "name": "项目1"}]
            if state["created"] and not state["deleted"]:
                rows.append({"id": "ops-tender-p1", "name": "OPS招标项目1工程"})
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": rows,
                "error": None,
            }
        if method == "DELETE" and url.endswith("/api/v1/projects/ops-tender-p1"):
            state["deleted"] = True
            return {
                "ok": True,
                "status_code": 204,
                "elapsed_ms": 1,
                "json": {},
                "error": None,
            }
        raise AssertionError(f"unexpected request: {method} {url}")

    original_time = oa.time.time
    oa.time.time = lambda: 0.001
    try:
        result = oa._run_tender_project_flow_agent(
            base_url="http://127.0.0.1:8000",
            api_key=None,
            timeout=5.0,
            requester=fake_requester,
        )
    finally:
        oa.time.time = original_time
    assert result["status"] == "pass"
    assert result["metrics"]["created_ok"] == 1
    assert result["metrics"]["inferred_ok"] == 1
    assert result["metrics"]["elapsed_ok"] == 1
    assert result["metrics"]["listed_after_create"] == 1
    assert result["metrics"]["delete_ok"] == 1
    assert result["metrics"]["projects_after_delete_ok"] == 1
    assert result["metrics"]["removed_after_delete"] == 1


def test_tender_project_flow_agent_fails_when_projects_unreachable_after_delete():
    state = {"created": False, "deleted": False}

    def fake_requester(**kwargs):
        method = str(kwargs.get("method") or "")
        url = str(kwargs.get("url") or "")
        files = kwargs.get("files") or []
        if method == "POST" and url.endswith("/api/v1/projects/create_from_tender"):
            assert files and files[0]["filename"] == "ops_tender_smoke.txt"
            state["created"] = True
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1200,
                "json": {
                    "project": {"id": "ops-tender-p1", "name": "OPS招标项目1工程"},
                    "inferred_name": "OPS招标项目1工程",
                },
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects"):
            if state["deleted"]:
                return {
                    "ok": False,
                    "status_code": 0,
                    "elapsed_ms": 0,
                    "json": {},
                    "error": "URLError: <urlopen error [Errno 61] Connection refused>",
                }
            rows = [{"id": "p1", "name": "项目1"}]
            if state["created"]:
                rows.append({"id": "ops-tender-p1", "name": "OPS招标项目1工程"})
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": rows,
                "error": None,
            }
        if method == "DELETE" and url.endswith("/api/v1/projects/ops-tender-p1"):
            state["deleted"] = True
            return {
                "ok": True,
                "status_code": 204,
                "elapsed_ms": 1,
                "json": {},
                "error": None,
            }
        raise AssertionError(f"unexpected request: {method} {url}")

    original_time = oa.time.time
    oa.time.time = lambda: 0.001
    try:
        result = oa._run_tender_project_flow_agent(
            base_url="http://127.0.0.1:8000",
            api_key=None,
            timeout=5.0,
            requester=fake_requester,
        )
    finally:
        oa.time.time = original_time
    assert result["status"] == "fail"
    assert result["metrics"]["delete_ok"] == 1
    assert result["metrics"]["projects_after_delete_ok"] == 0
    assert result["metrics"]["removed_after_delete"] == 0
    assert "项目列表不可达" in result["recommendations"][0]


def test_upload_flow_agent_smoke_success():
    state = {"created": False, "deleted": False, "material": False, "submission": False}

    def fake_requester(**kwargs):
        method = str(kwargs.get("method") or "")
        url = str(kwargs.get("url") or "")
        if method == "POST" and url.endswith("/api/v1/projects"):
            state["created"] = True
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {"id": "ops-upload-p1", "name": "OPS上传项目_1"},
                "error": None,
            }
        if method == "POST" and url.endswith("/api/v1/projects/ops-upload-p1/materials"):
            state["material"] = True
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {"material": {"id": "m1", "filename": "ops_material.txt"}},
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects/ops-upload-p1/materials"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": [{"id": "m1", "filename": "ops_material.txt"}] if state["material"] else [],
                "error": None,
            }
        if method == "POST" and url.endswith("/api/v1/projects/ops-upload-p1/shigong"):
            state["submission"] = True
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {"id": "s1", "filename": "ops_shigong.txt"},
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects/ops-upload-p1/submissions"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": [{"id": "s1", "filename": "ops_shigong.txt"}]
                if state["submission"]
                else [],
                "error": None,
            }
        if method == "DELETE" and url.endswith("/api/v1/projects/ops-upload-p1"):
            state["deleted"] = True
            return {
                "ok": True,
                "status_code": 204,
                "elapsed_ms": 1,
                "json": {},
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects"):
            rows = [{"id": "p1", "name": "项目1"}]
            if state["created"] and not state["deleted"]:
                rows.append({"id": "ops-upload-p1", "name": "OPS上传项目_1"})
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": rows,
                "error": None,
            }
        raise AssertionError(f"unexpected request: {method} {url}")

    original_time = oa.time.time
    oa.time.time = lambda: 0.001
    try:
        result = oa._run_upload_flow_agent(
            base_url="http://127.0.0.1:8000",
            api_key=None,
            timeout=5.0,
            requester=fake_requester,
        )
    finally:
        oa.time.time = original_time
    assert result["status"] == "pass"
    assert result["metrics"]["created_ok"] == 1
    assert result["metrics"]["material_upload_ok"] == 1
    assert result["metrics"]["material_listed"] == 1
    assert result["metrics"]["shigong_upload_ok"] == 1
    assert result["metrics"]["submission_listed"] == 1
    assert result["metrics"]["delete_ok"] == 1
    assert result["metrics"]["projects_after_delete_ok"] == 1
    assert result["metrics"]["removed_after_delete"] == 1


def test_upload_flow_agent_fails_when_projects_unreachable_after_delete():
    state = {"created": False, "deleted": False, "material": False, "submission": False}

    def fake_requester(**kwargs):
        method = str(kwargs.get("method") or "")
        url = str(kwargs.get("url") or "")
        if method == "POST" and url.endswith("/api/v1/projects"):
            state["created"] = True
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {"id": "ops-upload-p1", "name": "OPS上传项目_1"},
                "error": None,
            }
        if method == "POST" and url.endswith("/api/v1/projects/ops-upload-p1/materials"):
            state["material"] = True
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {"material": {"id": "m1", "filename": "ops_material.txt"}},
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects/ops-upload-p1/materials"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": [{"id": "m1", "filename": "ops_material.txt"}] if state["material"] else [],
                "error": None,
            }
        if method == "POST" and url.endswith("/api/v1/projects/ops-upload-p1/shigong"):
            state["submission"] = True
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {"id": "s1", "filename": "ops_shigong.txt"},
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects/ops-upload-p1/submissions"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": [{"id": "s1", "filename": "ops_shigong.txt"}]
                if state["submission"]
                else [],
                "error": None,
            }
        if method == "DELETE" and url.endswith("/api/v1/projects/ops-upload-p1"):
            state["deleted"] = True
            return {
                "ok": True,
                "status_code": 204,
                "elapsed_ms": 1,
                "json": {},
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects"):
            if state["deleted"]:
                return {
                    "ok": False,
                    "status_code": 0,
                    "elapsed_ms": 0,
                    "json": {},
                    "error": "URLError: <urlopen error [Errno 61] Connection refused>",
                }
            rows = [{"id": "p1", "name": "项目1"}]
            if state["created"]:
                rows.append({"id": "ops-upload-p1", "name": "OPS上传项目_1"})
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": rows,
                "error": None,
            }
        raise AssertionError(f"unexpected request: {method} {url}")

    original_time = oa.time.time
    oa.time.time = lambda: 0.001
    try:
        result = oa._run_upload_flow_agent(
            base_url="http://127.0.0.1:8000",
            api_key=None,
            timeout=5.0,
            requester=fake_requester,
        )
    finally:
        oa.time.time = original_time
    assert result["status"] == "fail"
    assert result["metrics"]["delete_ok"] == 1
    assert result["metrics"]["projects_after_delete_ok"] == 0
    assert result["metrics"]["removed_after_delete"] == 0
    assert "项目列表不可达" in result["recommendations"][0]


def test_upload_flow_agent_recovers_when_projects_after_delete_retries_once():
    state = {
        "created": False,
        "deleted": False,
        "material": False,
        "submission": False,
        "post_delete_list_attempts": 0,
    }

    def fake_requester(**kwargs):
        method = str(kwargs.get("method") or "")
        url = str(kwargs.get("url") or "")
        if method == "POST" and url.endswith("/api/v1/projects"):
            state["created"] = True
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {"id": "ops-upload-p1", "name": "OPS上传项目_1"},
                "error": None,
            }
        if method == "POST" and url.endswith("/api/v1/projects/ops-upload-p1/materials"):
            state["material"] = True
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {"material": {"id": "m1", "filename": "ops_material.txt"}},
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects/ops-upload-p1/materials"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": [{"id": "m1", "filename": "ops_material.txt"}] if state["material"] else [],
                "error": None,
            }
        if method == "POST" and url.endswith("/api/v1/projects/ops-upload-p1/shigong"):
            state["submission"] = True
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": {"id": "s1", "filename": "ops_shigong.txt"},
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects/ops-upload-p1/submissions"):
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": [{"id": "s1", "filename": "ops_shigong.txt"}]
                if state["submission"]
                else [],
                "error": None,
            }
        if method == "DELETE" and url.endswith("/api/v1/projects/ops-upload-p1"):
            state["deleted"] = True
            return {
                "ok": True,
                "status_code": 204,
                "elapsed_ms": 1,
                "json": {},
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects"):
            if state["deleted"]:
                state["post_delete_list_attempts"] += 1
                if state["post_delete_list_attempts"] == 1:
                    return {
                        "ok": False,
                        "status_code": 0,
                        "elapsed_ms": 0,
                        "json": {},
                        "error": "URLError: <urlopen error [Errno 61] Connection refused>",
                    }
                return {
                    "ok": True,
                    "status_code": 200,
                    "elapsed_ms": 1,
                    "json": [{"id": "p1", "name": "项目1"}],
                    "error": None,
                }
            rows = [{"id": "p1", "name": "项目1"}]
            if state["created"]:
                rows.append({"id": "ops-upload-p1", "name": "OPS上传项目_1"})
            return {
                "ok": True,
                "status_code": 200,
                "elapsed_ms": 1,
                "json": rows,
                "error": None,
            }
        raise AssertionError(f"unexpected request: {method} {url}")

    original_time = oa.time.time
    original_sleep = oa.time.sleep
    oa.time.time = lambda: 0.001
    oa.time.sleep = lambda _seconds: None
    try:
        result = oa._run_upload_flow_agent(
            base_url="http://127.0.0.1:8000",
            api_key=None,
            timeout=5.0,
            requester=fake_requester,
        )
    finally:
        oa.time.time = original_time
        oa.time.sleep = original_sleep
    assert state["post_delete_list_attempts"] == 2
    assert result["status"] == "pass"
    assert result["metrics"]["projects_after_delete_ok"] == 1
    assert result["metrics"]["removed_after_delete"] == 1


def test_ops_agents_history_entry_prefers_manual_confirmation_recommendation():
    payload = _ops_agents_status_payload(
        learning_metrics={
            "manual_confirmation_required_count": 1,
            "llm_account_low_quality_pool_count": 2,
        },
        manual_confirmation_rows=[
            {
                "project_id": "p-manual",
                "project_name": "真实项目A",
                "detail": "待人工确认极端样本 4 条；当前暂无可关联预测样本",
            }
        ],
        learning_recommendations=[
            "有 2 个服务提供方的账号池历史质量分偏低，系统会优先避开弱账号，但建议继续补强冗余账号。",
            "有 1 个项目的自动学习结论仍需人工确认，建议优先处理待审核项。",
        ],
        recommendations=[
            "建议继续观察巡检趋势。",
        ],
    )

    result = soa._build_history_entry(payload)

    assert result["quality_reason_code"] == "manual_confirmation_required"
    assert result["quality_reason_label"] == "自动学习需人工确认"
    assert (
        result["top_recommendation"]
        == "有 1 个项目的自动学习结论仍需人工确认，建议优先处理待审核项。"
    )
    assert result["quality_reason_project_id"] == "p-manual"
    assert result["quality_reason_project_name"] == "真实项目A"
    assert (
        result["quality_reason_project_detail"] == "待人工确认极端样本 4 条；当前暂无可关联预测样本"
    )


def test_ops_agents_history_entry_prefers_low_quality_pool_recommendation():
    payload = _ops_agents_status_payload(
        learning_metrics={
            "llm_account_low_quality_pool_count": 2,
        },
        learning_recommendations=[
            "建议继续处理待审核项。",
            "有 2 个服务提供方的账号池历史质量分偏低，系统会优先避开弱账号，但建议继续补强冗余账号。",
        ],
        recommendations=[
            "建议继续观察巡检趋势。",
        ],
    )

    result = soa._build_history_entry(payload)

    assert result["quality_reason_code"] == "llm_low_quality_pool"
    assert result["quality_reason_label"] == "LLM 账号池质量偏低"
    assert (
        result["top_recommendation"]
        == "有 2 个服务提供方的账号池历史质量分偏低，系统会优先避开弱账号，但建议继续补强冗余账号。"
    )


def test_ops_agents_history_entry_prefers_auto_action_recommendation():
    payload = _ops_agents_status_payload(
        learning_metrics={
            "evolve_attempted_count": 1,
            "evolve_success_count": 1,
        },
        runtime_actions={
            "repair_data_hygiene": {
                "attempted": False,
                "ok": False,
            },
            "restart_runtime": {
                "attempted": False,
                "ok": False,
            },
        },
        runtime_recommendations=[
            "建议继续观察巡检趋势。",
            "已执行自动修复并完成自动学习，建议继续做修复后复验。",
        ],
        recommendations=[
            "建议继续观察巡检趋势。",
        ],
    )

    result = soa._build_history_entry(payload)

    assert result["quality_reason_code"] == "auto_actions_executed"
    assert result["quality_reason_label"] == "已执行自动动作"
    assert result["top_recommendation"] == "已执行自动修复并完成自动学习，建议继续做修复后复验。"
