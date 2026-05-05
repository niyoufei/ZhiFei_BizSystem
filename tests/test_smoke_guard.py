from __future__ import annotations

import contextlib
import http.server
import json
import socket
import threading
from typing import Iterator

import pytest

from tools import smoke_guard


class SmokeHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/missing":
            self.send_response(404)
            self.send_header("content-type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"missing")
            return
        if self.path == "/health":
            self.send_response(204)
            self.end_headers()
            return
        body = b"alpha keyword"
        self.send_response(200)
        self.send_header("content-type", "text/plain; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return None


@contextlib.contextmanager
def local_server() -> Iterator[str]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), SmokeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_parse_paths_adds_slashes_and_skips_empty_items() -> None:
    assert smoke_guard.parse_paths(" /,/health,docs,, ") == ["/", "/health", "/docs"]


def test_parse_paths_defaults_to_root() -> None:
    assert smoke_guard.parse_paths("") == ["/"]


def test_build_url_joins_base_and_path() -> None:
    assert (
        smoke_guard.build_url("http://127.0.0.1:8000/base", "health")
        == "http://127.0.0.1:8000/base/health"
    )


def test_parse_statuses_defaults_and_custom_values() -> None:
    assert 200 in smoke_guard.parse_statuses("")
    assert smoke_guard.parse_statuses("200, 418") == {200, 418}


def test_parse_statuses_rejects_invalid_values() -> None:
    with pytest.raises(smoke_guard.SmokeGuardError):
        smoke_guard.parse_statuses("ok")


def test_probe_url_success_against_temporary_local_server() -> None:
    with local_server() as base_url:
        result = smoke_guard.probe_url(base_url, "/", timeout=2)
    assert result.ok is True
    assert result.status_code == 200
    assert result.body_bytes > 0
    assert "text/plain" in result.content_type


def test_probe_url_accepts_no_content_status() -> None:
    with local_server() as base_url:
        result = smoke_guard.probe_url(base_url, "/health", timeout=2)
    assert result.ok is True
    assert result.status_code == 204


def test_expect_text_hit() -> None:
    with local_server() as base_url:
        result = smoke_guard.probe_url(base_url, "/", timeout=2, expected_text=["keyword"])
    assert result.ok is True
    assert result.text_matches == (True,)


def test_expect_text_miss_marks_result_failed() -> None:
    with local_server() as base_url:
        result = smoke_guard.probe_url(base_url, "/", timeout=2, expected_text=["absent"])
    assert result.ok is False
    assert result.text_matches == (False,)
    assert "Missing expected text" in result.error


def test_multi_path_with_one_failure_is_overall_fail() -> None:
    with local_server() as base_url:
        results = smoke_guard.probe_urls(base_url, ["/", "/missing"], timeout=2)
    report = smoke_guard.render_url_report(
        title="probe", base_url=base_url, paths=["/", "/missing"], results=results
    )
    assert [result.ok for result in results] == [True, False]
    assert "- result: FAIL" in report


def test_markdown_report_pass() -> None:
    result = smoke_guard.UrlProbeResult(
        "/", "http://example.local/", True, 200, 1.5, "text/plain", 5, (), ()
    )
    report = smoke_guard.render_url_report(
        title="probe", base_url="http://example.local", paths=["/"], results=[result]
    )
    assert "# smoke_guard probe report" in report
    assert "- result: PASS" in report
    assert "- none" in report


def test_markdown_report_fail() -> None:
    result = smoke_guard.UrlProbeResult(
        "/", "http://example.local/", False, 500, 1.5, "text/plain", 5, (), (), "bad"
    )
    report = smoke_guard.render_url_report(
        title="probe", base_url="http://example.local", paths=["/"], results=[result]
    )
    assert "- result: FAIL" in report
    assert "`/`: bad" in report


def test_check_port_closed_reports_failure() -> None:
    result = smoke_guard.check_port("127.0.0.1", closed_port(), timeout=0.2)
    assert result.ok is False
    assert result.error


def test_prepare_start_command_uses_argument_vector() -> None:
    assert smoke_guard.prepare_start_command("python3 -m http.server 0") == [
        "python3",
        "-m",
        "http.server",
        "0",
    ]


def test_prepare_start_command_rejects_shell_script_files() -> None:
    with pytest.raises(smoke_guard.SmokeGuardError):
        smoke_guard.prepare_start_command("run.sh")


def test_scenario_basic_runtime_paths() -> None:
    plan = smoke_guard.build_scenario_plan("basic-runtime")
    assert plan.paths == ("/health", "/ready", "/")
    assert plan.report_latest_skipped is False


def test_scenario_light_page_paths() -> None:
    plan = smoke_guard.build_scenario_plan("light-page")
    assert plan.paths == ("/", "/__ping__")


def test_scenario_api_status_paths() -> None:
    plan = smoke_guard.build_scenario_plan("api-status")
    assert plan.paths == (
        "/api/v1/auth/status",
        "/api/v1/rate_limit/status",
        "/api/v1/config/status",
    )


def test_scenario_delivery_read_requires_submission_id() -> None:
    with pytest.raises(
        smoke_guard.SmokeGuardError, match="submission_id required for delivery-read"
    ):
        smoke_guard.build_scenario_plan("delivery-read", project_id="p1")


def test_scenario_delivery_read_paths_with_submission_id() -> None:
    plan = smoke_guard.build_scenario_plan("delivery-read", project_id="p1", submission_id="s1")
    assert plan.paths == (
        "/api/v1/submissions/s1/reports/latest",
        "/api/v1/projects/p1/analysis_bundle",
    )


def test_scenario_qingtian_runtime_skips_report_latest_without_submission_id() -> None:
    plan = smoke_guard.build_scenario_plan("qingtian-runtime-v1", project_id="p1")
    assert "/api/v1/projects/p1/analysis_bundle" in plan.paths
    assert all("reports/latest" not in path for path in plan.paths)
    assert plan.report_latest_skipped is True


def test_scenario_names_include_qingtian_data_preflight_v1() -> None:
    assert "qingtian-data-preflight-v1" in smoke_guard.SCENARIO_NAMES


def test_scenario_qingtian_data_preflight_has_no_http_paths() -> None:
    plan = smoke_guard.build_scenario_plan("qingtian-data-preflight-v1", project_id="p1")
    assert plan.paths == ()


def _write_json(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_data_preflight_fails_when_submissions_json_missing(tmp_path, capsys) -> None:
    data_dir = tmp_path / "data"
    _write_json(data_dir / "projects.json", [{"id": "p1", "name": "项目1"}])

    code = smoke_guard.main(["data-preflight", "--project-id", "p1", "--data-dir", str(data_dir)])

    output = capsys.readouterr().out
    assert code == 1
    assert "missing data/submissions.json" in output
    assert "evidence_trace/latest and scoring_basis/latest need latest submission" in output
    assert "- final_result: FAIL" in output


def test_data_preflight_fails_when_project_has_no_submissions(tmp_path, capsys) -> None:
    data_dir = tmp_path / "data"
    _write_json(data_dir / "projects.json", [{"id": "p1", "name": "项目1"}])
    _write_json(data_dir / "submissions.json", [{"id": "s2", "project_id": "p2"}])

    code = smoke_guard.main(["data-preflight", "--project-id", "p1", "--data-dir", str(data_dir)])

    output = capsys.readouterr().out
    assert code == 1
    assert "no submissions for project_id" in output
    assert "- final_result: FAIL" in output


def test_data_preflight_passes_with_selectable_project_submission(tmp_path, capsys) -> None:
    data_dir = tmp_path / "data"
    _write_json(data_dir / "projects.json", [{"id": "p1", "name": "项目1"}])
    _write_json(
        data_dir / "submissions.json",
        [
            {
                "id": "s1",
                "project_id": "p1",
                "created_at": "2026-05-05T00:00:00+00:00",
                "report": {"scoring_status": "scored"},
            }
        ],
    )

    code = smoke_guard.main(["data-preflight", "--project-id", "p1", "--data-dir", str(data_dir)])

    output = capsys.readouterr().out
    assert code == 0
    assert "- selected_submission_id: s1" in output
    assert "- final_result: PASS" in output


def test_data_preflight_fails_on_invalid_json(tmp_path, capsys) -> None:
    data_dir = tmp_path / "data"
    _write_json(data_dir / "projects.json", [{"id": "p1", "name": "项目1"}])
    (data_dir / "submissions.json").write_text("{not-json", encoding="utf-8")

    code = smoke_guard.main(["data-preflight", "--project-id", "p1", "--data-dir", str(data_dir)])

    output = capsys.readouterr().out
    assert code == 1
    assert "invalid json" in output
    assert "submissions.json" in output
    assert "- final_result: FAIL" in output


def test_scenario_qingtian_data_preflight_fails_when_submissions_json_missing(
    tmp_path, capsys
) -> None:
    data_dir = tmp_path / "data"
    _write_json(data_dir / "projects.json", [{"id": "p1", "name": "项目1"}])

    code = smoke_guard.main(
        [
            "scenario",
            "--name",
            "qingtian-data-preflight-v1",
            "--project-id",
            "p1",
            "--data-dir",
            str(data_dir),
        ]
    )

    output = capsys.readouterr().out
    assert code == 1
    assert "- mode: scenario" in output
    assert "- scenario_name: qingtian-data-preflight-v1" in output
    assert "- http_access_used: false" in output
    assert "missing data/submissions.json" in output
    assert "- final_result: FAIL" in output


def test_scenario_qingtian_data_preflight_passes_with_fixture(tmp_path, capsys) -> None:
    data_dir = tmp_path / "data"
    _write_json(data_dir / "projects.json", [{"id": "p1", "name": "项目1"}])
    _write_json(
        data_dir / "submissions.json",
        [
            {
                "id": "s1",
                "project_id": "p1",
                "created_at": "2026-05-05T00:00:00+00:00",
                "report": {"scoring_status": "scored"},
            }
        ],
    )

    code = smoke_guard.main(
        [
            "scenario",
            "--name",
            "qingtian-data-preflight-v1",
            "--project-id",
            "p1",
            "--data-dir",
            str(data_dir),
        ]
    )

    output = capsys.readouterr().out
    assert code == 0
    assert "- selected_submission_id: s1" in output
    assert "- final_result: PASS" in output


def test_scenario_qingtian_runtime_v1_does_not_require_submissions_json(tmp_path, capsys) -> None:
    data_dir = tmp_path / "data"
    _write_json(data_dir / "projects.json", [{"id": "p1", "name": "项目1"}])

    with local_server() as base_url:
        code = smoke_guard.main(
            [
                "scenario",
                "--name",
                "qingtian-runtime-v1",
                "--base-url",
                base_url,
                "--allow-status",
                "200,204",
                "--data-dir",
                str(data_dir),
            ]
        )

    output = capsys.readouterr().out
    assert code == 0
    assert "- scenario_name: qingtian-runtime-v1" in output
    assert "missing data/submissions.json" not in output
    assert "- final_result: PASS" in output


def test_scenario_denylist_allows_scoring_readiness() -> None:
    assert smoke_guard.scenario_forbidden_fragments("/api/v1/projects/p1/scoring_readiness") == []


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/projects/p1/score",
        "/api/v1/projects/p1/rescore",
        "/api/v1/projects/p1/evolve",
        "/api/v1/projects/p1/evolve/ollama_preview",
        "/api/v1/projects/p1/download",
        "/api/v1/projects/p1/export",
        "/api/v1/projects/p1/analysis_bundle.md",
    ],
)
def test_scenario_denylist_blocks_action_paths(path: str) -> None:
    assert smoke_guard.scenario_forbidden_fragments(path)


