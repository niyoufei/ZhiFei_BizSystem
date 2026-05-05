from __future__ import annotations

import inspect
import json
from copy import deepcopy

import pytest

import app.engine.local_llm_ollama_preview_adapter as adapter
from app.engine.local_llm_ollama_preview_adapter import (
    OLLAMA_LOCAL_BASE_URL,
    OllamaModelUnavailableError,
    OllamaUnreachableError,
    build_disabled_response,
    build_failure_response,
    build_real_ollama_preview_client,
    fetch_local_ollama_models,
    is_ollama_preview_enabled,
    is_ollama_real_transport_enabled,
    normalize_ollama_response,
    run_ollama_preview,
    select_local_ollama_model,
    validate_ollama_preview_boundary,
)


def _valid_prompt() -> str:
    return "Review this tender response excerpt for advisory-only local LLM preview."


def _valid_model() -> str:
    return "qwen3:0.6b"


def _valid_response() -> dict:
    return {"message": {"content": "Advisory preview only."}}


def _forbidden_keys(response: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(response, dict):
        for key, value in response.items():
            keys.add(str(key))
            keys.update(_forbidden_keys(value))
    elif isinstance(response, (list, tuple, set)):
        for item in response:
            keys.update(_forbidden_keys(item))
    return keys & adapter.FORBIDDEN_EXACT_KEYS


def _fail_client(_: dict) -> dict:
    raise AssertionError("client should not be called")


def _fail_tags_client() -> list[str]:
    raise AssertionError("tags client should not be called")


class _FakeHttpResponse:
    def __init__(self, payload: bytes, status: int = 200) -> None:
        self._payload = payload
        self.status = status

    def __enter__(self) -> "_FakeHttpResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


def test_feature_flag_absent_returns_disabled_without_client_call() -> None:
    response = run_ollama_preview(
        feature_flag_value=None,
        prompt=_valid_prompt(),
        model=_valid_model(),
        client=_fail_client,
    )

    assert response["status"] == "disabled"
    assert response["reason"] == "feature_flag_disabled"
    assert response["preview_only"] is True
    assert response["no_write"] is True
    assert response["affects_score"] is False


@pytest.mark.parametrize("value", ["", "false", "0", "no", "off"])
def test_false_like_feature_flags_return_disabled(value: str) -> None:
    response = run_ollama_preview(
        feature_flag_value=value,
        prompt=_valid_prompt(),
        model=_valid_model(),
        client=_fail_client,
    )

    assert response == build_disabled_response()


def test_is_ollama_preview_enabled_accepts_only_true_values() -> None:
    assert is_ollama_preview_enabled("true") is True
    assert is_ollama_preview_enabled("1") is True
    assert is_ollama_preview_enabled("yes") is True
    assert is_ollama_preview_enabled("on") is True
    assert is_ollama_preview_enabled("false") is False
    assert is_ollama_preview_enabled(None) is False


def test_is_ollama_real_transport_enabled_accepts_only_true_values() -> None:
    assert is_ollama_real_transport_enabled("true") is True
    assert is_ollama_real_transport_enabled("1") is True
    assert is_ollama_real_transport_enabled("yes") is True
    assert is_ollama_real_transport_enabled("on") is True
    assert is_ollama_real_transport_enabled("false") is False
    assert is_ollama_real_transport_enabled(None) is False


def test_validate_ollama_preview_boundary_rejects_invalid_input_before_client() -> None:
    response = validate_ollama_preview_boundary(
        prompt="",
        model=_valid_model(),
        metadata={"nested": {"final_score": 88}},
    )

    assert response is not None
    assert response["status"] == "error"
    assert response["error_type"] == "invalid_request"
    assert response["preview_only"] is True
    assert response["no_write"] is True
    assert response["affects_score"] is False


def test_enabled_valid_fake_client_response_returns_ok() -> None:
    calls: list[dict] = []

    def fake_client(request: dict) -> dict:
        calls.append(deepcopy(request))
        return _valid_response()

    response = run_ollama_preview(
        feature_flag_value="true",
        prompt=_valid_prompt(),
        model=_valid_model(),
        timeout_seconds=3.5,
        client=fake_client,
    )

    assert response["status"] == "ok"
    assert response["reason"] == "ok"
    assert response["adapter"] == "ollama_preview"
    assert response["source"] == "ollama_preview_adapter"
    assert response["preview_only"] is True
    assert response["no_write"] is True
    assert response["affects_score"] is False
    assert response["model"] == _valid_model()
    assert response["advisory"]["summary"] == "Advisory preview only."
    assert calls == [
        {
            "model": _valid_model(),
            "messages": [{"role": "user", "content": _valid_prompt()}],
            "stream": False,
            "timeout_seconds": 3.5,
            "metadata": {},
        }
    ]


def test_enabled_success_response_excludes_formal_score_fields() -> None:
    response = run_ollama_preview(
        feature_flag_value="on",
        prompt=_valid_prompt(),
        model=_valid_model(),
        client=lambda _: {"content": "Preview advisory."},
    )

    assert _forbidden_keys(response) == set()


def test_timeout_returns_stable_failure() -> None:
    def timeout_client(_: dict) -> dict:
        raise TimeoutError("slow")

    response = run_ollama_preview(
        feature_flag_value="true",
        prompt=_valid_prompt(),
        model=_valid_model(),
        client=timeout_client,
    )

    assert response["status"] == "error"
    assert response["error_type"] == "timeout"
    assert response["message"] == "Ollama preview request timed out."
    assert response["fallback_used"] is True
    assert response["fallback"]["reason"] == "timeout"
    assert response["preview_only"] is True
    assert response["no_write"] is True
    assert response["affects_score"] is False


@pytest.mark.parametrize(
    ("exc", "expected_error"),
    [
        (OllamaUnreachableError("down"), "ollama_unreachable"),
        (OllamaModelUnavailableError("missing"), "model_unavailable"),
        (ConnectionError("down"), "model_unavailable"),
        (OSError("broken pipe"), "transport_failure"),
    ],
)
def test_transport_errors_return_stable_failure(exc: Exception, expected_error: str) -> None:
    def failing_client(_: dict) -> dict:
        raise exc

    response = run_ollama_preview(
        feature_flag_value="true",
        prompt=_valid_prompt(),
        model=_valid_model(),
        client=failing_client,
    )

    assert response["status"] == "error"
    assert response["error_type"] == expected_error
    assert response["preview_only"] is True
    assert response["no_write"] is True
    assert response["affects_score"] is False


def test_configured_model_takes_priority_over_tags_client() -> None:
    model = select_local_ollama_model(
        configured_model=" qwen3-next:80b-a3b-instruct-q8_0 ",
        tags_client=_fail_tags_client,
    )

    assert model == "qwen3-next:80b-a3b-instruct-q8_0"


def test_select_local_ollama_model_uses_first_fake_tag() -> None:
    model = select_local_ollama_model(tags_client=lambda: ["local-a", "local-b"])

    assert model == "local-a"


def test_select_local_ollama_model_returns_none_when_fake_tags_are_empty() -> None:
    assert select_local_ollama_model(tags_client=lambda: []) is None


def test_fetch_local_ollama_models_uses_loopback_tags_endpoint_with_fake_transport() -> None:
    calls: list[tuple[str, str, float]] = []

    def fake_transport(request, timeout: float) -> _FakeHttpResponse:
        calls.append((request.full_url, request.get_method(), timeout))
        return _FakeHttpResponse(
            b'{"models":[{"name":"local-a"},{"name":""},{"digest":"ignored"}]}'
        )

    models = fetch_local_ollama_models(timeout_seconds=2.0, transport=fake_transport)

    assert models == ["local-a"]
    assert calls == [(f"{OLLAMA_LOCAL_BASE_URL}/api/tags", "GET", 2.0)]


def test_real_ollama_preview_client_posts_generate_request_with_fake_transport() -> None:
    calls: list[dict] = []

    def fake_transport(request, timeout: float) -> _FakeHttpResponse:
        calls.append(
            {
                "url": request.full_url,
                "method": request.get_method(),
                "timeout": timeout,
                "body": json.loads(request.data.decode("utf-8")),
            }
        )
        return _FakeHttpResponse(b'{"response":"OK","done":true}')

    client = build_real_ollama_preview_client(
        timeout_seconds=3.0,
        num_predict=8,
        transport=fake_transport,
    )
    response = client(
        {
            "model": "local-a",
            "messages": [{"role": "user", "content": "Return OK only."}],
        }
    )

    assert response == {"response": "OK", "done": True}
    assert calls == [
        {
            "url": f"{OLLAMA_LOCAL_BASE_URL}/api/generate",
            "method": "POST",
            "timeout": 3.0,
            "body": {
                "model": "local-a",
                "prompt": "Return OK only.",
                "stream": False,
                "options": {"num_predict": 8},
            },
        }
    ]


def test_real_client_fake_unreachable_returns_ollama_unreachable() -> None:
    def fake_transport(*_: object, **__: object) -> _FakeHttpResponse:
        raise adapter.urllib_error.URLError(ConnectionRefusedError("refused"))

    response = run_ollama_preview(
        feature_flag_value="true",
        prompt=_valid_prompt(),
        model=_valid_model(),
        client=build_real_ollama_preview_client(transport=fake_transport),
    )

    assert response["status"] == "error"
    assert response["error_type"] == "ollama_unreachable"
    assert response["preview_only"] is True
    assert response["no_write"] is True
    assert response["affects_score"] is False


def test_real_client_fake_timeout_returns_timeout() -> None:
    def fake_transport(*_: object, **__: object) -> _FakeHttpResponse:
        raise adapter.urllib_error.URLError(TimeoutError("slow"))

    response = run_ollama_preview(
        feature_flag_value="true",
        prompt=_valid_prompt(),
        model=_valid_model(),
        client=build_real_ollama_preview_client(transport=fake_transport),
    )

    assert response["status"] == "error"
    assert response["error_type"] == "timeout"
    assert response["preview_only"] is True
    assert response["no_write"] is True
    assert response["affects_score"] is False


def test_real_client_fake_invalid_json_returns_invalid_response() -> None:
    def fake_transport(*_: object, **__: object) -> _FakeHttpResponse:
        return _FakeHttpResponse(b"not-json")

    response = run_ollama_preview(
        feature_flag_value="true",
        prompt=_valid_prompt(),
        model=_valid_model(),
        client=build_real_ollama_preview_client(transport=fake_transport),
    )

    assert response["status"] == "error"
    assert response["error_type"] == "invalid_response"
    assert response["preview_only"] is True
    assert response["no_write"] is True
    assert response["affects_score"] is False


@pytest.mark.parametrize("raw_response", [None, {}, {"message": {}}, {"content": " "}])
def test_invalid_response_returns_stable_failure(raw_response: dict | None) -> None:
    response = normalize_ollama_response(raw_response, model=_valid_model())

    assert response["status"] == "error"
    assert response["error_type"] == "invalid_response"
    assert response["message"] == "Ollama response did not contain non-empty content."
    assert response["preview_only"] is True
    assert response["no_write"] is True
    assert response["affects_score"] is False


def test_same_input_and_fake_response_are_deterministic() -> None:
    kwargs = {
        "feature_flag_value": "true",
        "prompt": _valid_prompt(),
        "model": _valid_model(),
        "client": lambda _: _valid_response(),
        "metadata": {"request_id": "r1"},
    }

    first = run_ollama_preview(**kwargs)
    second = run_ollama_preview(**kwargs)

    assert first == second


@pytest.mark.parametrize(
    ("prompt", "model", "metadata", "expected_error", "expected_message"),
    [
        ("", _valid_model(), None, "invalid_request", "prompt must be a non-empty string."),
        (None, _valid_model(), None, "invalid_request", "prompt must be a non-empty string."),
        (
            _valid_prompt(),
            "",
            None,
            "model_unavailable",
            "model must be configured before Ollama preview.",
        ),
        (
            _valid_prompt(),
            _valid_model(),
            {"nested": {"final_score": 88}},
            "invalid_request",
            "forbidden key: final_score",
        ),
    ],
)
def test_invalid_inputs_return_stable_error_structure(
    prompt: str | None,
    model: str,
    metadata: dict | None,
    expected_error: str,
    expected_message: str,
) -> None:
    response = run_ollama_preview(
        feature_flag_value="true",
        prompt=prompt,
        model=model,
        metadata=metadata,
        client=_fail_client,
    )

    assert response["status"] == "error"
    assert response["error_type"] == expected_error
    assert response["message"] == expected_message
    assert response["preview_only"] is True
    assert response["no_write"] is True
    assert response["affects_score"] is False


def test_model_unavailable_without_client_returns_stable_failure() -> None:
    response = run_ollama_preview(
        feature_flag_value="true",
        prompt=_valid_prompt(),
        model=_valid_model(),
        client=None,
    )

    assert response["status"] == "error"
    assert response["error_type"] == "model_unavailable"
    assert response["preview_only"] is True
    assert response["no_write"] is True
    assert response["affects_score"] is False


def test_build_failure_response_is_preview_only_no_write() -> None:
    response = build_failure_response(
        "transport_failure", "transport failed", prompt="abc", model="m1"
    )

    assert response["adapter"] == "ollama_preview"
    assert response["source"] == "ollama_preview_adapter"
    assert response["preview_only"] is True
    assert response["no_write"] is True
    assert response["affects_score"] is False
    assert response["fallback"]["prompt_excerpt"] == "abc"


def test_adapter_source_does_not_import_forbidden_modules_or_paths() -> None:
    source = inspect.getsource(adapter)
    forbidden_fragments = {
        "app.main",
        "app.storage",
        "app.engine.scorer",
        "app.engine.v2_scorer",
        "app.engine.local_llm_preview_mock",
        "llm_evolution_openai",
        "llm_evolution_spark",
        "llm_evolution_gemini",
        "requests",
        "httpx",
        "subprocess",
        "open(",
        "Path(",
        "score_text(",
        "rescore(",
        "qingtian-results",
        "evidence_trace/latest",
        "scoring_basis/latest",
        "data/",
        "output/",
    }

    for fragment in forbidden_fragments:
        assert fragment not in source
    assert 'OLLAMA_LOCAL_BASE_URL = "http://127.0.0.1:11434"' in source
    assert "0.0.0.0" not in source
