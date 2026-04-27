from __future__ import annotations

from pathlib import Path

from app.engine import ops_agents as oa

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read_repo_text(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def _contains_path_fragment(text: str, path_fragment: str) -> bool:
    path_style = path_fragment.replace("/", '" / "')
    return path_fragment in text or path_style in text


def test_start_ops_agents_script_runtime_boundaries_are_visible():
    start_script = _read_repo_text("scripts/start_ops_agents.sh")
    ops_cli = _read_repo_text("scripts/ops_agents.py")
    boundary_doc = _read_repo_text("docs/health-stability-runtime-boundaries.md")

    assert "--auto-repair 1" in start_script
    assert "--auto-evolve 1" in start_script
    assert "build/ops_agents.log" in start_script
    assert "build/ops_agents.pid" in start_script
    assert 'mkdir -p "$ROOT_DIR/build"' in start_script
    assert "screen" in start_script or "nohup" in start_script
    assert "非只读运行入口" in start_script
    assert "启动 ops_agents 守护进程" in start_script
    assert "单独授权" in start_script
    assert "文档检查、静态检查、mock 测试不需要运行本脚本" in start_script

    assert "--auto-repair" in ops_cli
    assert "--auto-evolve" in ops_cli
    assert _contains_path_fragment(ops_cli, "build/ops_agents_status.json")
    assert _contains_path_fragment(ops_cli, "build/ops_agents_status.md")
    assert "--max-cycles" in ops_cli or "--interval-seconds" in ops_cli

    assert "start_ops_agents.sh" in boundary_doc
    assert "auto-repair" in boundary_doc
    assert "auto-evolve" in boundary_doc
    assert "build/ops_agents_status.json" in boundary_doc
    assert "build/ops_agents_status.md" in boundary_doc
    assert "单独授权" in boundary_doc
    assert "不接核心评分主链" in boundary_doc


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


def test_scoring_quality_treats_preparation_critical_as_non_failure():
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