def test_browser_markdown_copy_help_is_visible(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        smoke_guard.main(["browser-markdown-copy", "--help"])
    output = capsys.readouterr().out
    assert exc_info.value.code == 0
    assert "browser-markdown-copy" in output
    assert "--browser-executable" in output
    assert "--button-selector" in output


def test_browser_markdown_copy_lazy_import_does_not_affect_other_modes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_if_called() -> object:
        raise AssertionError("playwright should be lazy for non-browser modes")

    monkeypatch.setattr(smoke_guard, "load_playwright_sync_api", fail_if_called)
    with local_server() as base_url:
        assert smoke_guard.main(["probe", "--base-url", base_url, "--paths", "/"]) == 0
        assert smoke_guard.main(["report", "--base-url", base_url, "--paths", "/"]) == 0
        assert (
            smoke_guard.main(
                [
                    "scenario",
                    "--scenario-name",
                    "basic-runtime",
                    "--base-url",
                    base_url,
                    "--allow-status",
                    "200,204",
                ]
            )
            == 0
        )
    capsys.readouterr()


def test_browser_markdown_copy_missing_playwright_reports_clear_error(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    browser = tmp_path / "chrome"
    browser.write_text("#!/bin/sh\n", encoding="utf-8")
    browser.chmod(0o755)

    def missing_playwright() -> object:
        raise smoke_guard.SmokeGuardError("Playwright is required for browser-markdown-copy")

    monkeypatch.setattr(smoke_guard, "load_playwright_sync_api", missing_playwright)
    code = smoke_guard.main(["browser-markdown-copy", "--browser-executable", str(browser)])
    output = capsys.readouterr().out
    assert code == 2
    assert "Playwright is required for browser-markdown-copy" in output
    assert "- final_result: FAIL" in output


def test_browser_markdown_copy_external_base_url_fail_closed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = smoke_guard.main(
        [
            "browser-markdown-copy",
            "--base-url",
            "https://example.com",
            "--browser-executable",
            "/not/used",
        ]
    )
    output = capsys.readouterr().out
    assert code == 2
    assert "- external_network_seen: true" in output
    assert "- final_result: FAIL" in output


def test_browser_markdown_copy_missing_browser_executable_fail_closed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = smoke_guard.main(
        ["browser-markdown-copy", "--browser-executable", "/tmp/no-such-browser"]
    )
    output = capsys.readouterr().out
    assert code == 2
    assert "browser executable not found or not executable" in output
    assert "- final_result: FAIL" in output


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/projects/p1/rescore",
        "/api/v1/projects/p1/evolve/ollama_preview",
        "/api/v1/projects/p1/download",
        "/api/v1/projects/p1/export",
        "/api/v1/projects/p1/analysis_bundle.md",
    ],
)
def test_browser_markdown_copy_denylist_blocks_action_paths(path: str) -> None:
    assert smoke_guard.browser_markdown_copy_forbidden_fragments(path)


