from __future__ import annotations

import copy
import hashlib
import json
from typing import Dict, Optional

Record = Dict[str, object]

ASSESSMENT_CONTRACT_SCHEMA_VERSION = "assessment-contract-v1"
ASSESSMENT_CONTRACT_STATUS_CERTIFIED = "certified"
ASSESSMENT_CONTRACT_STATUS_LEGACY = "legacy_unversioned"
ASSESSMENT_CONTRACT_STATUS_INVALID = "invalid_contract"

_VOLATILE_KEYS = {
    "created_at",
    "generated_at",
    "timestamp",
    "updated_at",
}


def _mapping(value: object) -> Record:
    return copy.deepcopy(value) if isinstance(value, dict) else {}


def _semantic_snapshot(value: object) -> object:
    """Remove generation metadata that cannot affect a scoring decision."""
    if isinstance(value, dict):
        return {
            str(key): _semantic_snapshot(item)
            for key, item in value.items()
            if str(key) not in _VOLATILE_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_semantic_snapshot(item) for item in value]
    return copy.deepcopy(value)


def _contract_hash(*, schema_version: str, inputs: Record) -> str:
    canonical = json.dumps(
        {"schema_version": schema_version, "inputs": inputs},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_contract_from_inputs(inputs: Record) -> Record:
    semantic_inputs = _semantic_snapshot(inputs)
    if not isinstance(semantic_inputs, dict):
        raise TypeError("assessment contract inputs must be a mapping")
    contract_hash = _contract_hash(
        schema_version=ASSESSMENT_CONTRACT_SCHEMA_VERSION,
        inputs=semantic_inputs,
    )
    return {
        "schema_version": ASSESSMENT_CONTRACT_SCHEMA_VERSION,
        "status": ASSESSMENT_CONTRACT_STATUS_CERTIFIED,
        "hash_algorithm": "sha256",
        "contract_hash": contract_hash,
        "inputs": semantic_inputs,
    }


def _approved_tender_context(project: Record) -> Record:
    meta = project.get("meta") if isinstance(project.get("meta"), dict) else {}
    state = meta.get("tender_profile_state")
    state = state if isinstance(state, dict) else {}
    profile = state.get("profile")
    approved = bool(state.get("approved")) and isinstance(profile, dict)
    return {
        "approved": approved,
        "profile": copy.deepcopy(profile) if approved else None,
        "attention_profile": (
            copy.deepcopy(state.get("attention_profile")) if approved else None
        ),
    }


def _deployed_patch_context(deployed_patch: Optional[Record]) -> Optional[Record]:
    if not isinstance(deployed_patch, dict):
        return None
    return {
        "id": deployed_patch.get("id"),
        "patch_payload": copy.deepcopy(deployed_patch.get("patch_payload")),
    }


def _calibrator_context(calibrator_model: Optional[Record]) -> Optional[Record]:
    if not isinstance(calibrator_model, dict):
        return None
    return {
        "calibrator_version": calibrator_model.get("calibrator_version"),
        "model_type": calibrator_model.get("model_type"),
        "feature_schema_version": calibrator_model.get("feature_schema_version"),
        "train_filter": copy.deepcopy(calibrator_model.get("train_filter")),
        "model_artifact": copy.deepcopy(
            calibrator_model.get("model_artifact") or calibrator_model.get("artifact")
        ),
        "calibrator_summary": copy.deepcopy(calibrator_model.get("calibrator_summary")),
    }


def build_assessment_contract(
    *,
    project_id: str,
    project: Record,
    config_rubric: object,
    config_lexicon: object,
    multipliers: object,
    profile_snapshot: object,
    scoring_engine_version: str,
    engine_version: str,
    deployed_patch: Optional[Record],
    calibrator_model: Optional[Record] = None,
    resolved_scoring_inputs: Optional[Record] = None,
    post_processing: Optional[Record] = None,
) -> Record:
    """Freeze the exact rule context used for one scoring execution."""
    project_meta = project.get("meta") if isinstance(project.get("meta"), dict) else {}
    inputs: Record = {
        "project": {
            "id": str(project_id),
            "region": project.get("region"),
            "scoring_engine_version_locked": project.get(
                "scoring_engine_version_locked"
            ),
            "calibrator_version_locked": project.get("calibrator_version_locked"),
            "expert_profile_id": project.get("expert_profile_id"),
        },
        "engine": {
            "requested_version": str(scoring_engine_version),
            "effective_version": str(engine_version),
        },
        "base_configuration": {
            "rubric": _mapping(config_rubric),
            "lexicon": _mapping(config_lexicon),
        },
        "effective_multipliers": _mapping(multipliers),
        "expert_profile_snapshot": _mapping(profile_snapshot),
        "approved_tender_profile": _approved_tender_context(project),
        "calibration": {
            "actual_model": _calibrator_context(calibrator_model),
            "score_blend_configuration": _mapping(project_meta.get("score_blend")),
        },
        "deployed_patch": _deployed_patch_context(deployed_patch),
        "resolved_scoring_inputs": _mapping(resolved_scoring_inputs),
        "post_processing": _mapping(post_processing),
    }
    return build_contract_from_inputs(inputs)


def verify_assessment_contract(contract: object) -> bool:
    if not isinstance(contract, dict):
        return False
    if contract.get("schema_version") != ASSESSMENT_CONTRACT_SCHEMA_VERSION:
        return False
    if contract.get("hash_algorithm") != "sha256":
        return False
    inputs = contract.get("inputs")
    if not isinstance(inputs, dict):
        return False
    try:
        expected = _contract_hash(
            schema_version=ASSESSMENT_CONTRACT_SCHEMA_VERSION,
            inputs=inputs,
        )
    except (TypeError, ValueError):
        return False
    return str(contract.get("contract_hash") or "") == expected


def summarize_assessment_contract(contract: Record) -> Record:
    inputs = contract.get("inputs") if isinstance(contract.get("inputs"), dict) else {}
    project = inputs.get("project") if isinstance(inputs.get("project"), dict) else {}
    engine = inputs.get("engine") if isinstance(inputs.get("engine"), dict) else {}
    tender = (
        inputs.get("approved_tender_profile")
        if isinstance(inputs.get("approved_tender_profile"), dict)
        else {}
    )
    profile = tender.get("profile") if isinstance(tender.get("profile"), dict) else {}
    attention = (
        tender.get("attention_profile")
        if isinstance(tender.get("attention_profile"), dict)
        else {}
    )
    selection = (
        attention.get("selection_context")
        if isinstance(attention.get("selection_context"), dict)
        else {}
    )
    catalog_summary = (
        selection.get("catalog_summary")
        if isinstance(selection.get("catalog_summary"), dict)
        else {}
    )
    secondary_count = 0
    items = attention.get("items") if isinstance(attention.get("items"), list) else []
    for item in items:
        evidence_rows = item.get("evidence") if isinstance(item, dict) else []
        if not isinstance(evidence_rows, list):
            continue
        for evidence in evidence_rows:
            points = evidence.get("expert_points") if isinstance(evidence, dict) else []
            if isinstance(points, list):
                secondary_count += len(points)
    deployed_patch = (
        inputs.get("deployed_patch")
        if isinstance(inputs.get("deployed_patch"), dict)
        else {}
    )
    return {
        "contract_hash": contract.get("contract_hash"),
        "schema_version": contract.get("schema_version"),
        "project_id": project.get("id"),
        "scoring_engine_version": engine.get("requested_version"),
        "effective_engine_version": engine.get("effective_version"),
        "tender_profile_version": profile.get("version"),
        "selector_version": selection.get("version"),
        "criteria_catalog_version": catalog_summary.get("catalog_version"),
        "secondary_criteria_count": secondary_count,
        "deployed_patch_id": deployed_patch.get("id"),
    }


def attach_assessment_contract(report: Record, contract: Record) -> None:
    contract_copy = copy.deepcopy(contract)
    if not verify_assessment_contract(contract_copy):
        raise ValueError("assessment contract failed identity verification")
    report["assessment_contract"] = contract_copy
    report["assessment_contract_status"] = ASSESSMENT_CONTRACT_STATUS_CERTIFIED
    report["assessment_contract_hash"] = contract_copy.get("contract_hash")
    report["assessment_contract_schema_version"] = contract_copy.get("schema_version")
    meta = report.get("meta") if isinstance(report.get("meta"), dict) else {}
    meta = dict(meta)
    meta["assessment_contract_status"] = ASSESSMENT_CONTRACT_STATUS_CERTIFIED
    meta["assessment_contract_hash"] = contract_copy.get("contract_hash")
    meta["assessment_contract_schema_version"] = contract_copy.get("schema_version")
    meta["assessment_contract_summary"] = summarize_assessment_contract(contract_copy)
    report["meta"] = meta


def score_report_snapshot_contract_fields(report: Record) -> Record:
    contract = report.get("assessment_contract")
    if not isinstance(contract, dict) or not str(contract.get("contract_hash") or ""):
        return {
            "assessment_contract_status": ASSESSMENT_CONTRACT_STATUS_LEGACY,
            "assessment_contract_hash": None,
            "assessment_contract_schema_version": None,
            "assessment_contract": None,
        }
    if not verify_assessment_contract(contract):
        return {
            "assessment_contract_status": ASSESSMENT_CONTRACT_STATUS_INVALID,
            "assessment_contract_hash": contract.get("contract_hash"),
            "assessment_contract_schema_version": contract.get("schema_version"),
            "assessment_contract": copy.deepcopy(contract),
        }
    return {
        "assessment_contract_status": ASSESSMENT_CONTRACT_STATUS_CERTIFIED,
        "assessment_contract_hash": contract.get("contract_hash"),
        "assessment_contract_schema_version": contract.get("schema_version"),
        "assessment_contract": copy.deepcopy(contract),
    }


def normalize_score_report_snapshot(record: Record) -> Record:
    """Label historical rows without inventing contract content or identity."""
    contract = record.get("assessment_contract")
    if isinstance(contract, dict) and str(contract.get("contract_hash") or ""):
        status = (
            ASSESSMENT_CONTRACT_STATUS_CERTIFIED
            if verify_assessment_contract(contract)
            else ASSESSMENT_CONTRACT_STATUS_INVALID
        )
        record["assessment_contract_status"] = status
        record["assessment_contract_hash"] = contract.get("contract_hash")
        record["assessment_contract_schema_version"] = contract.get("schema_version")
    else:
        record.setdefault(
            "assessment_contract_status", ASSESSMENT_CONTRACT_STATUS_LEGACY
        )
        record.setdefault("assessment_contract_hash", None)
        record.setdefault("assessment_contract_schema_version", None)
        record.setdefault("assessment_contract", None)
    return record


def rebind_report_calibrator_contract(
    report: Record,
    *,
    project: Record,
    calibrator_model: Optional[Record],
) -> None:
    """Derive a new certified identity without altering the prior result snapshot."""
    existing = report.get("assessment_contract")
    if not verify_assessment_contract(existing):
        report.pop("assessment_contract", None)
        report["assessment_contract_status"] = ASSESSMENT_CONTRACT_STATUS_LEGACY
        report["assessment_contract_hash"] = None
        report["assessment_contract_schema_version"] = None
        meta = report.get("meta") if isinstance(report.get("meta"), dict) else {}
        meta = dict(meta)
        meta["assessment_contract_status"] = ASSESSMENT_CONTRACT_STATUS_LEGACY
        meta["assessment_contract_hash"] = None
        meta["assessment_contract_schema_version"] = None
        report["meta"] = meta
        return

    inputs = copy.deepcopy(existing["inputs"])
    project_inputs = (
        inputs.get("project") if isinstance(inputs.get("project"), dict) else {}
    )
    project_inputs["calibrator_version_locked"] = project.get(
        "calibrator_version_locked"
    )
    inputs["project"] = project_inputs
    project_meta = project.get("meta") if isinstance(project.get("meta"), dict) else {}
    inputs["calibration"] = {
        "actual_model": _calibrator_context(calibrator_model),
        "score_blend_configuration": _mapping(project_meta.get("score_blend")),
    }
    attach_assessment_contract(report, build_contract_from_inputs(inputs))
