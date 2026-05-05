"""Pure mock-only helpers for local LLM preview payloads."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

REQUIRED_FIELDS = ("project_id", "submission_id", "text_excerpt", "mode", "requested_by")
OPTIONAL_FIELDS = ("scoring_context", "evidence_context", "requirement_hits")
ALLOWED_MODES = {"mock_only", "preview_only"}
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
    "ollama",
    "openai",
    "spark",
    "gemini",
}


def _find_forbidden_keys(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in FORBIDDEN_EXACT_KEYS:
                found.append(str(key))
            found.extend(_find_forbidden_keys(item))
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            found.extend(_find_forbidden_keys(item))
    return found


def _require_non_empty(payload: dict[str, Any], field: str) -> None:
    if field not in payload:
        raise ValueError(f"missing required field: {field}")
    value = payload[field]
    if value is None:
        raise ValueError(f"missing required field: {field}")
    if isinstance(value, str) and not value.strip():
        raise ValueError(f"missing required field: {field}")


def validate_local_llm_preview_boundary(payload: dict) -> None:
    """Validate preview/mock payload boundaries without side effects."""
    if not isinstance(payload, dict):
        raise ValueError("payload must be a dict")

    forbidden_keys = sorted(set(_find_forbidden_keys(payload)))
    if forbidden_keys:
        raise ValueError(f"forbidden key: {forbidden_keys[0]}")

    for field in REQUIRED_FIELDS:
        _require_non_empty(payload, field)

    mode = payload["mode"]
    if mode not in ALLOWED_MODES:
        raise ValueError("mode must be mock_only or preview_only")


def build_local_llm_preview_input(payload: dict) -> dict:
    """Build a deterministic local LLM preview input without mutating input."""
    validate_local_llm_preview_boundary(payload)

    return {
        "project_id": deepcopy(payload["project_id"]),
        "submission_id": deepcopy(payload["submission_id"]),
        "text_excerpt": deepcopy(payload["text_excerpt"]),
        "mode": deepcopy(payload["mode"]),
        "requested_by": deepcopy(payload["requested_by"]),
        "scoring_context": deepcopy(payload.get("scoring_context", {})),
        "evidence_context": deepcopy(payload.get("evidence_context", {})),
        "requirement_hits": deepcopy(payload.get("requirement_hits", [])),
    }


def build_local_llm_mock_response(preview_input: dict) -> dict:
    """Build a deterministic mock advisory response without model calls."""
    normalized_input = build_local_llm_preview_input(preview_input)

    advisory = {
        "summary": "Mock-only local LLM preview. No model was called.",
        "guidance": [
            "Review the text excerpt against the requirement hits.",
            "Use scoring and evidence context for human advisory review only.",
        ],
        "boundary": {
            "mock_only": True,
            "preview_only": True,
            "no_write": True,
            "affects_score": False,
        },
    }

    return {
        "mode": "mock_only",
        "preview_only": True,
        "no_write": True,
        "affects_score": False,
        "source": "local_llm_preview_mock",
        "preview_input": normalized_input,
        "advisory": advisory,
    }