def test_browser_markdown_copy_denylist_allows_scoring_readiness() -> None:
    assert (
        smoke_guard.browser_markdown_copy_forbidden_fragments(
            "/api/v1/projects/p1/scoring_readiness"
        )
        == []
    )


def test_browser_markdown_copy_init_request_allowlist() -> None:
    policy = smoke_guard.build_browser_markdown_copy_policy("p1")
    expected = {
        "/",
        "/api/v1/projects",
        "/api/v1/projects/p1/expert-profile",
        "/api/v1/projects/p1/submissions",
        "/api/v1/projects/p1/materials",
        "/api/v1/projects/p1/scoring_readiness",
        "/api/v1/projects/p1/ground_truth",
    }
    assert set(policy.init_readonly_paths) == expected
    for path in expected:
        category, normalized, fragments = smoke_guard.classify_browser_markdown_copy_request(
            method="GET",
            url=f"http://127.0.0.1:8013{path}",
            base_url="http://127.0.0.1:8013",
            phase="init",
            policy=policy,
        )
        assert category == "init_readonly_requests"
        assert normalized == path
        assert fragments == ()


def test_browser_markdown_copy_click_delta_allowlist() -> None:
    policy = smoke_guard.build_browser_markdown_copy_policy("p1")
    category, normalized, fragments = smoke_guard.classify_browser_markdown_copy_request(
        method="GET",
        url="http://127.0.0.1:8013/api/v1/projects/p1/analysis_bundle",
        base_url="http://127.0.0.1:8013",
        phase="click",
        policy=policy,
    )
    assert category == "click_delta_requests"
    assert normalized == "/api/v1/projects/p1/analysis_bundle"
    assert fragments == ()

    category, normalized, _ = smoke_guard.classify_browser_markdown_copy_request(
        method="GET",
        url="http://127.0.0.1:8013/api/v1/projects/p1/materials",
        base_url="http://127.0.0.1:8013",
        phase="click",
        policy=policy,
    )
    assert category == "forbidden_path"
    assert normalized == "/api/v1/projects/p1/materials"


