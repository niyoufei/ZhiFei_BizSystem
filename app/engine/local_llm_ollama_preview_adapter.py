"""Default-off preview-only adapter boundary for local Ollama calls."""

from __future__ import annotations

import json
import math
import os
import socket
from copy import deepcopy
from typing import Any, Callable, Mapping
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import urlparse

ADAPTER_NAME = "ollama_preview"
ADAPTER_SOURCE = "ollama_preview_adapter"
FEATURE_FLAG_NAME = "LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED"
REAL_TRANSPORT_FEATURE_FLAG_NAME = "LOCAL_LLM_OLLAMA_REAL_TRANSPORT_ENABLED"
MODEL_ENV_NAME = "LOCAL_LLM_OLLAMA_MODEL"
TIMEOUT_ENV_NAME = "LOCAL_LLM_OLLAMA_TIMEOUT_SECONDS"
NUM_PREDICT_ENV_NAME = "LOCAL_LLM_OLLAMA_NUM_PREDICT"
TRUE_VALUES = {"true", "1", "yes", "on"}
DEFAULT_TIMEOUT_SECONDS = 5.0
MAX_TIMEOUT_SECONDS = 60.0
DEFAULT_GENERATE_NUM_PREDICT = 128
MAX_GENERATE_NUM_PREDICT = 128
OLLAMA_LOCAL_BASE_URL = "http://127.0.0.1:11434"
PROMPT_EXCERPT_LIMIT = 200
RESPONSE_SUMMARY_LIMIT = 500

FORBIDDEN_EXACT_KEYS = {
    "final_score",
    "score_result",
    "write_result",
    "persist",
    "export",
    "apply",
    "rescore",
    "qingtian_results",
    "evidence_trace_write",
    "scoring_basis_write",
    "storage_write",
    "score_text",
    "openai",
    "spark",
    "gemini",
}

OllamaPreviewClient = Callable[[dict[str, Any]], Mapping[str, Any] | None]
JsonTransport = Callable[..., Any]


class OllamaUnreachableError(ConnectionError):
    """Raised when the local Ollama loopback endpoint cannot be reached."""


class OllamaModelUnavailableError(Exception):
    """Raised when no local model can satisfy a preview request."""


class OllamaInvalidResponseError(ValueError):
    """Raised when the local Ollama response cannot be normalized."""


def is_ollama_preview_enabled(value: str | None) -> bool:
    """Return whether the explicit adapter feature flag enables preview calls."""
    normalized = str(value or "").strip().lower()
    return normalized in TRUE_VALUES


def is_ollama_real_transport_enabled(value: str | None) -> bool:
    """Return whether the explicit real transport flag permits localhost calls."""
    normalized = str(value or "").strip().lower()
    return normalized in TRUE_VALUES


def parse_ollama_timeout_seconds(value: Any) -> float:
    """Parse a bounded timeout value for preview-only Ollama transport."""
    if value is None:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SECONDS
    if not math.isfinite(parsed) or parsed <= 0:
        return DEFAULT_TIMEOUT_SECONDS
    return min(MAX_TIMEOUT_SECONDS, parsed)


def get_ollama_timeout_seconds(
    environ_get: Callable[[str], str | None] | None = None,
) -> float:
    """Read the optional timeout environment setting with safe bounds."""
    getter = environ_get or os.getenv
    return parse_ollama_timeout_seconds(getter(TIMEOUT_ENV_NAME))


def parse_ollama_num_predict(value: Any) -> int:
    """Parse a bounded generation limit for preview-only Ollama transport."""
    if value is None:
        return DEFAULT_GENERATE_NUM_PREDICT
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return DEFAULT_GENERATE_NUM_PREDICT
    if parsed <= 0:
        return DEFAULT_GENERATE_NUM_PREDICT
    return min(MAX_GENERATE_NUM_PREDICT, parsed)


def get_ollama_num_predict(
    environ_get: Callable[[str], str | None] | None = None,
) -> int:
    """Read the optional num_predict environment setting with safe bounds."""
    getter = environ_get or os.getenv
    return parse_ollama_num_predict(getter(NUM_PREDICT_ENV_NAME))


