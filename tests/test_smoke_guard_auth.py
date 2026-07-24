from __future__ import annotations

import io
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from email.message import Message
from pathlib import Path

import pytest

from tools import smoke_guard


class FakeResponse:
    def __init__(self, status: int = 200, body: bytes = b"ok") -> None:
        self._status = status
        self._body = body
        self.headers = Message()
        self.headers["content-type"] = "text/plain; charset=utf-8"

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def getcode(self) -> int:
        return self._status

    def read(self) -> bytes:
        return self._body


class RecordingOpener:
    def __init__(self, response: FakeResponse | Exception | None = None) -> None:
        self.response = response or FakeResponse()
        self.requests: list[urllib.request.Request] = []

    def open(self, request: urllib.request.Request, timeout: float):
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _http_error(url: str, status: int, body: bytes) -> urllib.error.HTTPError:
    headers = Message()
    headers["content-type"] = "application/json"
    return urllib.error.HTTPError(url, status, "error", headers, io.BytesIO(body))


def test_smoke_key_prefers_dedicated_environment_variable() -> None:
    assert (
        smoke_guard.resolve_smoke_api_key(
            {
                "QINGTIAN_SMOKE_API_KEY": " dedicated-key ",
                "API_KEYS": "fallback-key",
            }
        )
        == "dedicated-key"
    )


def test_blank_dedicated_key_falls_back_to_first_nonempty_api_key() -> None:
    assert (
        smoke_guard.resolve_smoke_api_key(
            {
                "QINGTIAN_SMOKE_API_KEY": "   ",
                "API_KEYS": " , first-key, second-key ",
            }
        )
        == "first-key"
    )


def test_blank_smoke_key_inputs_are_missing() -> None:
    assert smoke_guard.resolve_smoke_api_key(
        {"QINGTIAN_SMOKE_API_KEY": " ", "API_KEYS": " ,  , "}
    ) is None


def test_public_probe_does_not_send_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    opener = RecordingOpener()
    monkeypatch.setattr(smoke_guard.urllib.request, "build_opener", lambda *args: opener)

    result = smoke_guard.probe_urls(
        "http://127.0.0.1:8013",
        ["/health"],
        api_key="public-request-must-not-send-this",
    )[0]

    assert result.ok is True
    assert opener.requests[0].get_header("X-api-key") is None


def test_protected_probe_sends_header_without_url_or_query_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = "synthetic-protected-probe-key"
    opener = RecordingOpener()
    monkeypatch.setattr(smoke_guard.urllib.request, "build_opener", lambda *args: opener)

    result = smoke_guard.probe_urls(
        "http://127.0.0.1:8013",
        ["/__ping__"],
        api_key=key,
    )[0]
    request = opener.requests[0]

    assert result.ok is True
    assert request.get_header("X-api-key") == key
    assert key not in request.full_url
    assert urllib.parse.urlsplit(request.full_url).query == ""
    assert key not in repr(result)


