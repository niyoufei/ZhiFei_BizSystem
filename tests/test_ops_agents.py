from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.engine import ops_agents as oa


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


def test_run_ops_agents_cycle_retries_smoke_after_restart(monkeypatch):
    project_calls = {"count": 0}
    restart_calls = {"count": 0}

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
        lambda restart_cmd: restart_calls.__setitem__("count", restart_calls["count"] + 1)
        or {
            "attempted": True,
            "ok": True,
            "returncode": 0,
            "error": None,
        },
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


def test_learning_calibration_agent_auto_runs_evolve_and_reflection():
    recent_iso = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    calls = {"health": 0, "governance": 0}

    def fake_requester(**kwargs):
        method = str(kwargs.get("method") or "")
        url = str(kwargs.get("url") or "")
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


def test_learning_calibration_agent_warns_when_manual_confirmation_required():
    recent_iso = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    def fake_requester(**kwargs):
        method = str(kwargs.get("method") or "")
        url = str(kwargs.get("url") or "")
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
                        "few_shot_pending_review_count": 0,
                    },
                    "score_preview": {
                        "current_calibrator_version": "prior_five_scale_global_offset_v1",
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
        min_samples=3,
        requester=fake_requester,
    )

    assert result["status"] == "warn"
    assert result["metrics"]["manual_confirmation_required_count"] == 1
    assert result["metrics"]["pending_calibration_after"] == 1
    assert result["metrics"]["reflection_attempted_count"] == 0
    assert any("极端偏差样本" in row for row in result["recommendations"])


def test_learning_calibration_agent_respects_recent_reflection_cooldown():
    recent_iso = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    calibration_latest = datetime.now(timezone.utc).isoformat()

    def fake_requester(**kwargs):
        method = str(kwargs.get("method") or "")
        url = str(kwargs.get("url") or "")
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
    assert result["metrics"]["removed_after_delete"] == 1


def test_tender_project_flow_agent_smoke_success():
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
                    "project": {"id": "ops-tender-p1", "name": "OPS招标项目_1"},
                    "inferred_name": "OPS招标项目_1",
                },
                "error": None,
            }
        if method == "GET" and url.endswith("/api/v1/projects"):
            rows = [{"id": "p1", "name": "项目1"}]
            if state["created"] and not state["deleted"]:
                rows.append({"id": "ops-tender-p1", "name": "OPS招标项目_1"})
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
    assert result["metrics"]["removed_after_delete"] == 1


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
    assert result["metrics"]["removed_after_delete"] == 1
