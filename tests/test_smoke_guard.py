from __future__ import annotations

import contextlib
import http.server
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
    assert smoke_guard.build_url("http://127.0.0.1:8000/base", "health") == "http://127.0.0.1:8000/base/health"


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
    report = smoke_guard.render_url_report(title="probe", base_url=base_url, paths=["/", "/missing"], results=results)
    assert [result.ok for result in results] == [True, False]
    assert "- result: FAIL" in report


def test_markdown_report_pass() -> None:
    result = smoke_guard.UrlProbeResult("/", "http://example.local/", True, 200, 1.5, "text/plain", 5, (), ())
    report = smoke_guard.render_url_report(title="probe", base_url="http://example.local", paths=["/"], results=[result])
    assert "# smoke_guard probe report" in report
    assert "- result: PASS" in report
    assert "- none" in report


def test_markdown_report_fail() -> None:
    result = smoke_guard.UrlProbeResult("/", "http://example.local/", False, 500, 1.5, "text/plain", 5, (), (), "bad")
    report = smoke_guard.render_url_report(title="probe", base_url="http://example.local", paths=["/"], results=[result])
    assert "- result: FAIL" in report
    assert "`/`: bad" in report


def test_check_port_closed_reports_failure() -> None:
    result = smoke_guard.check_port("127.0.0.1", closed_port(), timeout=0.2)
    assert result.ok is False
    assert result.error


def test_prepare_start_command_uses_argument_vector() -> None:
    assert smoke_guard.prepare_start_command("python3 -m http.server 0") == ["python3", "-m", "http.server", "0"]


def test_prepare_start_command_rejects_shell_script_files() -> None:
    with pytest.raises(smoke_guard.SmokeGuardError):
        smoke_guard.prepare_start_command("run.sh")


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
    code = smoke_guard.main(["check-port", "--host", "127.0.0.1", "--port", str(closed_port()), "--timeout", "0.2"])
    output = capsys.readouterr().out
    assert code == 1
    assert "port_status: `closed`" in output
