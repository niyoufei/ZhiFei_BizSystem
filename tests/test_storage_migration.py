from __future__ import annotations

import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from app.sqlite_repository import SQLiteRepositoryBackend
from app.storage import _STORE_PATH_ATTRIBUTES, STORE_DEFINITIONS
from app.storage_migration import (
    StorageMigrationConflict,
    StorageMigrationError,
    StorageSourceInvalid,
    export_sqlite_to_json,
    migrate_json_to_sqlite,
    snapshot_fingerprint,
)


def _definitions(source_dir):
    return {
        "projects": (source_dir / "projects.json", []),
        "project_context": (source_dir / "project_context.json", {}),
        "submissions": (source_dir / "submissions.json", []),
    }


def _source_snapshot():
    return {
        "projects": [{"id": "p1", "name": "中文项目"}],
        "project_context": {"p1": {"text": "context"}},
        "submissions": [{"id": "s1", "project_id": "p1"}],
    }


def _write_snapshot(definitions, snapshot):
    for name, (path, _default) in definitions.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(snapshot[name], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _backend(database_path, definitions):
    return SQLiteRepositoryBackend(
        database_path,
        store_defaults={name: default for name, (_path, default) in definitions.items()},
    )


def test_canonical_store_manifest_covers_every_transaction_store():
    assert set(STORE_DEFINITIONS) == set(_STORE_PATH_ATTRIBUTES)
    assert len(STORE_DEFINITIONS) == 20
    assert sum(default == {} for _path, default in STORE_DEFINITIONS.values()) == 2


def test_migration_runbook_documents_cutover_and_recovery_boundaries():
    runbook = (Path(__file__).resolve().parents[1] / "docs/storage-backend-migration.md").read_text(
        encoding="utf-8"
    )
    for required in (
        "QINGTIAN_STORAGE_BACKEND=sqlite",
        "QINGTIAN_SQLITE_PATH",
        "app.storage_migration migrate",
        "app.storage_migration export-json",
        "source_fingerprint == destination_fingerprint",
        "source_fingerprint == recovered_fingerprint",
        "未声明写入会失败并回滚",
    ):
        assert required in runbook


def test_json_to_sqlite_migration_is_verified_and_idempotent(tmp_path):
    definitions = _definitions(tmp_path / "json")
    snapshot = _source_snapshot()
    _write_snapshot(definitions, snapshot)
    database_path = tmp_path / "qingtian.sqlite3"

    created = migrate_json_to_sqlite(
        database_path,
        store_definitions=definitions,
    )
    repeated = migrate_json_to_sqlite(
        database_path,
        store_definitions=definitions,
    )

    backend = _backend(database_path, definitions)
    assert {name: backend.load(name) for name in definitions} == snapshot
    assert created["created"] is True
    assert created["idempotent"] is False
    assert created["integrity_check"] == "ok"
    assert created["journal_mode"] == "wal"
    assert created["source_fingerprint"] == snapshot_fingerprint(snapshot)
    assert created["destination_fingerprint"] == snapshot_fingerprint(snapshot)
    assert repeated["created"] is False
    assert repeated["idempotent"] is True


def test_migration_refuses_to_replace_different_existing_database(tmp_path):
    definitions = _definitions(tmp_path / "json")
    original = _source_snapshot()
    _write_snapshot(definitions, original)
    database_path = tmp_path / "qingtian.sqlite3"
    migrate_json_to_sqlite(database_path, store_definitions=definitions)

    changed = deepcopy(original)
    changed["projects"] = [{"id": "p2"}]
    _write_snapshot(definitions, changed)

    with pytest.raises(
        StorageMigrationConflict,
        match="contains different store data",
    ):
        migrate_json_to_sqlite(database_path, store_definitions=definitions)

    backend = _backend(database_path, definitions)
    assert {name: backend.load(name) for name in definitions} == original


def test_malformed_json_source_fails_before_destination_creation(tmp_path):
    definitions = _definitions(tmp_path / "json")
    snapshot = _source_snapshot()
    _write_snapshot(definitions, snapshot)
    definitions["projects"][0].write_text("{invalid", encoding="utf-8")
    database_path = tmp_path / "qingtian.sqlite3"

    with pytest.raises(StorageSourceInvalid, match="cannot read JSON store projects"):
        migrate_json_to_sqlite(database_path, store_definitions=definitions)

    assert not database_path.exists()


def test_migration_removes_candidate_after_import_failure(tmp_path, monkeypatch):
    definitions = _definitions(tmp_path / "json")
    _write_snapshot(definitions, _source_snapshot())
    database_path = tmp_path / "qingtian.sqlite3"
    original_save = SQLiteRepositoryBackend.save

    def fail_on_context(self, name, value):
        if name == "project_context":
            raise OSError("controlled import failure")
        return original_save(self, name, value)

    monkeypatch.setattr(SQLiteRepositoryBackend, "save", fail_on_context)

    with pytest.raises(OSError, match="controlled import failure"):
        migrate_json_to_sqlite(database_path, store_definitions=definitions)

    assert not database_path.exists()
    assert list(tmp_path.glob(".*.candidate*")) == []


def test_sqlite_recovery_export_round_trips_to_new_json_directory(tmp_path):
    definitions = _definitions(tmp_path / "source-json")
    snapshot = _source_snapshot()
    _write_snapshot(definitions, snapshot)
    database_path = tmp_path / "qingtian.sqlite3"
    migrate_json_to_sqlite(database_path, store_definitions=definitions)
    target_dir = tmp_path / "recovered-json"

    report = export_sqlite_to_json(
        database_path,
        target_dir,
        store_definitions=definitions,
    )

    recovered = {
        name: json.loads((target_dir / path.name).read_text(encoding="utf-8"))
        for name, (path, _default) in definitions.items()
    }
    assert recovered == snapshot
    assert report["source_fingerprint"] == snapshot_fingerprint(snapshot)
    assert report["recovered_fingerprint"] == snapshot_fingerprint(snapshot)


def test_recovery_export_requires_existing_database_and_new_target(tmp_path):
    definitions = _definitions(tmp_path / "json")
    with pytest.raises(StorageMigrationError, match="source does not exist"):
        export_sqlite_to_json(
            tmp_path / "missing.sqlite3",
            tmp_path / "target",
            store_definitions=definitions,
        )

    database_path = tmp_path / "qingtian.sqlite3"
    _write_snapshot(definitions, _source_snapshot())
    migrate_json_to_sqlite(database_path, store_definitions=definitions)
    target = tmp_path / "existing-target"
    target.mkdir()
    with pytest.raises(StorageMigrationConflict, match="target already exists"):
        export_sqlite_to_json(
            database_path,
            target,
            store_definitions=definitions,
        )


def test_snapshot_fingerprint_is_order_independent():
    first = {"a": [{"x": 1, "y": 2}], "b": {"z": 3}}
    second = {"b": {"z": 3}, "a": [{"y": 2, "x": 1}]}
    assert snapshot_fingerprint(first) == snapshot_fingerprint(second)


def _run_storage_subprocess(tmp_path, backend_name, code):
    repo_root = Path(__file__).resolve().parents[1]
    data_dir = tmp_path / f"data-{backend_name}"
    database_path = tmp_path / f"{backend_name}.sqlite3"
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(repo_root),
            "QINGTIAN_DATA_DIR": str(data_dir),
            "QINGTIAN_STORAGE_BACKEND": backend_name,
            "QINGTIAN_SQLITE_PATH": str(database_path),
            "API_KEYS": "storage-migration-test-key",
        }
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=repo_root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, data_dir, database_path


