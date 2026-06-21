"""Preview-only receiver helpers for ZDoc-to-ZBid metadata payloads."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

RECEIVER_NAME = "zdoc_zbid_preview_receiver"
REQUIRED_FIELDS = ("preview_packet", "validator_result", "blocked_reasons")
FORMAL_CHAIN_FLAGS = (
    "generate_called",
    "export_docx_called",
    "review_apply_called",
    "zbid_writeback_called",
    "output_job_export_written",
)
ALLOWED_TOP_LEVEL_FIELDS = set(REQUIRED_FIELDS).union(FORMAL_CHAIN_FLAGS)
FORBIDDEN_EXACT_KEYS = {
    "evidence",
    "evidence_units",
    "formal_evidence",
    "evidence_trace_write",
    "scoring_basis_write",
    "storage_write",
    "writeback",
    "qingtian_results",
    "final_score",
    "score_result",
    "write_result",
    "persist",
    "export",
    "apply",
    "rescore",
    "score_text",
    "export_docx",
    "review_apply",
}


def _find_forbidden_keys(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in FORBIDDEN_EXACT_KEYS:
                found.append(str(key))
            found.extend(_find_forbidden_keys(item))
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            found.extend(_find_forbidden_keys(item))
    return found


def _normalize_blocked_reasons(value: Any) -> tuple[list[str], list[str]]:
    if not isinstance(value, (list, tuple)):
        return [], ["blocked_reasons_must_be_list"]
    return [str(item) for item in value], []


def _validate_mapping_field(payload: Mapping[str, Any], field: str) -> list[str]:
    if field not in payload:
        return [f"missing_required_field:{field}"]
    if not isinstance(payload[field], Mapping):
        return [f"{field}_must_be_mapping"]
    return []


def _inspect_formal_chain_flags(
    payload: Mapping[str, Any],
) -> tuple[dict[str, bool | None], list[str]]:
    received: dict[str, bool | None] = {}
    reasons: list[str] = []
    for flag in FORMAL_CHAIN_FLAGS:
        value = payload.get(flag)
        if flag not in payload:
            received[flag] = None
            reasons.append(f"missing_formal_chain_flag:{flag}")
        elif value is False:
            received[flag] = False
        elif value is True:
            received[flag] = True
            reasons.append(f"formal_chain_flag_must_be_false:{flag}")
        else:
            received[flag] = None
            reasons.append(f"formal_chain_flag_must_be_boolean_false:{flag}")
    return received, reasons


def _formal_chain_false_flags() -> dict[str, bool]:
    return {flag: False for flag in FORMAL_CHAIN_FLAGS}


def receive_zdoc_zbid_preview_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize a ZDoc preview-only payload without evidence or writeback effects."""
    if not isinstance(payload, Mapping):
        reasons = ["payload_must_be_mapping"]
        return _build_result(
            status="blocked_preview_only",
            receiver_accepted=False,
            preview_packet=None,
            validator_result=None,
            blocked_reasons=reasons,
            receiver_blocked_reasons=reasons,
            received_formal_chain_flags={flag: None for flag in FORMAL_CHAIN_FLAGS},
        )

    receiver_blocked_reasons: list[str] = []
    unexpected_fields = sorted(set(payload) - ALLOWED_TOP_LEVEL_FIELDS)
    receiver_blocked_reasons.extend(f"unexpected_field:{field}" for field in unexpected_fields)
    receiver_blocked_reasons.extend(_validate_mapping_field(payload, "preview_packet"))
    receiver_blocked_reasons.extend(_validate_mapping_field(payload, "validator_result"))

    blocked_reasons, blocked_reason_errors = _normalize_blocked_reasons(
        payload.get("blocked_reasons")
    )
    receiver_blocked_reasons.extend(blocked_reason_errors)

    received_flags, flag_errors = _inspect_formal_chain_flags(payload)
    receiver_blocked_reasons.extend(flag_errors)

    forbidden_keys = sorted(set(_find_forbidden_keys(payload)))
    receiver_blocked_reasons.extend(
        f"forbidden_formal_or_evidence_key:{key}" for key in forbidden_keys
    )

    receiver_accepted = not receiver_blocked_reasons
    status = "accepted_preview_only" if receiver_accepted else "blocked_preview_only"
    merged_blocked_reasons = [*blocked_reasons, *receiver_blocked_reasons]

    return _build_result(
        status=status,
        receiver_accepted=receiver_accepted,
        preview_packet=deepcopy(payload.get("preview_packet")),
        validator_result=deepcopy(payload.get("validator_result")),
        blocked_reasons=merged_blocked_reasons,
        receiver_blocked_reasons=receiver_blocked_reasons,
        received_formal_chain_flags=received_flags,
    )


def _build_result(
    *,
    status: str,
    receiver_accepted: bool,
    preview_packet: Any,
    validator_result: Any,
    blocked_reasons: list[str],
    receiver_blocked_reasons: list[str],
    received_formal_chain_flags: dict[str, bool | None],
) -> dict[str, Any]:
    false_flags = _formal_chain_false_flags()
    return {
        "receiver": RECEIVER_NAME,
        "status": status,
        "receiver_accepted": receiver_accepted,
        "preview_only": True,
        "no_write": True,
        "no_evidence": True,
        "preview_packet": preview_packet,
        "validator_result": validator_result,
        "blocked_reasons": blocked_reasons,
        "receiver_blocked_reasons": receiver_blocked_reasons,
        "formal_chain_flags": false_flags,
        "received_formal_chain_flags": received_formal_chain_flags,
        "produces_evidence": False,
        "produces_writeback": False,
        "writes_storage": False,
        "writes_scoring_basis": False,
        "calls_external_endpoint": False,
        **false_flags,
    }
