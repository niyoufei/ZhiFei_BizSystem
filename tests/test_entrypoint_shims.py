from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import app.cli as cli_runtime
import app.main as app_main
from app.application.services.workflows import CliScoreExecution
from app.bootstrap.app_factory import create_fastapi_app
from app.bootstrap.config import ServerBinding
from app.bootstrap.entrypoints import run_api


def _service_bundle(**overrides):
    defaults = {
        "projects": SimpleNamespace(),
        "materials": SimpleNamespace(),
        "scoring": SimpleNamespace(),
        "governance": SimpleNamespace(),
        "learning": SimpleNamespace(),
        "ops": SimpleNamespace(),
        "cli": SimpleNamespace(),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_main_module_compat_alias_stays_available():
    assert app_main.__name__ == "app.application.runtime"
    assert hasattr(app_main, "create_app")


def test_cli_module_compat_alias_stays_available():
    assert cli_runtime.__name__ == "app.interfaces.cli.runtime"
    assert hasattr(cli_runtime, "app")


def test_bootstrap_factory_returns_runtime_app():
    assert create_fastapi_app() is app_main.app


def test_create_project_route_delegates_to_application_service():
    sentinel = object()
    services = _service_bundle(
        projects=SimpleNamespace(create_project=MagicMock(return_value=sentinel))
    )
    payload = app_main.ProjectCreate(name="项目A")

    with patch("app.main.get_application_services", return_value=services):
        result = app_main.create_project(payload)

    assert result is sentinel
    services.projects.create_project.assert_called_once_with(payload)


def test_upload_material_route_delegates_to_application_service():
    async def _run():
        services = _service_bundle(
            materials=SimpleNamespace(upload_material=AsyncMock(return_value={"ok": True}))
        )
        file = SimpleNamespace(filename="招标文件.pdf", content_type="application/pdf")

        with patch("app.main.get_application_services", return_value=services):
            result = await app_main.upload_material(
                project_id="p1",
                file=file,
                material_type="tender_qa",
                locale="zh",
            )

        assert result == {"ok": True}
        services.materials.upload_material.assert_awaited_once_with(
            project_id="p1",
            file=file,
            material_type="tender_qa",
            locale="zh",
        )

    asyncio.run(_run())


def test_score_route_delegates_to_application_service():
    sentinel = object()
    services = _service_bundle(
        scoring=SimpleNamespace(score_submission_text=MagicMock(return_value=sentinel))
    )
    payload = app_main.ScoreRequest(text="施工组织设计内容")

    with patch("app.main.get_application_services", return_value=services):
        result = app_main.score_text_for_project(project_id="p1", payload=payload, locale="zh")

    assert result is sentinel
    services.scoring.score_submission_text.assert_called_once_with(
        project_id="p1",
        payload=payload,
        locale="zh",
    )


def test_governance_route_delegates_to_application_service():
    sentinel = object()
    services = _service_bundle(
        governance=SimpleNamespace(get_feedback_governance=MagicMock(return_value=sentinel))
    )

    with patch("app.main.get_application_services", return_value=services):
        result = app_main.get_feedback_governance(project_id="p1", locale="zh")

    assert result is sentinel
    services.governance.get_feedback_governance.assert_called_once_with(
        project_id="p1",
        locale="zh",
    )


def test_evolve_route_delegates_to_application_service():
    sentinel = object()
    services = _service_bundle(
        learning=SimpleNamespace(evolve_project=MagicMock(return_value=sentinel))
    )

    with patch("app.main.get_application_services", return_value=services):
        result = app_main.evolve_project(project_id="p1", confirm_extreme_sample=False, locale="zh")

    assert result is sentinel
    services.learning.evolve_project.assert_called_once_with(
        project_id="p1",
        confirm_extreme_sample=False,
        locale="zh",
    )


def test_cli_score_command_delegates_to_cli_service(capsys):
    execution = CliScoreExecution(
        output='{"judge_mode":"rules"}',
        report_json={"judge_mode": "rules"},
        summary_text="评分摘要",
        summary_path=None,
        docx_path=None,
    )
    services = _service_bundle(cli=SimpleNamespace(execute_score=MagicMock(return_value=execution)))

    with patch("app.cli.get_application_services", return_value=services):
        cli_runtime.score_command(
            input="sample_shigong.txt",
            mode="rules",
            prompt="openai_judge_qingtian_v1",
            out=None,
            summary=True,
            summary_out=None,
            docx_out=None,
            locale="zh",
        )

    output = capsys.readouterr().out
    assert '{"judge_mode":"rules"}' in output
    assert "评分摘要" in output
    services.cli.execute_score.assert_called_once()


def test_run_api_uses_factory_and_binding_without_browser():
    fake_app = object()
    fake_uvicorn = SimpleNamespace(run=MagicMock())
    binding = ServerBinding(host="127.0.0.1", port=8010, open_browser=False)

    with (
        patch("app.bootstrap.entrypoints.create_fastapi_app", return_value=fake_app),
        patch(
            "app.bootstrap.entrypoints.resolve_server_binding",
            return_value=binding,
        ),
        patch.dict("sys.modules", {"uvicorn": fake_uvicorn}),
    ):
        run_api(["--no-browser"])

    fake_uvicorn.run.assert_called_once_with(fake_app, host="127.0.0.1", port=8010, reload=False)
