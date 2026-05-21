from __future__ import annotations

import inspect
from copy import deepcopy

from fastapi.testclient import TestClient

import app.main as app_main

PATH = "/local-llm/zdoc-preview-only/receive"


def _client() -> TestClient:
    return TestClient(app_main.app)


def _valid_payload() -> dict:
    return {
        "preview_packet": {
            "source": "zdoc",
            "preview": {"summary": "preview-only metadata"},
            "advisory": {"summary": "human review advisory only"},
        },
        "validator_result": {
            "status": "accepted_preview_only",
            "accepted_preview_only": True,
        },
        "blocked_reasons": [
            "preview_only_is_not_writeback_permission",
            "preview_only_is_not_evidence",
        ],
        "generate_called": False,
        "export_docx_called": False,
        "review_apply_called": False,
        "zbid_writeback_called": False,
        "output_job_export_written": False,
    }


def _fail_call(*args, **kwargs):
    raise AssertionError("forbidden formal-chain call")


def _patch_forbidden_runtime_paths(monkeypatch) -> None:
    for name in (
        "ensure_data_dirs",
        "save_score_reports",
        "save_submissions",
        "save_qingtian_results",
        "save_evidence_units",
        "save_learning_profiles",
        "score_text",
        "score_text_v2",
        "record_score",
        "record_history_score",
        "build_compare_narrative",
        "preview_evolution_report_with_ollama",
        "enhance_evolution_report_with_llm",
    ):
        monkeypatch.setattr(app_main, name, _fail_call, raising=False)


def test_endpoint_exists_as_preview_only_receive_route() -> None:
    routes = {
        (route.path, tuple(sorted(route.methods or ())))
        for route in app_main.app.routes
        if getattr(route, "path", None) == PATH
    }

    assert (PATH, ("POST",)) in routes


def test_valid_payload_returns_preview_only_no_write_no_evidence(monkeypatch) -> None:
    _patch_forbidden_runtime_paths(monkeypatch)

    response = _client().post(PATH, json=_valid_payload())

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "accepted_preview_only"
    assert data["receiver_accepted"] is True
    assert data["preview_only"] is True
    assert data["no_write"] is True
    assert data["no_evidence"] is True
    assert data["preview_packet"] == _valid_payload()["preview_packet"]
    assert data["validator_result"] == _valid_payload()["validator_result"]
    assert data["blocked_reasons"] == _valid_payload()["blocked_reasons"]


def test_endpoint_returns_five_false_formal_chain_flags(monkeypatch) -> None:
    _patch_forbidden_runtime_paths(monkeypatch)

    response = _client().post(PATH, json=_valid_payload())

    assert response.status_code == 200
    data = response.json()
    for flag in (
        "generate_called",
        "export_docx_called",
        "review_apply_called",
        "zbid_writeback_called",
        "output_job_export_written",
    ):
        assert data[flag] is False
        assert data["formal_chain_flags"][flag] is False


def test_true_formal_chain_flag_returns_blocked_without_enabling_output_flag(
    monkeypatch,
) -> None:
    _patch_forbidden_runtime_paths(monkeypatch)
    payload = _valid_payload()
    payload["export_docx_called"] = True

    response = _client().post(PATH, json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "blocked_preview_only"
    assert data["receiver_accepted"] is False
    assert data["export_docx_called"] is False
    assert "formal_chain_flag_must_be_false:export_docx_called" in data["blocked_reasons"]


def test_missing_key_returns_preview_only_no_write_error(monkeypatch) -> None:
    _patch_forbidden_runtime_paths(monkeypatch)
    payload = _valid_payload()
    payload.pop("preview_packet")

    response = _client().post(PATH, json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "blocked_preview_only"
    assert data["preview_only"] is True
    assert data["no_write"] is True
    assert data["no_evidence"] is True
    assert "missing_required_field:preview_packet" in data["blocked_reasons"]


def test_endpoint_delegates_only_to_preview_receiver(monkeypatch) -> None:
    _patch_forbidden_runtime_paths(monkeypatch)
    calls: list[dict] = []

    def fake_receive(payload: dict) -> dict:
        calls.append(deepcopy(payload))
        return {
            "status": "accepted_preview_only",
            "preview_only": True,
            "no_write": True,
            "no_evidence": True,
            "preview_packet": payload["preview_packet"],
            "validator_result": payload["validator_result"],
            "blocked_reasons": payload["blocked_reasons"],
            "generate_called": False,
            "export_docx_called": False,
            "review_apply_called": False,
            "zbid_writeback_called": False,
            "output_job_export_written": False,
            "produces_evidence": False,
            "produces_writeback": False,
            "writes_storage": False,
            "writes_scoring_basis": False,
            "calls_external_endpoint": False,
        }

    monkeypatch.setattr(
        app_main.zdoc_zbid_preview_receiver,
        "receive_zdoc_zbid_preview_payload",
        fake_receive,
    )
    payload = _valid_payload()

    response = _client().post(PATH, json=payload)

    assert response.status_code == 200
    assert calls == [payload]
    assert response.json()["status"] == "accepted_preview_only"


def test_endpoint_does_not_return_evidence_writeback_or_storage_effects(monkeypatch) -> None:
    _patch_forbidden_runtime_paths(monkeypatch)

    response = _client().post(PATH, json=_valid_payload())

    assert response.status_code == 200
    data = response.json()
    assert data["produces_evidence"] is False
    assert data["produces_writeback"] is False
    assert data["writes_storage"] is False
    assert data["writes_scoring_basis"] is False
    assert data["calls_external_endpoint"] is False


def test_endpoint_source_does_not_reference_formal_chain_routes_or_helpers() -> None:
    source = inspect.getsource(app_main.zdoc_zbid_preview_only_receive_api)
    forbidden_fragments = {
        "/generate",
        "/export_docx",
        "/review/apply",
        "score_text",
        "score_text_v2",
        "save_",
        "evidence",
        "docx",
        "storage",
        "writeback",
        "requests",
        "httpx",
        "urllib",
        "socket",
        "subprocess",
    }

    for fragment in forbidden_fragments:
        assert fragment not in source