def test_cli_scenario_external_base_url_fail_closed(capsys: pytest.CaptureFixture[str]) -> None:
    code = smoke_guard.main(
        ["scenario", "--scenario-name", "basic-runtime", "--base-url", "https://example.com"]
    )
    output = capsys.readouterr().out
    assert code == 2
    assert "external_base_url_blocked: true" in output
    assert "final_result: FAIL" in output


def test_cli_scenario_basic_runtime_success(capsys: pytest.CaptureFixture[str]) -> None:
    with local_server() as base_url:
        code = smoke_guard.main(
            [
                "scenario",
                "--scenario-name",
                "basic-runtime",
                "--base-url",
                base_url,
                "--allow-status",
                "200,204",
            ]
        )
    output = capsys.readouterr().out
    assert code == 0
    assert "- mode: scenario" in output
    assert "- scenario_name: basic-runtime" in output
    assert "- request_count: 3" in output
    assert "- final_result: PASS" in output


def test_cli_probe_returns_zero_for_success(capsys: pytest.CaptureFixture[str]) -> None:
    with local_server() as base_url:
        code = smoke_guard.main(["probe", "--base-url", base_url, "--paths", "/"])
    output = capsys.readouterr().out
    assert code == 0
    assert "- result: PASS" in output


def test_cli_probe_returns_nonzero_for_failed_status(capsys: pytest.CaptureFixture[str]) -> None:
    with local_server() as base_url:
        code = smoke_guard.main(["probe", "--base-url", base_url, "--paths", "/missing"])
    output = capsys.readouterr().out
    assert code == 1
    assert "- result: FAIL" in output


def test_cli_report_mode_outputs_markdown(capsys: pytest.CaptureFixture[str]) -> None:
    with local_server() as base_url:
        code = smoke_guard.main(["report", "--base-url", base_url, "--paths", "/"])
    output = capsys.readouterr().out
    assert code == 0
    assert "# smoke_guard report report" in output


def test_cli_check_port_closed_returns_nonzero(capsys: pytest.CaptureFixture[str]) -> None:
    code = smoke_guard.main(
        ["check-port", "--host", "127.0.0.1", "--port", str(closed_port()), "--timeout", "0.2"]
    )
    output = capsys.readouterr().out
    assert code == 1
    assert "port_status: `closed`" in output
