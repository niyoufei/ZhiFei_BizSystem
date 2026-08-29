from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Mapping

from app.sqlite_repository import SQLiteRepositoryBackend
from app.storage import STORE_DEFINITIONS, atomic_write_text, load_json, path_transaction

StoreDefinitions = Mapping[str, tuple[Path, Any]]
Snapshot = Dict[str, Any]


class StorageMigrationError(RuntimeError):
    pass


class StorageMigrationConflict(StorageMigrationError):
    pass


class StorageSourceInvalid(StorageMigrationError):
    pass


def _canonical_payload(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def snapshot_fingerprint(snapshot: Mapping[str, Any]) -> str:
    digest = hashlib.sha256()
    for name in sorted(snapshot):
        store_digest = hashlib.sha256(
            _canonical_payload(snapshot[name]).encode("utf-8")
        ).hexdigest()
        digest.update(f"{name}:{store_digest}\n".encode())
    return digest.hexdigest()


def snapshot_json_stores(
    store_definitions: StoreDefinitions = STORE_DEFINITIONS,
) -> Snapshot:
    paths = tuple(path for path, _default in store_definitions.values())
    snapshot: Snapshot = {}
    with path_transaction(*paths):
        for name, (path, default) in store_definitions.items():
            try:
                value = load_json(path, deepcopy(default))
            except (OSError, TypeError, json.JSONDecodeError) as exc:
                raise StorageSourceInvalid(f"cannot read JSON store {name}: {exc}") from exc
            if not isinstance(value, type(default)):
                raise StorageSourceInvalid(
                    f"JSON store {name} has {type(value).__name__}; "
                    f"expected {type(default).__name__}"
                )
            snapshot[name] = deepcopy(value)
    return snapshot


def _record_count(snapshot: Mapping[str, Any]) -> int:
    return sum(len(value) for value in snapshot.values())


def _sqlite_snapshot(
    backend: SQLiteRepositoryBackend,
    names: tuple[str, ...],
) -> Snapshot:
    return {name: backend.load(name) for name in names}


def _remove_sqlite_artifacts(database_path: Path) -> None:
    for path in (
        database_path,
        Path(f"{database_path}-wal"),
        Path(f"{database_path}-shm"),
    ):
        path.unlink(missing_ok=True)


def _fsync_parent(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _migration_report(
    *,
    snapshot: Snapshot,
    database_path: Path,
    created: bool,
    idempotent: bool,
    backend: SQLiteRepositoryBackend,
) -> Dict[str, object]:
    return {
        "database_path": str(database_path),
        "store_count": len(snapshot),
        "record_count": _record_count(snapshot),
        "source_fingerprint": snapshot_fingerprint(snapshot),
        "destination_fingerprint": snapshot_fingerprint(_sqlite_snapshot(backend, tuple(snapshot))),
        "journal_mode": backend.journal_mode(),
        "integrity_check": backend.integrity_check(),
        "created": created,
        "idempotent": idempotent,
    }


def migrate_json_to_sqlite(
    database_path: Path,
    *,
    store_definitions: StoreDefinitions = STORE_DEFINITIONS,
) -> Dict[str, object]:
    snapshot = snapshot_json_stores(store_definitions)
    database_path = Path(database_path).expanduser().resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    defaults = {name: deepcopy(default) for name, (_path, default) in store_definitions.items()}
    names = tuple(store_definitions)

    if database_path.exists():
        try:
            existing = SQLiteRepositoryBackend(
                database_path,
                store_defaults=defaults,
            )
            if existing.integrity_check() != "ok":
                raise StorageMigrationConflict("existing SQLite database failed integrity check")
            existing_snapshot = _sqlite_snapshot(existing, names)
        except StorageMigrationConflict:
            raise
        except BaseException as exc:
            raise StorageMigrationConflict(
                f"existing SQLite database cannot be verified: {exc}"
            ) from exc
        if existing_snapshot == snapshot:
            return _migration_report(
                snapshot=snapshot,
                database_path=database_path,
                created=False,
                idempotent=True,
                backend=existing,
            )
        if any(existing.revision(name) > 0 for name in names):
            raise StorageMigrationConflict("existing SQLite database contains different store data")

    descriptor, candidate_raw = tempfile.mkstemp(
        prefix=f".{database_path.name}.",
        suffix=".candidate",
        dir=str(database_path.parent),
    )
    os.close(descriptor)
    candidate = Path(candidate_raw)
    try:
        candidate_backend = SQLiteRepositoryBackend(
            candidate,
            store_defaults=defaults,
        )

        @candidate_backend.transaction_factory(*names)
        def import_snapshot() -> None:
            for name in names:
                candidate_backend.save(name, deepcopy(snapshot[name]))

        import_snapshot()
        if _sqlite_snapshot(candidate_backend, names) != snapshot:
            raise StorageMigrationError("SQLite candidate semantic verification failed")
        if candidate_backend.integrity_check() != "ok":
            raise StorageMigrationError("SQLite candidate integrity check failed")
        checkpoint = candidate_backend.checkpoint()
        if checkpoint[0] != 0:
            raise StorageMigrationError(f"SQLite candidate checkpoint remained busy: {checkpoint}")
        Path(f"{candidate}-wal").unlink(missing_ok=True)
        Path(f"{candidate}-shm").unlink(missing_ok=True)
        Path(f"{database_path}-wal").unlink(missing_ok=True)
        Path(f"{database_path}-shm").unlink(missing_ok=True)
        os.replace(candidate, database_path)
        _fsync_parent(database_path)
    except BaseException:
        _remove_sqlite_artifacts(candidate)
        raise

    published = SQLiteRepositoryBackend(
        database_path,
        store_defaults=defaults,
    )
    if _sqlite_snapshot(published, names) != snapshot:
        raise StorageMigrationError("published SQLite database differs from JSON source")
    return _migration_report(
        snapshot=snapshot,
        database_path=database_path,
        created=True,
        idempotent=False,
        backend=published,
    )


def export_sqlite_to_json(
    database_path: Path,
    target_data_dir: Path,
    *,
    store_definitions: StoreDefinitions = STORE_DEFINITIONS,
) -> Dict[str, object]:
    database_path = Path(database_path).expanduser().resolve()
    target_data_dir = Path(target_data_dir).expanduser().resolve()
    if not database_path.exists():
        raise StorageMigrationError(f"SQLite recovery source does not exist: {database_path}")
    if target_data_dir.exists():
        raise StorageMigrationConflict(f"JSON recovery target already exists: {target_data_dir}")
    target_data_dir.parent.mkdir(parents=True, exist_ok=True)
    defaults = {name: deepcopy(default) for name, (_path, default) in store_definitions.items()}
    backend = SQLiteRepositoryBackend(database_path, store_defaults=defaults)
    if backend.integrity_check() != "ok":
        raise StorageMigrationError("SQLite recovery source failed integrity check")
    snapshot = _sqlite_snapshot(backend, tuple(store_definitions))

    candidate_dir = Path(
        tempfile.mkdtemp(
            prefix=f".{target_data_dir.name}.",
            suffix=".candidate",
            dir=str(target_data_dir.parent),
        )
    )
    try:
        filenames: set[str] = set()
        for name, (source_path, _default) in store_definitions.items():
            filename = source_path.name
            if filename in filenames:
                raise StorageMigrationError(f"duplicate JSON recovery filename: {filename}")
            filenames.add(filename)
            atomic_write_text(
                candidate_dir / filename,
                json.dumps(snapshot[name], ensure_ascii=False, indent=2),
            )
        recovered = {
            name: load_json(candidate_dir / source_path.name, deepcopy(default))
            for name, (source_path, default) in store_definitions.items()
        }
        if recovered != snapshot:
            raise StorageMigrationError("JSON recovery candidate semantic verification failed")
        os.replace(candidate_dir, target_data_dir)
        _fsync_parent(target_data_dir)
    except BaseException:
        shutil.rmtree(candidate_dir, ignore_errors=True)
        raise

    return {
        "database_path": str(database_path),
        "target_data_dir": str(target_data_dir),
        "store_count": len(snapshot),
        "record_count": _record_count(snapshot),
        "source_fingerprint": snapshot_fingerprint(snapshot),
        "recovered_fingerprint": snapshot_fingerprint(recovered),
        "integrity_check": "ok",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="QingTian JSON/SQLite storage migration")
    subparsers = parser.add_subparsers(dest="command", required=True)
    migrate_parser = subparsers.add_parser("migrate")
    migrate_parser.add_argument("--database", required=True, type=Path)
    export_parser = subparsers.add_parser("export-json")
    export_parser.add_argument("--database", required=True, type=Path)
    export_parser.add_argument("--target-data-dir", required=True, type=Path)
    args = parser.parse_args(argv)

    if args.command == "migrate":
        report = migrate_json_to_sqlite(args.database)
    else:
        report = export_sqlite_to_json(args.database, args.target_data_dir)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
