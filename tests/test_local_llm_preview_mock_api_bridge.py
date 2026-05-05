from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

import app.main as app_main

PATH = "/local-llm/preview-mock"
FLAG = "LOCAL_LLM_PREVIEW_MOCK_API_ENABLED"
ADAPTER_FLAG = "LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED"
REAL_TRANSPORT_FLAG = "LOCAL_LLM_OLLAMA_REAL_TRANSPORT_ENABLED"
MODEL_FLAG = "LOCAL_LLM_OLLAMA_MODEL"
TIMEOUT_FLAG = "LOCAL_LLM_OLLAMA_TIMEOUT_SECONDS"
NUM_PREDICT_FLAG = "LOCAL_LLM_OLLAMA_NUM_PREDICT"


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


@pytest.fixture(autouse=True)
def _clear_adapter_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ADAPTER_FLAG, raising=False)
    monkeypatch.delenv(REAL_TRANSPORT_FLAG, raising=False)
    monkeypatch.delenv(MODEL_FLAG, raising=False)
    monkeypatch.delenv(TIMEOUT_FLAG, raising=False)
    monkeypatch.delenv(NUM_PREDICT_FLAG, raising=False)


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


def _set_adapter_flag(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    if value is None:
        monkeypatch.delenv(ADAPTER_FLAG, raising=False)
    else:
        monkeypatch.setenv(ADAPTER_FLAG, value)


def _set_real_transport_flag(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    if value is None:
        monkeypatch.delenv(REAL_TRANSPORT_FLAG, raising=False)
    else:
        monkeypatch.setenv(REAL_TRANSPORT_FLAG, value)


def _fake_adapter_success(*args, **kwargs) -> dict:
    return {
        "adapter": "ollama_preview",
        "source": "ollama_preview_adapter",
        "status": "ok",
        "reason": "ok",
        "preview_only": True,
        "no_write": True,
        "affects_score": False,
        "model": "fake-local-model",
        "advisory": {
            "summary": "Fake adapter advisory preview.",
            "boundary": {
                "preview_only": True,
                "no_write": True,
                "affects_score": False,
            },
        },
    }


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


def test_disabled_state_does_not_check_or_call_ollama_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(FLAG, raising=False)
    monkeypatch.setenv(ADAPTER_FLAG, "true")
    monkeypatch.setenv(REAL_TRANSPORT_FLAG, "true")
    monkeypatch.setattr(
        app_main.local_llm_ollama_preview_adapter,
        "is_ollama_preview_enabled",
        _fail_call,
    )
    monkeypatch.setattr(
        app_main.local_llm_ollama_preview_adapter,
        "is_ollama_real_transport_enabled",
        _fail_call,
    )
    monkeypatch.setattr(
        app_main.local_llm_ollama_preview_adapter,
        "run_ollama_preview",
        _fail_call,
    )
    monkeypatch.setattr(
        app_main.local_llm_ollama_preview_adapter,
        "select_local_ollama_model",
        _fail_call,
    )

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


@pytest.mark.parametrize("adapter_value", [None, "", "false", "0", "no", "off"])
def test_endpoint_enabled_adapter_disabled_keeps_mock_only_helper(
    monkeypatch: pytest.MonkeyPatch, adapter_value: str | None
) -> None:
    monkeypatch.setenv(FLAG, "true")
    _set_adapter_flag(monkeypatch, adapter_value)
    monkeypatch.setenv(REAL_TRANSPORT_FLAG, "true")
    monkeypatch.setattr(
        app_main.local_llm_ollama_preview_adapter,
        "is_ollama_real_transport_enabled",
        _fail_call,
    )
    monkeypatch.setattr(
        app_main.local_llm_ollama_preview_adapter,
        "run_ollama_preview",
        _fail_call,
    )

    response = _client().post(PATH, json=_valid_payload())

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["mock_only"] is True
    assert data["source"] == "local_llm_preview_mock"


def test_endpoint_enabled_adapter_enabled_enters_preview_adapter_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    def fake_run_ollama_preview(**kwargs) -> dict:
        calls.append(deepcopy(kwargs))
        return _fake_adapter_success()

    monkeypatch.setenv(FLAG, "true")
    monkeypatch.setenv(ADAPTER_FLAG, "true")
    _patch_helper_to_fail(monkeypatch)
    monkeypatch.setattr(
        app_main.local_llm_ollama_preview_adapter,
        "run_ollama_preview",
        fake_run_ollama_preview,
    )

    response = _client().post(PATH, json=_valid_payload())

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["adapter"] == "ollama_preview"
    assert data["adapter_enabled"] is True
    assert data["adapter_feature_flag"] == ADAPTER_FLAG
    assert data["preview_only"] is True
    assert data["no_write"] is True
    assert data["affects_score"] is False
    assert calls == [
        {
            "feature_flag_value": "true",
            "prompt": "sample tender response excerpt",
            "model": "local-preview-no-real-model",
            "timeout_seconds": app_main.local_llm_ollama_preview_adapter.DEFAULT_TIMEOUT_SECONDS,
            "client": None,
            "metadata": _valid_payload(),
        }
    ]


def test_adapter_enabled_without_fake_client_returns_no_real_model_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(FLAG, "true")
    monkeypatch.setenv(ADAPTER_FLAG, "true")
    _patch_forbidden_runtime_paths(monkeypatch)

    response = _client().post(PATH, json=_valid_payload())

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error"
    assert data["error_type"] == "model_unavailable"
    assert data["preview_only"] is True
    assert data["no_write"] is True
    assert data["affects_score"] is False


@pytest.mark.parametrize("real_value", [None, "", "false", "0", "no", "off"])
def test_adapter_enabled_real_transport_disabled_does_not_construct_transport(
    monkeypatch: pytest.MonkeyPatch, real_value: str | None
) -> None:
    monkeypatch.setenv(FLAG, "true")
    monkeypatch.setenv(ADAPTER_FLAG, "true")
    _set_real_transport_flag(monkeypatch, real_value)
    monkeypatch.setattr(
        app_main.local_llm_ollama_preview_adapter,
        "select_local_ollama_model",
        _fail_call,
    )
    monkeypatch.setattr(
        app_main.local_llm_ollama_preview_adapter,
        "build_real_ollama_preview_client",
        _fail_call,
    )

    response = _client().post(PATH, json=_valid_payload())

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error"
    assert data["error_type"] == "model_unavailable"
    assert data["real_transport_enabled"] is False
    assert data["preview_only"] is True
    assert data["no_write"] is True
    assert data["affects_score"] is False


def test_real_transport_enabled_with_fake_empty_tags_returns_model_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(FLAG, "true")
    monkeypatch.setenv(ADAPTER_FLAG, "true")
    monkeypatch.setenv(REAL_TRANSPORT_FLAG, "true")
    monkeypatch.setattr(
        app_main.local_llm_ollama_preview_adapter,
        "select_local_ollama_model",
        lambda **_: None,
    )
    monkeypatch.setattr(
        app_main.local_llm_ollama_preview_adapter,
        "build_real_ollama_preview_client",
        _fail_call,
    )

    response = _client().post(PATH, json=_valid_payload())

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error"
    assert data["error_type"] == "model_unavailable"
    assert data["real_transport_enabled"] is True
    assert data["preview_only"] is True
    assert data["no_write"] is True
    assert data["affects_score"] is False


def test_real_transport_enabled_with_fake_tags_uses_local_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_calls: list[dict] = []

    def fake_build_client(**kwargs):
        build_calls.append(deepcopy(kwargs))

        def fake_client(_: dict) -> dict:
            return {"response": "Real transport fake preview."}

        return fake_client

    monkeypatch.setenv(FLAG, "true")
    monkeypatch.setenv(ADAPTER_FLAG, "true")
    monkeypatch.setenv(REAL_TRANSPORT_FLAG, "true")
    monkeypatch.setattr(
        app_main.local_llm_ollama_preview_adapter,
        "select_local_ollama_model",
        lambda **_: "local-model-a",
    )
    monkeypatch.setattr(
        app_main.local_llm_ollama_preview_adapter,
        "build_real_ollama_preview_client",
        fake_build_client,
    )
    _patch_forbidden_runtime_paths(monkeypatch)

    response = _client().post(PATH, json=_valid_payload())

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["model"] == "local-model-a"
    assert data["real_transport_enabled"] is True
    assert data["advisory"]["summary"] == "Real transport fake preview."
    assert data["preview_only"] is True
    assert data["no_write"] is True
    assert data["affects_score"] is False
    assert build_calls == [
        {
            "timeout_seconds": app_main.local_llm_ollama_preview_adapter.DEFAULT_TIMEOUT_SECONDS,
            "num_predict": app_main.local_llm_ollama_preview_adapter.DEFAULT_GENERATE_NUM_PREDICT,
        }
    ]


def test_real_transport_uses_timeout_and_num_predict_env_controls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    select_calls: list[dict] = []
    build_calls: list[dict] = []

    def fake_select_model(**kwargs) -> str:
        select_calls.append(deepcopy(kwargs))
        return "local-model-a"

    def fake_build_client(**kwargs):
        build_calls.append(deepcopy(kwargs))
        return lambda _: {"response": "Config controlled preview."}

    monkeypatch.setenv(FLAG, "true")
    monkeypatch.setenv(ADAPTER_FLAG, "true")
    monkeypatch.setenv(REAL_TRANSPORT_FLAG, "true")
    monkeypatch.setenv(TIMEOUT_FLAG, "12.5")
    monkeypatch.setenv(NUM_PREDICT_FLAG, "6")
    monkeypatch.setattr(
        app_main.local_llm_ollama_preview_adapter,
        "select_local_ollama_model",
        fake_select_model,
    )
    monkeypatch.setattr(
        app_main.local_llm_ollama_preview_adapter,
        "build_real_ollama_preview_client",
        fake_build_client,
    )
    _patch_forbidden_runtime_paths(monkeypatch)

    response = _client().post(PATH, json=_valid_payload())

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["model"] == "local-model-a"
    assert data["preview_only"] is True
    assert data["no_write"] is True
    assert data["affects_score"] is False
    assert select_calls == [{"configured_model": None, "timeout_seconds": 12.5}]
    assert build_calls == [{"timeout_seconds": 12.5, "num_predict": 6}]


def test_real_transport_invalid_timeout_and_num_predict_fall_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_calls: list[dict] = []

    def fake_build_client(**kwargs):
        build_calls.append(deepcopy(kwargs))
        return lambda _: {"response": "Fallback config preview."}

    monkeypatch.setenv(FLAG, "true")
    monkeypatch.setenv(ADAPTER_FLAG, "true")
    monkeypatch.setenv(REAL_TRANSPORT_FLAG, "true")
    monkeypatch.setenv(TIMEOUT_FLAG, "not-a-number")
    monkeypatch.setenv(NUM_PREDICT_FLAG, "-1")
    monkeypatch.setattr(
        app_main.local_llm_ollama_preview_adapter,
        "select_local_ollama_model",
        lambda **_: "local-model-a",
    )
    monkeypatch.setattr(
        app_main.local_llm_ollama_preview_adapter,
        "build_real_ollama_preview_client",
        fake_build_client,
    )

    response = _client().post(PATH, json=_valid_payload())

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["preview_only"] is True
    assert data["no_write"] is True
    assert data["affects_score"] is False
    assert build_calls == [
        {
            "timeout_seconds": app_main.local_llm_ollama_preview_adapter.DEFAULT_TIMEOUT_SECONDS,
            "num_predict": app_main.local_llm_ollama_preview_adapter.DEFAULT_GENERATE_NUM_PREDICT,
        }
    ]


def test_real_transport_env_model_unavailable_returns_stable_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected: list[str | None] = []

    def fake_select_model(**kwargs) -> None:
        selected.append(kwargs["configured_model"])
        return None

    monkeypatch.setenv(FLAG, "true")
    monkeypatch.setenv(ADAPTER_FLAG, "true")
    monkeypatch.setenv(REAL_TRANSPORT_FLAG, "true")
    monkeypatch.setenv(MODEL_FLAG, "missing-local-model")
    monkeypatch.setattr(
        app_main.local_llm_ollama_preview_adapter,
        "select_local_ollama_model",
        fake_select_model,
    )
    monkeypatch.setattr(
        app_main.local_llm_ollama_preview_adapter,
        "build_real_ollama_preview_client",
        _fail_call,
    )

    response = _client().post(PATH, json=_valid_payload())

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error"
    assert data["error_type"] == "model_unavailable"
    assert data["preview_only"] is True
    assert data["no_write"] is True
    assert data["affects_score"] is False
    assert selected == ["missing-local-model"]


def test_real_transport_prefers_env_model_over_tags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected: list[str | None] = []

    def fake_select_model(**kwargs) -> str:
        selected.append(kwargs["configured_model"])
        return str(kwargs["configured_model"])

    def fake_build_client(**_: object):
        return lambda _: {"response": "Env model preview."}

    monkeypatch.setenv(FLAG, "true")
    monkeypatch.setenv(ADAPTER_FLAG, "true")
    monkeypatch.setenv(REAL_TRANSPORT_FLAG, "true")
    monkeypatch.setenv(MODEL_FLAG, "env-model")
    monkeypatch.setattr(
        app_main.local_llm_ollama_preview_adapter,
        "select_local_ollama_model",
        fake_select_model,
    )
    monkeypatch.setattr(
        app_main.local_llm_ollama_preview_adapter,
        "build_real_ollama_preview_client",
        fake_build_client,
    )

    response = _client().post(PATH, json=_valid_payload())

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["model"] == "env-model"
    assert selected == ["env-model"]


@pytest.mark.parametrize(
    ("fake_client", "expected_error"),
    [
        (
            lambda _: (_ for _ in ()).throw(
                app_main.local_llm_ollama_preview_adapter.OllamaUnreachableError("down")
            ),
            "ollama_unreachable",
        ),
        (lambda _: (_ for _ in ()).throw(TimeoutError("slow")), "timeout"),
        (
            lambda _: (_ for _ in ()).throw(
                app_main.local_llm_ollama_preview_adapter.OllamaModelUnavailableError("missing")
            ),
            "model_unavailable",
        ),
        (lambda _: {}, "invalid_response"),
    ],
)
def test_real_transport_fake_failures_return_stable_errors(
    monkeypatch: pytest.MonkeyPatch,
    fake_client,
    expected_error: str,
) -> None:
    monkeypatch.setenv(FLAG, "true")
    monkeypatch.setenv(ADAPTER_FLAG, "true")
    monkeypatch.setenv(REAL_TRANSPORT_FLAG, "true")
    monkeypatch.setattr(
        app_main.local_llm_ollama_preview_adapter,
        "select_local_ollama_model",
        lambda **_: "local-model-a",
    )
    monkeypatch.setattr(
        app_main.local_llm_ollama_preview_adapter,
        "build_real_ollama_preview_client",
        lambda **_: fake_client,
    )
    _patch_forbidden_runtime_paths(monkeypatch)

    response = _client().post(PATH, json=_valid_payload())

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error"
    assert data["error_type"] == expected_error
    assert data["real_transport_enabled"] is True
    assert data["preview_only"] is True
    assert data["no_write"] is True
    assert data["affects_score"] is False


def test_real_transport_enabled_invalid_payload_does_not_construct_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(FLAG, "true")
    monkeypatch.setenv(ADAPTER_FLAG, "true")
    monkeypatch.setenv(REAL_TRANSPORT_FLAG, "true")
    monkeypatch.setattr(
        app_main.local_llm_ollama_preview_adapter,
        "select_local_ollama_model",
        _fail_call,
    )
    monkeypatch.setattr(
        app_main.local_llm_ollama_preview_adapter,
        "build_real_ollama_preview_client",
        _fail_call,
    )

    response = _client().post(PATH, json={**_valid_payload(), "text_excerpt": ""})

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error"
    assert data["error_type"] == "invalid_request"
    assert data["real_transport_enabled"] is True
    assert data["preview_only"] is True
    assert data["no_write"] is True
    assert data["affects_score"] is False


@pytest.mark.parametrize(
    ("adapter_response", "expected_error"),
    [
        (
            {
                "adapter": "ollama_preview",
                "source": "ollama_preview_adapter",
                "status": "error",
                "error_type": "model_unavailable",
                "message": "Ollama preview client is not configured.",
                "preview_only": True,
                "no_write": True,
                "affects_score": False,
            },
            "model_unavailable",
        ),
        (
            {
                "adapter": "ollama_preview",
                "source": "ollama_preview_adapter",
                "status": "error",
                "error_type": "timeout",
                "message": "Ollama preview request timed out.",
                "preview_only": True,
                "no_write": True,
                "affects_score": False,
            },
            "timeout",
        ),
        (
            {
                "adapter": "ollama_preview",
                "source": "ollama_preview_adapter",
                "status": "error",
                "error_type": "invalid_response",
                "message": "Ollama response did not contain non-empty content.",
                "preview_only": True,
                "no_write": True,
                "affects_score": False,
            },
            "invalid_response",
        ),
    ],
)
def test_adapter_failure_responses_stay_stable_and_outside_scoring_chain(
    monkeypatch: pytest.MonkeyPatch, adapter_response: dict, expected_error: str
) -> None:
    monkeypatch.setenv(FLAG, "true")
    monkeypatch.setenv(ADAPTER_FLAG, "true")
    _patch_forbidden_runtime_paths(monkeypatch)
    monkeypatch.setattr(
        app_main.local_llm_ollama_preview_adapter,
        "run_ollama_preview",
        lambda **_: adapter_response,
    )

    response = _client().post(PATH, json=_valid_payload())

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error"
    assert data["error_type"] == expected_error
    assert data["preview_only"] is True
    assert data["no_write"] is True
    assert data["affects_score"] is False


def test_adapter_enabled_success_does_not_call_forbidden_runtime_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(FLAG, "true")
    monkeypatch.setenv(ADAPTER_FLAG, "true")
    _patch_forbidden_runtime_paths(monkeypatch)
    monkeypatch.setattr(
        app_main.local_llm_ollama_preview_adapter,
        "run_ollama_preview",
        lambda **_: _fake_adapter_success(),
    )

    response = _client().post(PATH, json=_valid_payload())

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["preview_only"] is True
    assert data["no_write"] is True
    assert data["affects_score"] is False


def test_adapter_enabled_state_is_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(FLAG, "true")
    monkeypatch.setenv(ADAPTER_FLAG, "true")
    monkeypatch.setattr(
        app_main.local_llm_ollama_preview_adapter,
        "run_ollama_preview",
        lambda **_: _fake_adapter_success(),
    )
    payload = _valid_payload()

    first = _client().post(PATH, json=payload).json()
    second = _client().post(PATH, json=deepcopy(payload)).json()

    assert first == second


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
