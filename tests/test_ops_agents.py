from __future__ import annotations

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
    assert result["agents"]["scoring_quality"]["status"] == "fail"
    assert result["agents"]["evolution"]["status"] == "fail"


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

    result = oa.run_ops_agents_cycle(base_url="http://127.0.0.1:8000", max_workers=3)
    assert result["overall"]["status"] == "warn"
    assert result["overall"]["warn_count"] == 1
    assert result["overall"]["fail_count"] == 0
    assert "quality watch" in result["recommendations"]