def test_dedicated_environment_key_reaches_protected_probe_header(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    key = "synthetic-environment-probe-key"
    opener = RecordingOpener()
    monkeypatch.setenv("QINGTIAN_SMOKE_API_KEY", key)
    monkeypatch.setattr(smoke_guard.urllib.request, "build_opener", lambda *args: opener)

    code = smoke_guard.main(
        [
            "probe",
            "--base-url",
            "http://127.0.0.1:8013",
            "--paths",
            "/__ping__",
        ]
    )

    output = capsys.readouterr().out
    assert code == 0
    assert opener.requests[0].get_header("X-api-key") == key
    assert key not in output


def test_missing_key_fails_before_protected_probe_executes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("QINGTIAN_SMOKE_API_KEY", raising=False)
    monkeypatch.delenv("API_KEYS", raising=False)

    code = smoke_guard.main(
        [
            "probe",
            "--base-url",
            "http://127.0.0.1:8013",
            "--paths",
            "/__ping__",
        ]
    )

    output = capsys.readouterr().out
    assert code != 0
    assert "SMOKE_AUTH_KEY_MISSING" in output
    assert "X-API-Key" not in output


def test_public_only_scenario_runs_without_key_and_reports_classification(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("QINGTIAN_SMOKE_API_KEY", raising=False)
    monkeypatch.delenv("API_KEYS", raising=False)
    monkeypatch.setattr(
        smoke_guard,
        "probe_urls",
        lambda base_url, paths, **kwargs: [
            smoke_guard.UrlProbeResult(
                path,
                smoke_guard.build_url(base_url, path),
                True,
                200,
                1.0,
                "text/plain",
                2,
                (),
                (),
            )
            for path in paths
        ],
    )

    code = smoke_guard.main(
        [
            "scenario",
            "--name",
            "basic-runtime",
            "--base-url",
            "http://127.0.0.1:8013",
        ]
    )

    output = capsys.readouterr().out
    assert code == 0
    assert "- public_only: true" in output
    assert "- protected_probes_executed: false" in output


@pytest.mark.parametrize(
    ("status", "body", "marker"),
    [
        (401, b'{"detail":{"code":"AUTH_REQUIRED"}}', "SMOKE_AUTH_KEY_INVALID"),
        (503, b'{"detail":{"code":"AUTH_NOT_CONFIGURED"}}', "SMOKE_AUTH_NOT_CONFIGURED"),
    ],
)
def test_auth_failures_cannot_be_allowed_as_success(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    body: bytes,
    marker: str,
) -> None:
    key = "synthetic-invalid-key"
    url = "http://127.0.0.1:8013/__ping__"
    opener = RecordingOpener(_http_error(url, status, body))
    monkeypatch.setattr(smoke_guard.urllib.request, "build_opener", lambda *args: opener)

    result = smoke_guard.probe_url(
        "http://127.0.0.1:8013",
        "/__ping__",
        expected_statuses={status},
        headers={"X-API-Key": key},
    )
    report = smoke_guard.render_url_report(
        title="auth",
        base_url="http://127.0.0.1:8013",
        paths=["/__ping__"],
        results=[result],
    )

    assert result.ok is False
    assert result.error == marker
    assert key not in report


@pytest.mark.parametrize("query_name", ["api_key", "key", "API_KEY", "KEY"])
def test_api_key_query_parameters_are_rejected(query_name: str) -> None:
    with pytest.raises(smoke_guard.SmokeGuardError, match="query parameters"):
        smoke_guard.build_url(
            "http://127.0.0.1:8013",
            f"/__ping__?{query_name}=synthetic-query-key",
        )


def test_cross_origin_redirect_is_blocked_before_forwarding_header() -> None:
    request = urllib.request.Request(
        "http://127.0.0.1:8013/__ping__",
        headers={"X-API-Key": "synthetic-redirect-key"},
    )
    handler = smoke_guard._SameOriginRedirectHandler()

    with pytest.raises(smoke_guard._CrossOriginRedirectBlocked):
        handler.redirect_request(
            request,
            None,
            302,
            "Found",
            Message(),
            "http://127.0.0.1:9013/redirect-target",
        )


def test_same_origin_redirect_to_public_path_strips_api_key() -> None:
    request = urllib.request.Request(
        "http://127.0.0.1:8013/__ping__",
        headers={"X-API-Key": "synthetic-redirect-key"},
    )
    handler = smoke_guard._SameOriginRedirectHandler()

    redirected = handler.redirect_request(
        request,
        None,
        302,
        "Found",
        Message(),
        "http://127.0.0.1:8013/health",
    )

    assert redirected is not None
    assert redirected.get_header("X-api-key") is None


def test_inprocess_runtime_uses_isolated_synthetic_server_key_and_header(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    parent_api_keys = "parent-environment-key"
    monkeypatch.setenv("API_KEYS", parent_api_keys)
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                f"QINGTIAN_DATA_DIR={tmp_path}\n"
                "DATA_DIR_MATCH=True\n"
                "SUBMISSIONS_PATH_MATCH=True\n"
                "EVIDENCE_TRACE_STATUS=200\n"
                "SCORING_BASIS_STATUS=200\n"
                "EVIDENCE_TRACE_SUBMISSION_ID=s1\n"
                "SCORING_BASIS_SUBMISSION_ID=s1\n"
                "SCORING_STATUS=scored\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(smoke_guard.subprocess, "run", fake_run)
    result = smoke_guard.run_external_data_runtime("p1", tmp_path)

    child_env = captured["env"]
    script = captured["command"][2]
    assert result.ok is True
    assert child_env["API_KEYS"] == smoke_guard.INPROCESS_SMOKE_API_KEY
    assert child_env["QINGTIAN_DATA_DIR"] == str(tmp_path.resolve())
    assert "headers=auth_headers" in script
    assert 'auth_headers = {"X-API-Key": os.environ["API_KEYS"]}' in script
    assert smoke_guard.os.environ["API_KEYS"] == parent_api_keys
