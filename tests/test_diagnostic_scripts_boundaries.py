"""Static boundary tests for diagnostic scripts."""

from __future__ import annotations

from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _read_repo_text(relative_path: str) -> str:
    return (_repo_root() / relative_path).read_text(encoding="utf-8")


def test_diagnostic_scripts_runtime_boundaries_are_visible():
    """Diagnostic scripts must stay visibly separated from read-only checks."""
    doctor = _read_repo_text("scripts/doctor.sh")
    restart_server = _read_repo_text("scripts/restart_server.sh")
    data_hygiene = _read_repo_text("scripts/data_hygiene.sh")
    e2e_flow = _read_repo_text("scripts/e2e_api_flow.sh")
    server_status = _read_repo_text("scripts/server_status.sh")
    boundary_doc = _read_repo_text("docs/health-stability-runtime-boundaries.md")

    assert "restart_server.sh" in doctor
    assert "/health" in doctor
    assert "self_check" in doctor or "system/self_check" in doctor
    assert "openapi" in doctor or "openapi.json" in doctor
    assert "curl" in doctor

    assert "build/server.pid" in restart_server
    assert "build/server.log" in restart_server
    assert "kill" in restart_server
    assert "python" in restart_server or "python3" in restart_server
    assert "app.main" in restart_server
    assert "screen" in restart_server or "nohup" in restart_server

    assert "data_hygiene" in data_hygiene
    assert "APPLY" in data_hygiene
    assert "repair" in data_hygiene
    assert "POST" in data_hygiene
    assert "build/data_hygiene_latest" in data_hygiene

    assert "project" in e2e_flow or "projects" in e2e_flow
    assert "upload" in e2e_flow or "materials" in e2e_flow
    assert "score" in e2e_flow or "rescore" in e2e_flow
    assert "compare" in e2e_flow or "compare_report" in e2e_flow
    assert "evolve" in e2e_flow or "learning" in e2e_flow
    assert "build/e2e_flow" in e2e_flow
    assert "curl" in e2e_flow

    assert "/health" in server_status
    assert "build/server.pid" in server_status or "server.pid" in server_status

    assert "diagnostic scripts 副作用边界说明" in boundary_doc
    assert "doctor.sh" in boundary_doc
    assert "restart_server.sh" in boundary_doc
    assert "data_hygiene.sh" in boundary_doc
    assert "e2e_api_flow.sh" in boundary_doc
    assert "server_status.sh" in boundary_doc
    assert "不是纯只读检查" in boundary_doc
    assert "单独授权" in boundary_doc
    assert "服务控制脚本" in boundary_doc
    assert "端到端写入验证" in boundary_doc
    assert "不应在只读阶段执行" in boundary_doc
    assert "不改变任何脚本行为" in boundary_doc
