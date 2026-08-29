from __future__ import annotations

import argparse
import json
import os
import platform
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict
from unittest.mock import patch

from app import storage, storage_migration
from app.sqlite_repository import STORE_TABLE, SQLitePayloadError, SQLiteRepositoryBackend
from app.storage import atomic_write_text

FAULT_INJECTION_SCHEMA_VERSION = "qingtian-fault-injection-v1"


def _result(
    name: str,
    component: str,
    expected_outcome: str,
    operation: Callable[[], Dict[str, object]],
) -> Dict[str, object]:
    try:
        observations = operation()
        passed = all(bool(value) for value in observations.values())
        unexpected_error = None
    except BaseException as exc:
        observations = {}
        passed = False
        unexpected_error = f"{type(exc).__name__}: {exc}"
    return {
        "name": name,
        "component": component,
        "expected_outcome": expected_outcome,
        "passed": passed,
        "observations": observations,
        "unexpected_error": unexpected_error,
    }


def _definitions(directory: Path) -> Dict[str, tuple[Path, Any]]:
    return {
        "projects": (directory / "projects.json", []),
        "project_context": (directory / "project_context.json", {}),
        "submissions": (directory / "submissions.json", []),
    }


def _source_snapshot() -> Dict[str, Any]:
    return {
        "projects": [{"id": "p1", "name": "fault-probe"}],
        "project_context": {"p1": {"text": "context"}},
        "submissions": [{"id": "s1", "project_id": "p1"}],
    }


def _write_source(definitions: Dict[str, tuple[Path, Any]]) -> Dict[str, Any]:
    snapshot = _source_snapshot()
    for name, (path, _default) in definitions.items():
        atomic_write_text(
            path,
            json.dumps(snapshot[name], ensure_ascii=False, indent=2),
        )
    return snapshot


def _json_before_publish_failure(directory: Path) -> Dict[str, object]:
    path = directory / "state.json"
    old_payload = '{"version":"old"}'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(old_payload, encoding="utf-8")
    error_seen = False
    with patch.object(storage.os, "replace", side_effect=OSError("injected replace failure")):
        try:
            storage.atomic_write_text(path, '{"version":"new"}')
        except OSError as exc:
            error_seen = str(exc) == "injected replace failure"
    return {
        "injected_error_propagated": error_seen,
        "old_snapshot_preserved": path.read_text(encoding="utf-8") == old_payload,
        "temporary_files_cleaned": not list(directory.glob(".state.json.*.tmp")),
    }


def _json_after_publish_ack_failure(directory: Path) -> Dict[str, object]:
    path = directory / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"version":"old"}', encoding="utf-8")
    error_seen = False
    with patch.object(
        storage,
        "_fsync_parent_dir",
        side_effect=OSError("injected directory fsync failure"),
    ):
        try:
            storage.atomic_write_text(path, '{"version":"new"}')
        except OSError as exc:
            error_seen = str(exc) == "injected directory fsync failure"
    return {
        "injected_error_propagated": error_seen,
        "complete_new_snapshot_visible": json.loads(path.read_text(encoding="utf-8"))
        == {"version": "new"},
        "temporary_files_cleaned": not list(directory.glob(".state.json.*.tmp")),
    }


def _json_corrupt_source_rejected(directory: Path) -> Dict[str, object]:
    path = directory / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    original = b'{"incomplete":true'
    path.write_bytes(original)
    update_called = False
    error_seen = False

    def update(value: Dict[str, Any]) -> Dict[str, Any]:
        nonlocal update_called
        update_called = True
        return value

    try:
        storage.update_json(path, {}, update)
    except json.JSONDecodeError:
        error_seen = True
    return {
        "decode_error_propagated": error_seen,
        "update_not_called": not update_called,
        "corrupt_source_preserved": path.read_bytes() == original,
    }


def _sqlite_transaction_failure_rolls_back(directory: Path) -> Dict[str, object]:
    backend = SQLiteRepositoryBackend(
        directory / "qingtian.sqlite3",
        store_defaults={"projects": [], "submissions": []},
    )
    backend.save("projects", [{"id": "committed-project"}])
    backend.save("submissions", [{"id": "committed-submission"}])
    revisions = {name: backend.revision(name) for name in backend.store_names}
    error_seen = False

    @backend.transaction_factory("projects", "submissions")
    def fail() -> None:
        backend.save("projects", [{"id": "partial-project"}])
        backend.save("submissions", [{"id": "partial-submission"}])
        raise RuntimeError("injected transaction failure")

    try:
        fail()
    except RuntimeError as exc:
        error_seen = str(exc) == "injected transaction failure"
    return {
        "injected_error_propagated": error_seen,
        "projects_restored": backend.load("projects") == [{"id": "committed-project"}],
        "submissions_restored": backend.load("submissions") == [{"id": "committed-submission"}],
        "revisions_unchanged": revisions
        == {name: backend.revision(name) for name in backend.store_names},
        "integrity_ok": backend.integrity_check() == "ok",
    }


