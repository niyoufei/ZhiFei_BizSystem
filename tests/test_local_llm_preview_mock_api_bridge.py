from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

import app.main as app_main

PATH = "/local-llm/preview-mock"
FLAG = "LOCAL_LLM_PREVIEW_MOCK_API_ENABLED"


def _client() -> TestClient:
    return TestClient(app_main.app)


def _valid_payload() -> dict:
    return {
        "project_id": "p1",
        "submission_id": "s1",
        "text_excerpt": "sample tender response excerpt",
        "mode": "preview_only",
        "requested_by": "operator",
        "scoring_context": {"dimension": "technical"},
        "evidence_context": {"source": "excerpt"},
        "requirement_hits": [{"requirement": "R1", "hit": True}],
    }


def _fail_call(*args, **kwargs):
    raise AssertionError("forbidden call")


def _patch_helper_to_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        app_main.local_llm_preview_mock,
        "validate_local_llm_preview_boundary",
        _fail_call,
    )
    monkeypatch.setattr(
        app_main.local_llm_preview_mock,
        "build_local_llm_preview_input",
        _fail_call,
    )
    monkeypatch.setattr(
        app_main.local_llm_preview_mock,
        "build_local_llm_mock_response",
        _fail_call,
    )


def _patch_forbidden_runtime_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "ensure_data_dirs",
        "save_score_reports",
        "save_submissions",
        "save_qingtian_results",
        "save_evolution_reports",
        "score_text",
        "rescore_project_submissions",
        "get_latest_submission_evidence_trace",
        "get_latest_submission_scoring_basis",
        "get_latest_qingtian_result",
        "preview_evolution_report_with_ollama",
        "enhance_evolution_report_with_llm",
    ):
        monkeypatch.setattr(app_main, name, _fail_call)


def _set_flag(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    if value is None:
        monkeypatch.delenv(FLAG, raising=False)
    else:
        monkeypatch.setenv(FLAG, value)


@pytest.mark.parametrize("value", [None, "", "false", "0", "no", "off"])
def test_feature_flag_disabled_values_return_disabled(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    _set_flag(monkeypatch, value)

    response = _client().post(PATH, json=_valid_payload())

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "disabled"
    assert data["enabled"] is False
    assert data["disabled"] is True
    assert data["reason"] == "feature_flag_disabled"
    assert data["feature_flag"] == FLAG
    assert data["no_write"] is True
    assert data["affects_score"] is False
    assert "preview_input" not in data
    assert "advisory" not in data


def test_disabled_state_does_not_call_local_llm_preview_mock_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(FLAG, raising=False)
    _patch_helper_to_fail(monkeypatch)

    response = _client().post(PATH, json=_valid_payload())

    assert response.status_code == 200
    assert response.json()["status"] == "disabled"


def test_disabled_state_does_not_write_data_output_or_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(FLAG, "off")
    _patch_forbidden_runtime_paths(monkeypatch)

    response = _client().post(PATH, json=_valid_payload())

    assert response.status_code == 200
    assert response.json()["status"] == "disabled"


@pytest.mark.parametrize("value", ["true", "1", "yes", "on"])
def test_enabled_values_return_mock_only_preview(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(FLAG, value)

    response = _client().post(PATH, json=_valid_payload())

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["enabled"] is True
    assert data["feature_flag"] == FLAG
    assert data["mode"] == "mock_only"
    assert data["mock_only"] is True
    assert data["preview_only"] is True
    assert data["no_write"] is True
    assert data["affects_score"] is False
    assert data["source"] == "local_llm_preview_mock"
    assert data["preview_input"]["project_id"] == "p1"
    assert data["advisory"]["boundary"]["no_write"] is True


def test_enabled_state_does_not_call_forbidden_runtime_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(FLAG, "true")
    _patch_forbidden_runtime_paths(monkeypatch)

    response = _client().post(PATH, json=_valid_payload())

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_enabled_state_is_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(FLAG, "true")
    payload = _valid_payload()

    first = _client().post(PATH, json=payload).json()
    second = _client().post(PATH, json=deepcopy(payload)).json()

    assert first == second


@pytest.mark.parametrize(
    ("payload", "expected_message"),
    [
        (None, "payload must be a dict"),
        ({**_valid_payload(), "text_excerpt": ""}, "missing required field: text_excerpt"),
        ({**_valid_payload(), "final_score": 88}, "forbidden key: final_score"),
    ],
)
def test_invalid_enabled_payload_returns_stable_error_without_scoring_chain(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict | None,
    expected_message: str,
) -> None:
    monkeypatch.setenv(FLAG, "true")
    _patch_forbidden_runtime_paths(monkeypatch)

    if payload is None:
        response = _client().post(PATH)
    else:
        response = _client().post(PATH, json=payload)

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail == {
        "status": "error",
        "error": "invalid_preview_mock_payload",
        "message": expected_message,
        "preview_only": True,
        "mock_only": True,
        "no_write": True,
        "affects_score": False,
    }
