#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.expert_benchmark_service import (  # noqa: E402
    DATASET_KIND_REAL,
    PREDICTIONS_SCHEMA_VERSION,
    STATUS_BLOCKED,
    STATUS_ELIGIBLE,
    STATUS_NOT_CERTIFIED,
    STATUS_PASS,
    BenchmarkManifest,
    BenchmarkValidationError,
    CasePrediction,
    PredictionArtifact,
    canonical_sha256,
    commitment_is_trusted,
    commitment_sha256,
    compute_execution_evidence_sha256,
    evaluate_expert_benchmark,
    manifest_sha256,
    parse_commitment,
    prediction_artifact_sha256,
    prediction_case_fingerprint,
    prediction_output_fingerprint,
    thresholds_sha256,
)


def _read_json(path: Path) -> Dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise BenchmarkValidationError(f"{path} must contain a JSON object")
    return payload


def _atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    candidate = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    candidate.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(candidate, path)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _is_below(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _external_case_file(data_root: Path, relative_path: str) -> Path:
    root = data_root.resolve()
    path = (root / relative_path).resolve()
    if not _is_below(path, root):
        raise BenchmarkValidationError("case file escapes the external data root")
    if not path.is_file():
        raise BenchmarkValidationError(f"case file does not exist: {relative_path}")
    return path


def _engine_source_sha256() -> str:
    dependency_files = {
        path
        for path in (
            ROOT / "pyproject.toml",
            ROOT / "requirements.txt",
            ROOT / "requirements-runtime.txt",
        )
        if path.is_file()
    }
    files = sorted(
        {
            *(
                path
                for path in (ROOT / "app").rglob("*.py")
                if "__pycache__" not in path.parts
            ),
            *(
                path
                for path in (ROOT / "app" / "resources").rglob("*")
                if path.is_file() and "__pycache__" not in path.parts
            ),
            Path(__file__).resolve(),
            *dependency_files,
        }
    )
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(ROOT)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    installed_distributions = sorted(
        {
            f"{str(name).lower().replace('_', '-')}=={distribution.version}"
            for distribution in importlib.metadata.distributions()
            if (name := distribution.metadata.get("Name"))
        }
    )
    runtime_identity = {
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "installed_distributions": installed_distributions,
    }
    digest.update(b"runtime-identity\0")
    digest.update(
        json.dumps(
            runtime_identity,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    digest.update(b"\0")
    return digest.hexdigest()


def _stable_requirement_id(item_id: str, requirement: str) -> str:
    digest = hashlib.sha256(f"{item_id}\0{requirement}".encode("utf-8")).hexdigest()
    return f"requirement-{digest[:24]}"


def _stable_redline_id(family: str, value: str) -> str:
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()
    return f"{family}-{digest[:24]}"


def _extract_evidence_spans(score_payload: Mapping[str, object]) -> list[dict[str, object]]:
    spans: list[dict[str, object]] = []
    seen: set[tuple[str, str, int, int, str]] = set()
    items = score_payload.get("items") if isinstance(score_payload.get("items"), list) else []
    for raw_item in items:
        if not isinstance(raw_item, dict):
            continue
        item_id = str(raw_item.get("item_id") or "")
        keyword_requirements: Dict[str, list[str]] = {}
        requirements = (
            raw_item.get("requirements")
            if isinstance(raw_item.get("requirements"), list)
            else []
        )
        for raw_requirement in requirements:
            if not isinstance(raw_requirement, dict):
                continue
            requirement = str(raw_requirement.get("requirement") or "")
            requirement_id = _stable_requirement_id(item_id, requirement)
            for keyword in raw_requirement.get("hits") or []:
                keyword_requirements.setdefault(str(keyword), []).append(requirement_id)
        evidence_rows = (
            raw_item.get("evidence") if isinstance(raw_item.get("evidence"), list) else []
        )
        for raw_evidence in evidence_rows:
            if not isinstance(raw_evidence, dict):
                continue
            keyword = str(raw_evidence.get("keyword") or "")
            start = int(raw_evidence.get("start_index") or 0)
            end = int(raw_evidence.get("end_index") or 0)
            if end <= start:
                continue
            for requirement_id in keyword_requirements.get(keyword, [])[:1]:
                identity = (item_id, requirement_id, start, end, "support")
                if identity in seen:
                    continue
                seen.add(identity)
                spans.append(
                    {
                        "item_id": item_id,
                        "requirement_id": requirement_id,
                        "start_index": start,
                        "end_index": end,
                        "polarity": "support",
                        "importance": "core",
                    }
                )
    return spans


def _secondary_catalog_ids(attention_profile: Mapping[str, object]) -> list[str]:
    result: set[str] = set()
    items = (
        attention_profile.get("items")
        if isinstance(attention_profile.get("items"), list)
        else []
    )
    for raw_item in items:
        if not isinstance(raw_item, dict):
            continue
        for raw_evidence in raw_item.get("evidence") or []:
            if not isinstance(raw_evidence, dict):
                continue
            for raw_point in raw_evidence.get("expert_points") or []:
                if not isinstance(raw_point, dict):
                    continue
                catalog_id = str(raw_point.get("catalog_id") or "")
                if catalog_id:
                    result.add(catalog_id)
    return sorted(result)


def _score_case(
    *,
    manifest: BenchmarkManifest,
    case,
    document_text: str,
    context_payload: Mapping[str, object],
    engine_source_sha256: str,
) -> Dict[str, object]:
    # Scoring imports live only in the score phase.  The evaluate phase never
    # imports a production scoring entry point.
    from app.assessment_contract_service import build_contract_from_inputs
    from app.engine.preflight import pre_flight_check
    from app.tender_criteria_service import (
        build_evidence_attention_profile,
        score_document_against_profile,
    )

    profile = context_payload.get("tender_profile")
    if not isinstance(profile, dict):
        raise BenchmarkValidationError(
            f"{case.case_id}: scoring context must contain tender_profile"
        )
    source_context = str(context_payload.get("source_context") or "")
    score = score_document_against_profile(profile, document_text)
    attention = build_evidence_attention_profile(profile, source_context=source_context)
    selection = (
        attention.get("selection_context")
        if isinstance(attention.get("selection_context"), dict)
        else {}
    )
    project_types = [str(value) for value in selection.get("scene_tags") or []]
    secondary_ids = _secondary_catalog_ids(attention)

    primary_items: list[dict[str, object]] = []
    for raw_item in score.get("items") or []:
        if not isinstance(raw_item, dict):
            continue
        maximum = float(raw_item.get("max_score") or 0.0)
        item_score = float(raw_item.get("score") or 0.0)
        primary_items.append(
            {
                "item_id": str(raw_item.get("item_id") or ""),
                "score_ratio": round(item_score / maximum, 8) if maximum > 0 else 0.0,
                "band": str(raw_item.get("band") or ""),
            }
        )

    preflight = pre_flight_check(document_text, raise_on_fatal=False)
    redline_predictions: list[dict[str, object]] = []
    for heading in preflight.get("missing_sections") or []:
        redline_predictions.append(
            {
                "redline_id": _stable_redline_id("missing_core_section", str(heading)),
                "family": "missing_core_section",
                "severity": "disqualify",
            }
        )
    for norm in preflight.get("outdated_norm_refs") or []:
        redline_predictions.append(
            {
                "redline_id": _stable_redline_id("outdated_norm", str(norm)),
                "family": "outdated_norm",
                "severity": "disqualify",
            }
        )

    uncovered_tender_redline_ids = sorted(
        {
            str(row.get("redline_id") or "")
            for row in score.get("hard_redlines") or []
            if isinstance(row, dict) and str(row.get("redline_id") or "")
        }
    )
    contract = build_contract_from_inputs(
        {
            "benchmark": {
                "dataset_id": manifest.dataset_id,
                "case_id": case.case_id,
                "ranking_group_id": case.ranking_group_id,
                "document_sha256": case.document.sha256,
                "scoring_context_sha256": case.scoring_context.sha256,
                "criteria_catalog_version": manifest.criteria_catalog_version,
                "engine_source_sha256": engine_source_sha256,
                "project_types": project_types,
                "secondary_catalog_ids": secondary_ids,
                "scoring_mode": "approved_tender_profile_rule_only",
            },
            "project": {"id": case.project_key},
            "tender_profile": profile,
            "attention_profile": attention,
        }
    )
    case_payload: Dict[str, object] = {
        "case_id": case.case_id,
        "project_key": case.project_key,
        "ranking_group_id": case.ranking_group_id,
        "document_sha256": case.document.sha256,
        "scoring_context_sha256": case.scoring_context.sha256,
        "total_score_5pt": round(float(score.get("normalized_total") or 0.0) / 20.0, 8),
        "project_types": project_types,
        "primary_items": primary_items,
        "secondary_catalog_ids": secondary_ids,
        "evidence_spans": _extract_evidence_spans(score),
        "redline_predictions": redline_predictions,
        "uncovered_tender_redline_ids": uncovered_tender_redline_ids,
        "assessment_contract_hash": str(contract["contract_hash"]),
        "assessment_contract": contract,
    }
    case_payload["semantic_fingerprint"] = prediction_case_fingerprint(case_payload)
    return CasePrediction.model_validate(case_payload).model_dump(mode="json")


def _score_command(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest).resolve()
    commitment_path = Path(args.commitment).resolve()
    manifest_payload = _read_json(manifest_path)
    commitment_payload = _read_json(commitment_path)
    try:
        manifest = BenchmarkManifest.model_validate(manifest_payload)
        commitment = parse_commitment(commitment_payload)
    except Exception as exc:
        raise BenchmarkValidationError(str(exc)) from exc
    data_root = Path(args.data_root).resolve()
    output = Path(args.output).resolve()
    for path, label in (
        (manifest_path, "manifest"),
        (commitment_path, "commitment"),
        (data_root, "case data root"),
        (output, "case-level prediction artifact"),
    ):
        if _is_below(path, ROOT):
            raise BenchmarkValidationError(f"{label} must live outside the repository")

    engine_digest = _engine_source_sha256()
    if commitment.provenance != manifest.dataset_kind:
        raise BenchmarkValidationError("commitment provenance does not match manifest")
    if commitment.dataset_id != manifest.dataset_id:
        raise BenchmarkValidationError("commitment dataset_id does not match manifest")
    if commitment.manifest_sha256 != manifest_sha256(manifest):
        raise BenchmarkValidationError("commitment manifest digest mismatch")
    if commitment.thresholds_sha256 != thresholds_sha256():
        raise BenchmarkValidationError("commitment threshold digest mismatch")
    if commitment.expected_engine_source_sha256 != engine_digest:
        raise BenchmarkValidationError("commitment engine source digest mismatch")
    if manifest.expected_engine_source_sha256 != engine_digest:
        raise BenchmarkValidationError("manifest engine source digest mismatch")
    if commitment.scoring_mode != manifest.scoring_mode:
        raise BenchmarkValidationError("commitment scoring mode mismatch")
    if manifest.dataset_kind == DATASET_KIND_REAL and not commitment_is_trusted(commitment):
        raise BenchmarkValidationError(
            "real expert commitment is not signed by a code-pinned trusted authority"
        )

    execution_started_at = datetime.now(timezone.utc).isoformat()
    execution_nonce = f"exec-{secrets.token_hex(16)}"
    case_predictions: list[dict[str, object]] = []
    for case in manifest.cases:
        document_path = _external_case_file(data_root, case.document.relative_path)
        context_path = _external_case_file(data_root, case.scoring_context.relative_path)
        document_bytes = document_path.read_bytes()
        context_bytes = context_path.read_bytes()
        if _sha256_bytes(document_bytes) != case.document.sha256:
            raise BenchmarkValidationError(f"{case.case_id}: document SHA-256 mismatch")
        if _sha256_bytes(context_bytes) != case.scoring_context.sha256:
            raise BenchmarkValidationError(f"{case.case_id}: scoring context SHA-256 mismatch")
        document_text = document_bytes.decode(case.document.encoding)
        context_text = context_bytes.decode(case.scoring_context.encoding)
        if len(document_bytes) != case.document.byte_length or len(document_text) != case.document.character_length:
            raise BenchmarkValidationError(f"{case.case_id}: document length mismatch")
        if len(context_bytes) != case.scoring_context.byte_length or len(context_text) != case.scoring_context.character_length:
            raise BenchmarkValidationError(f"{case.case_id}: scoring context length mismatch")
        context_payload = json.loads(context_text)
        if not isinstance(context_payload, dict):
            raise BenchmarkValidationError(
                f"{case.case_id}: scoring context must contain a JSON object"
            )
        case_predictions.append(
            _score_case(
                manifest=manifest,
                case=case,
                document_text=document_text,
                context_payload=context_payload,
                engine_source_sha256=engine_digest,
            )
        )

    semantic_output_sha = prediction_output_fingerprint(case_predictions)
    generated_at = datetime.now(timezone.utc).isoformat()
    execution_evidence_sha = compute_execution_evidence_sha256(
        run_id=args.run_id,
        execution_nonce=execution_nonce,
        execution_started_at=execution_started_at,
        generated_at=generated_at,
        manifest_digest=manifest_sha256(manifest),
        commitment_digest=commitment_sha256(commitment),
        engine_source_sha256=engine_digest,
        semantic_output_sha256=semantic_output_sha,
    )
    artifact_payload: Dict[str, object] = {
        "schema_version": PREDICTIONS_SCHEMA_VERSION,
        "dataset_id": manifest.dataset_id,
        "dataset_kind": manifest.dataset_kind,
        "run_id": args.run_id,
        "scoring_mode": manifest.scoring_mode,
        "criteria_catalog_version": manifest.criteria_catalog_version,
        "cases_sha256": manifest_sha256(manifest),
        "engine_source_sha256": engine_digest,
        "semantic_output_sha256": semantic_output_sha,
        "execution_evidence_sha256": execution_evidence_sha,
        "execution_nonce": execution_nonce,
        "execution_started_at": execution_started_at,
        "generated_at": generated_at,
        "cases": case_predictions,
    }
    artifact = PredictionArtifact.model_validate(artifact_payload)
    _atomic_json(output, artifact.model_dump(mode="json"))
    print(f"prediction_artifact={output}")
    print(f"prediction_artifact_sha256={prediction_artifact_sha256(artifact)}")
    print(f"semantic_output_sha256={artifact.semantic_output_sha256}")
    return 0


def _evaluate_command(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest).resolve()
    commitment_path = Path(args.commitment).resolve()
    labels_path = Path(args.labels).resolve()
    release_path = Path(args.release).resolve()
    prediction_paths = [Path(path).resolve() for path in args.predictions]
    for path, label in (
        (manifest_path, "manifest"),
        (commitment_path, "commitment"),
        (labels_path, "expert labels"),
        (release_path, "label release"),
        *((path, "case-level prediction artifact") for path in prediction_paths),
    ):
        if _is_below(path, ROOT):
            raise BenchmarkValidationError(f"{label} must live outside the repository")

    commitment_payload = _read_json(commitment_path)
    commitment = parse_commitment(commitment_payload)
    if commitment.provenance == DATASET_KIND_REAL and not commitment_is_trusted(commitment):
        raise BenchmarkValidationError(
            "real expert commitment is not signed by a code-pinned trusted authority"
        )
    manifest_payload = _read_json(manifest_path)
    try:
        manifest = BenchmarkManifest.model_validate(manifest_payload)
    except Exception as exc:
        raise BenchmarkValidationError(str(exc)) from exc
    current_engine_source_sha256 = _engine_source_sha256()
    if manifest.expected_engine_source_sha256 != current_engine_source_sha256:
        raise BenchmarkValidationError(
            "manifest engine source digest does not match the current benchmark closure"
        )
    if commitment.expected_engine_source_sha256 != current_engine_source_sha256:
        raise BenchmarkValidationError(
            "commitment engine source digest does not match the current benchmark closure"
        )
    labels_payload = _read_json(labels_path)
    release_payload = _read_json(release_path)
    prediction_payloads = [_read_json(path) for path in prediction_paths]
    report = evaluate_expert_benchmark(
        manifest_payload=manifest_payload,
        commitment_payload=commitment_payload,
        labels_payload=labels_payload,
        release_payload=release_payload,
        prediction_payloads=prediction_payloads,
    )
    if report["status"] == STATUS_ELIGIBLE:
        if commitment.provenance != DATASET_KIND_REAL:
            raise BenchmarkValidationError(
                "only trusted external expert gold may receive official certification"
            )
        report["status"] = STATUS_PASS
        report["official_certification"] = {
            "entrypoint": "file_based_external_expert_gold_v1",
            "all_case_level_inputs_outside_repository": True,
            "current_engine_source_sha256": current_engine_source_sha256,
        }
        report.pop("report_sha256", None)
        hash_payload = dict(report)
        hash_payload.pop("generated_at", None)
        report["report_sha256"] = canonical_sha256(hash_payload)
    output = Path(args.output).resolve()
    _atomic_json(output, report)
    print(f"status={report['status']}")
    print(f"report={output}")
    print(f"report_sha256={report['report_sha256']}")
    if report["status"] == STATUS_BLOCKED:
        return 1
    if report["status"] == STATUS_NOT_CERTIFIED:
        return 3
    return 0


def _source_digest_command(args: argparse.Namespace) -> int:
    del args
    print(_engine_source_sha256())
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run sealed, two-phase QingTian expert blind benchmarking."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    source_digest = subparsers.add_parser(
        "source-digest",
        help="Print the complete benchmark scoring-closure SHA-256 for manifest preparation.",
    )
    source_digest.set_defaults(handler=_source_digest_command)

    score = subparsers.add_parser(
        "score",
        help="Read only blind cases and freeze predictions; this command has no labels argument.",
    )
    score.add_argument("--manifest", required=True)
    score.add_argument("--commitment", required=True)
    score.add_argument("--data-root", required=True)
    score.add_argument("--run-id", required=True)
    score.add_argument("--output", required=True)
    score.set_defaults(handler=_score_command)

    evaluate = subparsers.add_parser(
        "evaluate",
        help="Evaluate already frozen predictions against separately released expert labels.",
    )
    evaluate.add_argument("--manifest", required=True)
    evaluate.add_argument("--commitment", required=True)
    evaluate.add_argument("--labels", required=True)
    evaluate.add_argument("--release", required=True)
    evaluate.add_argument("--predictions", nargs="+", required=True)
    evaluate.add_argument("--output", required=True)
    evaluate.set_defaults(handler=_evaluate_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except (BenchmarkValidationError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"BLOCKED_EXPERT_BENCHMARK_INPUT: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