def test_explicit_sqlite_backend_routes_existing_storage_api_and_transactions(tmp_path):
    code = """
from app import storage
assert storage.active_storage_backend() == 'sqlite'
assert storage.load_projects() == []
storage.save_projects([{'id': 'p1'}])
storage.append_score_history({'id': 'h1'})
try:
    @storage.atomic_json_transaction('projects', 'submissions')
    def fail():
        storage.save_projects([{'id': 'partial'}])
        storage.save_submissions([{'id': 'partial'}])
        raise RuntimeError('rollback')
    fail()
except RuntimeError:
    pass
assert storage.load_projects() == [{'id': 'p1'}]
assert storage.load_submissions() == []
assert storage.load_score_history() == [{'id': 'h1'}]
print('SQLITE_STORAGE_OK')
"""
    result, data_dir, database_path = _run_storage_subprocess(tmp_path, "sqlite", code)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "SQLITE_STORAGE_OK"
    assert database_path.exists()
    assert not (data_dir / "projects.json").exists()


def test_json_backend_remains_default_compatible_path(tmp_path):
    code = """
from app import storage
assert storage.active_storage_backend() == 'json'
storage.save_projects([{'id': 'p1'}])
assert storage.load_projects() == [{'id': 'p1'}]
print('JSON_STORAGE_OK')
"""
    result, data_dir, database_path = _run_storage_subprocess(tmp_path, "json", code)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "JSON_STORAGE_OK"
    assert (data_dir / "projects.json").exists()
    assert not database_path.exists()


def test_storage_migration_cli_migrates_and_exports_canonical_manifest(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    data_dir = tmp_path / "source-data"
    data_dir.mkdir()
    (data_dir / "projects.json").write_text(
        json.dumps([{"id": "p1"}]),
        encoding="utf-8",
    )
    database_path = tmp_path / "qingtian.sqlite3"
    recovery_dir = tmp_path / "recovery-data"
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(repo_root),
            "QINGTIAN_DATA_DIR": str(data_dir),
            "QINGTIAN_STORAGE_BACKEND": "json",
        }
    )

    migrate = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.storage_migration",
            "migrate",
            "--database",
            str(database_path),
        ],
        cwd=repo_root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    exported = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.storage_migration",
            "export-json",
            "--database",
            str(database_path),
            "--target-data-dir",
            str(recovery_dir),
        ],
        cwd=repo_root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert migrate.returncode == 0, migrate.stderr
    assert exported.returncode == 0, exported.stderr
    assert json.loads(migrate.stdout)["store_count"] == 20
    assert json.loads(exported.stdout)["store_count"] == 20
    assert json.loads((recovery_dir / "projects.json").read_text()) == [{"id": "p1"}]


def test_main_project_lifecycle_smoke_runs_on_explicit_sqlite_backend(tmp_path):
    code = """
from fastapi.testclient import TestClient
from app.main import app
client = TestClient(app, headers={'X-API-Key': 'storage-migration-test-key'})
created = client.post('/api/v1/projects', json={'name': 'SQLite smoke'})
assert created.status_code == 200, created.text
project_id = created.json()['id']
listed = client.get('/api/v1/projects')
assert listed.status_code == 200
assert [row['id'] for row in listed.json()] == [project_id]
hygiene = client.get('/api/v1/system/data_hygiene')
assert hygiene.status_code == 200, hygiene.text
deleted = client.delete(f'/api/v1/projects/{project_id}')
assert deleted.status_code == 204, deleted.text
assert client.get('/api/v1/projects').json() == []
print('SQLITE_MAIN_SMOKE_OK')
"""
    result, _data_dir, database_path = _run_storage_subprocess(tmp_path, "sqlite", code)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "SQLITE_MAIN_SMOKE_OK"
    assert database_path.exists()
