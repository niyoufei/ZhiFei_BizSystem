from __future__ import annotations

import inspect
from copy import deepcopy

import pytest

import app.engine.local_llm_preview_mock as local_llm_preview_mock
from app.engine.local_llm_preview_mock import (
    FORBIDDEN_EXACT_KEYS,
    build_local_llm_mock_response,
    build_local_llm_preview_input,
    validate_local_llm_preview_boundary,
)


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


def _collect_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            keys.add(str(key))
            keys.update(_collect_keys(item))
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            keys.update(_collect_keys(item))
    return keys


def test_valid_payload_builds_preview_input() -> None:
    payload = _valid_payload()

    preview_input = build_local_llm_preview_input(payload)

    assert preview_input["project_id"] == "p1"
    assert preview_input["submission_id"] == "s1"
    assert preview_input["text_excerpt"] == "sample tender response excerpt"
    assert preview_input["mode"] == "preview_only"
    assert preview_input["requested_by"] == "operator"
    assert preview_input["scoring_context"] == {"dimension": "technical"}
    assert preview_input["evidence_context"] == {"source": "excerpt"}
    assert preview_input["requirement_hits"] == [{"requirement": "R1", "hit": True}]


def test_valid_preview_input_builds_mock_response() -> None:
    preview_input = build_local_llm_preview_input(_valid_payload())

    response = build_local_llm_mock_response(preview_input)

    assert response["mode"] == "mock_only"
    assert response["preview_only"] is True
    assert response["no_write"] is True
    assert response["affects_score"] is False
    assert response["source"] == "local_llm_preview_mock"
    assert response["preview_input"] == preview_input
    assert response["advisory"]["boundary"] == {
        "mock_only": True,
        "preview_only": True,
        "no_write": True,
        "affects_score": False,
    }


def test_input_object_is_not_modified() -> None:
    payload = _valid_payload()
    original = deepcopy(payload)

    preview_input = build_local_llm_preview_input(payload)
    preview_input["requirement_hits"][0]["hit"] = False
    build_local_llm_mock_response(payload)

    assert payload == original


def test_missing_required_field_raises() -> None:
    payload = _valid_payload()
    payload.pop("submission_id")

    with pytest.raises(ValueError, match="missing required field: submission_id"):
        build_local_llm_preview_input(payload)


def test_non_dict_input_raises() -> None:
    with pytest.raises(ValueError, match="payload must be a dict"):
        validate_local_llm_preview_boundary(["not", "a", "dict"])  # type: ignore[arg-type]


def test_illegal_mode_raises() -> None:
    payload = _valid_payload()
    payload["mode"] = "live_model"

    with pytest.raises(ValueError, match="mode must be mock_only or preview_only"):
        build_local_llm_preview_input(payload)


def test_forbidden_top_level_key_raises() -> None:
    payload = _valid_payload()
    payload["final_score"] = 88

    with pytest.raises(ValueError, match="forbidden key: final_score"):
        build_local_llm_preview_input(payload)


def test_forbidden_nested_key_raises() -> None:
    payload = _valid_payload()
    payload["scoring_context"] = {"nested": {"storage_write": True}}

    with pytest.raises(ValueError, match="forbidden key: storage_write"):
        build_local_llm_preview_input(payload)


def test_response_does_not_contain_forbidden_exact_keys() -> None:
    response = build_local_llm_mock_response(build_local_llm_preview_input(_valid_payload()))

    assert _collect_keys(response).isdisjoint(FORBIDDEN_EXACT_KEYS)


def test_helper_does_not_import_storage_scoring_or_real_model_modules() -> None:
    source = inspect.getsource(local_llm_preview_mock)
    forbidden_source_fragments = {
        "app.storage",
        "app.engine.scorer",
        "app.engine.v2_scorer",
        "llm_evolution_ollama",
        "llm_evolution_openai",
        "llm_evolution_spark",
        "llm_evolution_gemini",
        "urllib",
        "requests",
        "httpx",
        "subprocess",
        "open(",
        "Path(",
    }

    for fragment in forbidden_source_fragments:
        assert fragment not in source