def _sqlite_abandoned_transaction_recovers(directory: Path) -> Dict[str, object]:
    database_path = directory / "qingtian.sqlite3"
    backend = SQLiteRepositoryBackend(database_path, store_defaults={"counter": {}})
    backend.save("counter", {"version": "committed"})
    repo_root = Path(__file__).resolve().parents[1]
    code = """
import os
import sys
from pathlib import Path
from app.sqlite_repository import SQLiteRepositoryBackend
backend = SQLiteRepositoryBackend(Path(sys.argv[1]), store_defaults={'counter': {}})
@backend.transaction_factory('counter')
def abandon():
    backend.save('counter', {'version': 'uncommitted'})
    os._exit(73)
abandon()
"""
    process = subprocess.run(
        [sys.executable, "-c", code, str(database_path)],
        cwd=repo_root,
        env={**os.environ, "PYTHONPATH": str(repo_root)},
        text=True,
        capture_output=True,
        check=False,
    )
    reopened = SQLiteRepositoryBackend(database_path, store_defaults={"counter": {}})
    return {
        "child_terminated_at_injection": process.returncode == 73,
        "last_commit_restored": reopened.load("counter") == {"version": "committed"},
        "integrity_ok": reopened.integrity_check() == "ok",
        "checkpoint_not_busy": reopened.checkpoint()[0] == 0,
    }


def _sqlite_corrupt_payload_detected(directory: Path) -> Dict[str, object]:
    backend = SQLiteRepositoryBackend(
        directory / "qingtian.sqlite3",
        store_defaults={"projects": []},
    )
    backend.save("projects", [{"id": "p1"}])
    connection = sqlite3.connect(backend.database_path)
    try:
        connection.execute(
            f"UPDATE {STORE_TABLE} SET payload = ? WHERE name = ?",
            ("{invalid", "projects"),
        )
        connection.commit()
    finally:
        connection.close()
    error_seen = False
    try:
        backend.load("projects")
    except SQLitePayloadError:
        error_seen = True
    verification = sqlite3.connect(backend.database_path)
    try:
        raw_payload = verification.execute(
            f"SELECT payload FROM {STORE_TABLE} WHERE name = ?",
            ("projects",),
        ).fetchone()[0]
    finally:
        verification.close()
    return {
        "semantic_corruption_rejected": error_seen,
        "corrupt_payload_not_overwritten": raw_payload == "{invalid",
        "physical_integrity_still_reported": backend.integrity_check() == "ok",
    }


def _migration_import_failure_cleans_candidate(directory: Path) -> Dict[str, object]:
    definitions = _definitions(directory / "json")
    _write_source(definitions)
    database_path = directory / "qingtian.sqlite3"
    original_save = SQLiteRepositoryBackend.save

    def fail_on_context(self: SQLiteRepositoryBackend, name: str, value: Any) -> None:
        if name == "project_context":
            raise OSError("injected migration import failure")
        original_save(self, name, value)

    error_seen = False
    with patch.object(SQLiteRepositoryBackend, "save", fail_on_context):
        try:
            storage_migration.migrate_json_to_sqlite(
                database_path,
                store_definitions=definitions,
            )
        except OSError as exc:
            error_seen = str(exc) == "injected migration import failure"
    return {
        "injected_error_propagated": error_seen,
        "destination_not_published": not database_path.exists(),
        "candidate_artifacts_cleaned": not list(directory.glob(".*.candidate*")),
    }


def _migration_publish_failure_cleans_candidate(directory: Path) -> Dict[str, object]:
    definitions = _definitions(directory / "json")
    _write_source(definitions)
    database_path = directory / "qingtian.sqlite3"
    error_seen = False
    with patch.object(
        storage_migration.os,
        "replace",
        side_effect=OSError("injected migration publish failure"),
    ):
        try:
            storage_migration.migrate_json_to_sqlite(
                database_path,
                store_definitions=definitions,
            )
        except OSError as exc:
            error_seen = str(exc) == "injected migration publish failure"
    return {
        "injected_error_propagated": error_seen,
        "destination_not_published": not database_path.exists(),
        "candidate_artifacts_cleaned": not list(directory.glob(".*.candidate*")),
    }