def validate_ollama_preview_boundary(
    *,
    prompt: str | None,
    model: str | None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Validate preview-only input before any transport is selected."""
    return _validate_preview_request(prompt=prompt, model=model, metadata=metadata)


def build_disabled_response(
    *,
    feature_flag: str = FEATURE_FLAG_NAME,
) -> dict[str, Any]:
    """Build the stable default-off response without calling transport."""
    return _base_response(
        status="disabled",
        reason="feature_flag_disabled",
        feature_flag=feature_flag,
        enabled=False,
    )


def build_failure_response(
    error_type: str,
    message: str,
    *,
    model: str | None = None,
    prompt: str | None = None,
    fallback_used: bool = True,
) -> dict[str, Any]:
    """Build a stable preview-only failure response."""
    response = _base_response(
        status="error",
        error_type=error_type,
        message=message,
        model=_normalize_optional_text(model),
        fallback_used=fallback_used,
    )
    if fallback_used:
        response["fallback"] = _build_mock_fallback(prompt=prompt, model=model, reason=error_type)
    return response


def normalize_ollama_response(
    response: Mapping[str, Any] | None,
    *,
    model: str,
) -> dict[str, Any]:
    """Normalize a mocked Ollama response into a stable preview-only payload."""
    content = _extract_response_content(response)
    if content is None:
        return build_failure_response(
            "invalid_response",
            "Ollama response did not contain non-empty content.",
            model=model,
        )

    return _base_response(
        status="ok",
        reason="ok",
        enabled=True,
        model=model,
        advisory={
            "summary": content[:RESPONSE_SUMMARY_LIMIT],
            "boundary": {
                "preview_only": True,
                "no_write": True,
                "affects_score": False,
            },
        },
        raw_response_included=False,
    )


def run_ollama_preview(
    *,
    feature_flag_value: str | None,
    prompt: str | None = None,
    model: str | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    client: OllamaPreviewClient | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a preview-only adapter call through an injected client."""
    if not is_ollama_preview_enabled(feature_flag_value):
        return build_disabled_response()

    validation_error = _validate_preview_request(prompt=prompt, model=model, metadata=metadata)
    if validation_error is not None:
        return validation_error

    normalized_prompt = str(prompt).strip()
    normalized_model = str(model).strip()

    if client is None:
        return build_failure_response(
            "model_unavailable",
            "Ollama preview client is not configured.",
            model=normalized_model,
            prompt=normalized_prompt,
        )

    request = {
        "model": normalized_model,
        "messages": [{"role": "user", "content": normalized_prompt}],
        "stream": False,
        "timeout_seconds": timeout_seconds,
        "metadata": deepcopy(dict(metadata or {})),
    }

    try:
        response = client(request)
    except OllamaUnreachableError:
        return build_failure_response(
            "ollama_unreachable",
            "Local Ollama service is unreachable.",
            model=normalized_model,
            prompt=normalized_prompt,
        )
    except OllamaModelUnavailableError:
        return build_failure_response(
            "model_unavailable",
            "Ollama model is unavailable.",
            model=normalized_model,
            prompt=normalized_prompt,
        )
    except OllamaInvalidResponseError:
        return build_failure_response(
            "invalid_response",
            "Ollama response did not contain non-empty content.",
            model=normalized_model,
            prompt=normalized_prompt,
        )
    except (TimeoutError, socket.timeout):
        return build_failure_response(
            "timeout",
            "Ollama preview request timed out.",
            model=normalized_model,
            prompt=normalized_prompt,
        )
    except ConnectionError:
        return build_failure_response(
            "model_unavailable",
            "Ollama model or service is unavailable.",
            model=normalized_model,
            prompt=normalized_prompt,
        )
    except OSError:
        return build_failure_response(
            "transport_failure",
            "Ollama preview transport failed.",
            model=normalized_model,
            prompt=normalized_prompt,
        )

    return normalize_ollama_response(response, model=normalized_model)


def select_local_ollama_model(
    *,
    configured_model: str | None = None,
    tags_client: Callable[[], list[str]] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> str | None:
    """Resolve a preview model from an explicit value or local tags."""
    explicit_model = _normalize_optional_text(configured_model)
    if explicit_model:
        return explicit_model

    client = tags_client or (lambda: fetch_local_ollama_models(timeout_seconds=timeout_seconds))
    models = client()
    for model in models:
        normalized_model = _normalize_optional_text(model)
        if normalized_model:
            return normalized_model
    return None


def fetch_local_ollama_models(
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    base_url: str = OLLAMA_LOCAL_BASE_URL,
    transport: JsonTransport | None = None,
) -> list[str]:
    """Read local Ollama tags from the loopback-only endpoint."""
    payload = _send_json_request(
        _build_local_ollama_url(base_url, "/api/tags"),
        method="GET",
        body=None,
        timeout_seconds=timeout_seconds,
        transport=transport,
    )
    models = payload.get("models") if isinstance(payload, Mapping) else None
    if not isinstance(models, list):
        raise OllamaInvalidResponseError("Ollama tags response did not contain models.")

    names: list[str] = []
    for item in models:
        if isinstance(item, Mapping):
            name = _normalize_optional_text(item.get("name"))
            if name:
                names.append(name)
    return names


def build_real_ollama_preview_client(
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    base_url: str = OLLAMA_LOCAL_BASE_URL,
    num_predict: int = DEFAULT_GENERATE_NUM_PREDICT,
    transport: JsonTransport | None = None,
) -> OllamaPreviewClient:
    """Build a loopback-only Ollama generate client without touching storage."""
    generate_url = _build_local_ollama_url(base_url, "/api/generate")
    prediction_limit = _bounded_num_predict(num_predict)

    def client(request: dict[str, Any]) -> Mapping[str, Any] | None:
        if not isinstance(request, Mapping):
            raise OllamaInvalidResponseError("preview request must be a mapping.")
        model = _normalize_optional_text(request.get("model"))
        if not model:
            raise OllamaModelUnavailableError("model must be configured.")
        prompt = _extract_request_prompt(request)
        if not prompt:
            raise OllamaInvalidResponseError("prompt must be a non-empty string.")

        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": prediction_limit},
        }
        return _send_json_request(
            generate_url,
            method="POST",
            body=payload,
            timeout_seconds=timeout_seconds,
            transport=transport,
        )

    return client


def _base_response(status: str, **fields: Any) -> dict[str, Any]:
    response = {
        "adapter": ADAPTER_NAME,
        "source": ADAPTER_SOURCE,
        "status": status,
        "preview_only": True,
        "no_write": True,
        "affects_score": False,
    }
    response.update(fields)
    return response


def _validate_preview_request(
    *,
    prompt: str | None,
    model: str | None,
    metadata: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    forbidden_keys = sorted(set(_find_forbidden_keys(metadata or {})))
    if forbidden_keys:
        return build_failure_response(
            "invalid_request",
            f"forbidden key: {forbidden_keys[0]}",
            model=model,
            prompt=prompt,
            fallback_used=False,
        )

    if not isinstance(prompt, str) or not prompt.strip():
        return build_failure_response(
            "invalid_request",
            "prompt must be a non-empty string.",
            model=model,
            prompt=prompt,
            fallback_used=False,
        )

    if not isinstance(model, str) or not model.strip():
        return build_failure_response(
            "model_unavailable",
            "model must be configured before Ollama preview.",
            model=model,
            prompt=prompt,
        )

    return None


def _find_forbidden_keys(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key) in FORBIDDEN_EXACT_KEYS:
                found.append(str(key))
            found.extend(_find_forbidden_keys(item))
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            found.extend(_find_forbidden_keys(item))
    return found


def _extract_response_content(response: Mapping[str, Any] | None) -> str | None:
    if not isinstance(response, Mapping):
        return None

    candidates = [
        response.get("content"),
        response.get("response"),
    ]

    message = response.get("message")
    if isinstance(message, Mapping):
        candidates.append(message.get("content"))

    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _extract_request_prompt(request: Mapping[str, Any]) -> str | None:
    messages = request.get("messages")
    if isinstance(messages, list) and messages:
        first = messages[0]
        if isinstance(first, Mapping):
            content = first.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
    prompt = request.get("prompt")
    if isinstance(prompt, str) and prompt.strip():
        return prompt.strip()
    return None


def _build_local_ollama_url(base_url: str, path: str) -> str:
    return f"{_validate_local_ollama_base_url(base_url)}{path}"


def _validate_local_ollama_base_url(base_url: str) -> str:
    parsed = urlparse(str(base_url or "").strip())
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.port != 11434
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Ollama preview transport must target http://127.0.0.1:11434.")
    path = (parsed.path or "").rstrip("/")
    if path:
        raise ValueError("Ollama preview transport base URL must not include a path.")
    return OLLAMA_LOCAL_BASE_URL


def _send_json_request(
    url: str,
    *,
    method: str,
    body: Mapping[str, Any] | None,
    timeout_seconds: float,
    transport: JsonTransport | None,
) -> Mapping[str, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
        headers["Content-Type"] = "application/json"

    http_request = urllib_request.Request(url, data=data, headers=headers, method=method)
    sender = transport or urllib_request.urlopen
    try:
        with sender(http_request, timeout=timeout_seconds) as http_response:
            status = int(getattr(http_response, "status", getattr(http_response, "code", 200)))
            raw_body = http_response.read()
    except urllib_error.HTTPError as exc:
        if int(getattr(exc, "code", 0)) == 404:
            raise OllamaModelUnavailableError("Ollama model was not found.") from exc
        raise OSError(f"Ollama HTTP error: {getattr(exc, 'code', 'unknown')}") from exc
    except urllib_error.URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, (TimeoutError, socket.timeout)):
            raise TimeoutError("Ollama request timed out.") from exc
        raise OllamaUnreachableError("Local Ollama service is unreachable.") from exc
    except socket.timeout as exc:
        raise TimeoutError("Ollama request timed out.") from exc

    if status == 404:
        raise OllamaModelUnavailableError("Ollama model was not found.")
    if status < 200 or status >= 300:
        raise OSError(f"Ollama HTTP status: {status}")

    try:
        parsed_body = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OllamaInvalidResponseError("Ollama response was not valid JSON.") from exc
    if not isinstance(parsed_body, Mapping):
        raise OllamaInvalidResponseError("Ollama response JSON must be an object.")
    return parsed_body


def _bounded_num_predict(value: int) -> int:
    return parse_ollama_num_predict(value)


def _build_mock_fallback(
    *,
    prompt: str | None,
    model: str | None,
    reason: str,
) -> dict[str, Any]:
    normalized_prompt = _normalize_optional_text(prompt)
    return {
        "mode": "mock_fallback",
        "reason": reason,
        "model": _normalize_optional_text(model),
        "prompt_excerpt": normalized_prompt[:PROMPT_EXCERPT_LIMIT] if normalized_prompt else "",
        "preview_only": True,
        "no_write": True,
        "affects_score": False,
    }


def _normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None
