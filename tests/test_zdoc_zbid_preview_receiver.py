from __future__ import annotations

import inspect
from copy import deepcopy

import app.engine.zdoc_zbid_preview_receiver as zdoc_zbid_preview_receiver
from app.engine.zdoc_zbid_preview_receiver import (
    FORMAL_CHAIN_FLAGS,
    receive_zdoc_zbid_preview_payload,
)


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


def test_valid_payload_is_accepted_and_normalized_preview_only() -> None:
    result = receive_zdoc_zbid_preview_payload(_valid_payload())

    assert result["status"] == "accepted_preview_only"
    assert result["receiver_accepted"] is True
    assert result["preview_only"] is True
    assert result["no_write"] is True
    assert result["no_evidence"] is True
    assert result["produces_evidence"] is False
    assert result["produces_writeback"] is False
    assert result["writes_storage"] is False
    assert result["writes_scoring_basis"] is False
    assert result["calls_external_endpoint"] is False


def test_preview_packet_validator_result_and_blocked_reasons_are_readable() -> None:
    payload = _valid_payload()

    result = receive_zdoc_zbid_preview_payload(payload)

    assert result["preview_packet"] == payload["preview_packet"]
    assert result["validator_result"] == payload["validator_result"]
    assert result["blocked_reasons"] == payload["blocked_reasons"]


def test_input_payload_is_not_modified() -> None:
    payload = _valid_payload()
    original = deepcopy(payload)

    result = receive_zdoc_zbid_preview_payload(payload)
    result["preview_packet"]["preview"]["summary"] = "changed"

    assert payload == original


def test_formal_chain_flags_are_always_false_in_output() -> None:
    result = receive_zdoc_zbid_preview_payload(_valid_payload())

    for flag in FORMAL_CHAIN_FLAGS:
        assert result[flag] is False
        assert result["formal_chain_flags"][flag] is False


def test_true_formal_chain_flag_is_blocked_without_enabling_output_flag() -> None:
    payload = _valid_payload()
    payload["zbid_writeback_called"] = True

    result = receive_zdoc_zbid_preview_payload(payload)

    assert result["status"] == "blocked_preview_only"
    assert result["receiver_accepted"] is False
    assert result["zbid_writeback_called"] is False
    assert result["formal_chain_flags"]["zbid_writeback_called"] is False
    assert "formal_chain_flag_must_be_false:zbid_writeback_called" in result["blocked_reasons"]


def test_missing_required_field_returns_preview_only_no_write_error() -> None:
    payload = _valid_payload()
    payload.pop("validator_result")

    result = receive_zdoc_zbid_preview_payload(payload)

    assert result["status"] == "blocked_preview_only"
    assert result["preview_only"] is True
    assert result["no_write"] is True
    assert result["no_evidence"] is True
    assert "missing_required_field:validator_result" in result["blocked_reasons"]


def test_unexpected_top_level_field_is_blocked() -> None:
    payload = _valid_payload()
    payload["formal_writeback"] = {"enabled": True}

    result = receive_zdoc_zbid_preview_payload(payload)

    assert result["status"] == "blocked_preview_only"
    assert "unexpected_field:formal_writeback" in result["blocked_reasons"]


def test_nested_evidence_or_writeback_keys_are_blocked() -> None:
    payload = _valid_payload()
    payload["preview_packet"] = {"evidence": {"source": "advisory"}}

    result = receive_zdoc_zbid_preview_payload(payload)

    assert result["status"] == "blocked_preview_only"
    assert "forbidden_formal_or_evidence_key:evidence" in result["blocked_reasons"]
    assert result["produces_evidence"] is False


def test_receiver_result_does_not_create_evidence_or_writeback_payloads() -> None:
    result = receive_zdoc_zbid_preview_payload(_valid_payload())

    keys = _collect_keys(result)
    assert "evidence" not in keys
    assert "formal_evidence" not in keys
    assert "evidence_units" not in keys
    assert "writeback" not in keys
    assert "storage_write" not in keys
    assert "scoring_basis_write" not in keys


def test_receiver_helper_does_not_import_service_io_or_formal_chain_modules() -> None:
    source = inspect.getsource(zdoc_zbid_preview_receiver)
    forbidden_source_fragments = {
        "app.main",
        "app.storage",
        "app.engine.scorer",
        "app.engine.v2_scorer",
        "app.engine.evidence",
        "app.engine.evidence_units",
        "app.engine.docx_exporter",
        "requests",
        "httpx",
        "urllib",
        "socket",
        "subprocess",
        "open(",
        "Path(",
    }

    for fragment in forbidden_source_fragments:
        assert fragment not in source