def _recovery_export_failure_cleans_candidate(directory: Path) -> Dict[str, object]:
    definitions = _definitions(directory / "json")
    _write_source(definitions)
    database_path = directory / "qingtian.sqlite3"
    storage_migration.migrate_json_to_sqlite(
        database_path,
        store_definitions=definitions,
    )
    target = directory / "recovered-json"
    original_atomic_write = storage_migration.atomic_write_text
    calls = 0

    def fail_second_write(path: Path, payload: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected recovery write failure")
        original_atomic_write(path, payload)

    error_seen = False
    with patch.object(storage_migration, "atomic_write_text", fail_second_write):
        try:
            storage_migration.export_sqlite_to_json(
                database_path,
                target,
                store_definitions=definitions,
            )
        except OSError as exc:
            error_seen = str(exc) == "injected recovery write failure"
    backend = SQLiteRepositoryBackend(
        database_path,
        store_defaults={name: default for name, (_path, default) in definitions.items()},
    )
    return {
        "injected_error_propagated": error_seen,
        "target_not_published": not target.exists(),
        "candidate_directories_cleaned": not list(directory.glob(".*.candidate")),
        "source_integrity_ok": backend.integrity_check() == "ok",
    }


def _round_trip_recovery_preserves_snapshot(directory: Path) -> Dict[str, object]:
    definitions = _definitions(directory / "json")
    snapshot = _write_source(definitions)
    source_fingerprint = storage_migration.snapshot_fingerprint(snapshot)
    database_path = directory / "qingtian.sqlite3"
    migrated = storage_migration.migrate_json_to_sqlite(
        database_path,
        store_definitions=definitions,
    )
    target = directory / "recovered-json"
    recovered = storage_migration.export_sqlite_to_json(
        database_path,
        target,
        store_definitions=definitions,
    )
    restored = {
        name: json.loads((target / path.name).read_text(encoding="utf-8"))
        for name, (path, _default) in definitions.items()
    }
    return {
        "migration_fingerprint_matches": migrated["destination_fingerprint"] == source_fingerprint,
        "recovery_fingerprint_matches": recovered["recovered_fingerprint"] == source_fingerprint,
        "restored_snapshot_exact": restored == snapshot,
        "integrity_ok": migrated["integrity_check"] == "ok",
    }


def evaluate_fault_injection_guardrails(report: Dict[str, Any]) -> Dict[str, object]:
    checks = {scenario["name"]: bool(scenario["passed"]) for scenario in report["scenarios"]}
    return {
        "passed": bool(checks) and all(checks.values()),
        "checks": checks,
        "scenario_count": len(checks),
    }


def run_fault_injection_probe(work_directory: Path) -> Dict[str, object]:
    work_directory = Path(work_directory).expanduser().resolve()
    work_directory.mkdir(parents=True, exist_ok=True)
    run_directory = Path(tempfile.mkdtemp(prefix="fault-run-", dir=work_directory))
    specifications = (
        (
            "json_before_publish_failure",
            "json_atomic_write",
            "old snapshot remains visible and temporary file is removed",
            _json_before_publish_failure,
        ),
        (
            "json_after_publish_ack_failure",
            "json_atomic_write",
            "complete new snapshot is visible while durability acknowledgement fails",
            _json_after_publish_ack_failure,
        ),
        (
            "json_corrupt_source_rejected",
            "json_update",
            "corrupt bytes are preserved and update callback is not called",
            _json_corrupt_source_rejected,
        ),
        (
            "sqlite_transaction_failure_rolls_back",
            "sqlite_transaction",
            "all writes and revisions roll back to the last commit",
            _sqlite_transaction_failure_rolls_back,
        ),
        (
            "sqlite_abandoned_transaction_recovers",
            "sqlite_wal_recovery",
            "process interruption restores the last committed snapshot",
            _sqlite_abandoned_transaction_recovers,
        ),
        (
            "sqlite_corrupt_payload_detected",
            "sqlite_semantic_integrity",
            "invalid JSON payload fails closed without being overwritten",
            _sqlite_corrupt_payload_detected,
        ),
        (
            "migration_import_failure_cleans_candidate",
            "json_to_sqlite_migration",
            "failed candidate import leaves no destination or candidate artifacts",
            _migration_import_failure_cleans_candidate,
        ),
        (
            "migration_publish_failure_cleans_candidate",
            "json_to_sqlite_migration",
            "failed candidate publication leaves no destination or candidate artifacts",
            _migration_publish_failure_cleans_candidate,
        ),
        (
            "recovery_export_failure_cleans_candidate",
            "sqlite_to_json_recovery",
            "failed recovery leaves no target and preserves the source database",
            _recovery_export_failure_cleans_candidate,
        ),
        (
            "round_trip_recovery_preserves_snapshot",
            "migration_recovery",
            "verified migration and recovery preserve the exact semantic snapshot",
            _round_trip_recovery_preserves_snapshot,
        ),
    )
    scenarios = []
    for name, component, expected_outcome, operation in specifications:
        scenario_directory = run_directory / name
        scenario_directory.mkdir(parents=True, exist_ok=True)
        scenarios.append(
            _result(
                name,
                component,
                expected_outcome,
                lambda operation=operation, directory=scenario_directory: operation(directory),
            )
        )
    report: Dict[str, Any] = {
        "schema_version": FAULT_INJECTION_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "scenarios": scenarios,
    }
    report["guardrails"] = evaluate_fault_injection_guardrails(report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="QingTian storage fault injection probe")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--work-directory", type=Path)
    args = parser.parse_args(argv)
    if args.work_directory is None:
        with tempfile.TemporaryDirectory(prefix="qingtian-fault-injection-") as temporary:
            report = run_fault_injection_probe(Path(temporary))
    else:
        report = run_fault_injection_probe(args.work_directory)
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output is not None:
        atomic_write_text(args.output.expanduser().resolve(), payload)
    print(payload)
    return 0 if report["guardrails"]["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
