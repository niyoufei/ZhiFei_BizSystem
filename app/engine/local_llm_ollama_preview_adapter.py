"""Default-off preview-only adapter boundary for local Ollama calls."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Mapping

ADAPTER_NAME = "ollama_preview"
ADAPTER_SOURCE = "ollama_preview_adapter"
FEATURE_FLAG_NAME = "LOCAL_LLM_OLLAMA_PREVIEW_ADAPTER_ENABLED"
TRUE_VALUES = {"true", "1", "yes", "on"}
DEFAULT_TIMEOUT_SECONDS = 5.0
PROMPT_EXCERPT_LIMIT = 200

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


def is_ollama_preview_enabled(value: str | None) -> bool:
    """Return whether the explicit adapter feature flag enables preview calls."""
    normalized = str(value or "").strip().lower()
    return normalized in TRUE_VALUES


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
            "summary": content,
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
    except TimeoutError:
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
