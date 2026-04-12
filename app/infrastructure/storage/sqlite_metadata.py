from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from app.ports.repositories import CollectionDescriptor


class SQLiteMetadataError(RuntimeError):
    def __init__(self, db_path: Path, code: str, detail: str):
        super().__init__(detail)
        self.db_path = db_path
        self.code = code
        self.detail = detail


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _payload_hash(data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _iter_index_rows(
    descriptor: CollectionDescriptor,
    data: Any,
) -> Iterable[tuple[str, str | None, str, str]]:
    if descriptor.shape == "list":
        rows = data if isinstance(data, list) else []
        for index, item in enumerate(rows):
            payload = item if isinstance(item, dict) else {"value": item}
            record_key = str(payload.get(descriptor.record_id_field) or index)
            project_id = payload.get(descriptor.project_id_field)
            yield (
                record_key,
                str(project_id).strip()
                if project_id is not None and str(project_id).strip()
                else None,
                descriptor.entity_kind,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            )
        return

    rows = data if isinstance(data, dict) else {}
    for key, item in rows.items():
        payload = item if isinstance(item, dict) else {"value": item}
        project_id = payload.get(descriptor.project_id_field)
        yield (
            str(key),
            str(project_id).strip() if project_id is not None and str(project_id).strip() else None,
            descriptor.entity_kind,
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
        )


class SQLiteMetadataRepository:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.db_path), timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA foreign_keys=ON")
        self._initialize(connection)
        return connection

    def _initialize(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS collection_snapshots (
                collection_name TEXT PRIMARY KEY,
                payload_kind TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS collection_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collection_name TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS metadata_records (
                collection_name TEXT NOT NULL,
                record_key TEXT NOT NULL,
                project_id TEXT,
                entity_kind TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (collection_name, record_key)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_metadata_records_project
            ON metadata_records (collection_name, project_id)
            """
        )

    def exists(self, descriptor: CollectionDescriptor) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM collection_snapshots WHERE collection_name = ?",
                (descriptor.name,),
            ).fetchone()
        return row is not None

    def load(self, descriptor: CollectionDescriptor) -> Any:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM collection_snapshots WHERE collection_name = ?",
                (descriptor.name,),
            ).fetchone()
        if row is None:
            return descriptor.default_factory()
        try:
            payload = json.loads(str(row["payload_json"]))
        except json.JSONDecodeError as exc:
            raise SQLiteMetadataError(
                self.db_path,
                "sqlite_payload_parse_failed",
                f"SQLite 元数据快照损坏：{descriptor.name}",
            ) from exc
        if descriptor.shape == "list" and not isinstance(payload, list):
            raise SQLiteMetadataError(
                self.db_path,
                "sqlite_shape_mismatch",
                f"SQLite 元数据结构异常：{descriptor.name} 应为数组。",
            )
        if descriptor.shape == "dict" and not isinstance(payload, dict):
            raise SQLiteMetadataError(
                self.db_path,
                "sqlite_shape_mismatch",
                f"SQLite 元数据结构异常：{descriptor.name} 应为对象。",
            )
        return payload

    def save(
        self, descriptor: CollectionDescriptor, data: Any, *, keep_history: bool = False
    ) -> None:
        payload_json = json.dumps(data, ensure_ascii=False, sort_keys=True)
        payload_hash = _payload_hash(data)
        now_iso = _utc_now_iso()
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO collection_snapshots (
                        collection_name,
                        payload_kind,
                        payload_json,
                        payload_hash,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(collection_name) DO UPDATE SET
                        payload_kind = excluded.payload_kind,
                        payload_json = excluded.payload_json,
                        payload_hash = excluded.payload_hash,
                        updated_at = excluded.updated_at
                    """,
                    (
                        descriptor.name,
                        descriptor.shape,
                        payload_json,
                        payload_hash,
                        now_iso,
                    ),
                )
                if keep_history:
                    connection.execute(
                        """
                        INSERT INTO collection_history (
                            collection_name,
                            payload_json,
                            payload_hash,
                            created_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (descriptor.name, payload_json, payload_hash, now_iso),
                    )
                connection.execute(
                    "DELETE FROM metadata_records WHERE collection_name = ?",
                    (descriptor.name,),
                )
                for record_key, project_id, entity_kind, record_payload_json in _iter_index_rows(
                    descriptor, data
                ):
                    connection.execute(
                        """
                        INSERT INTO metadata_records (
                            collection_name,
                            record_key,
                            project_id,
                            entity_kind,
                            payload_json,
                            payload_hash,
                            updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            descriptor.name,
                            record_key,
                            project_id,
                            entity_kind,
                            record_payload_json,
                            hashlib.sha256(record_payload_json.encode("utf-8")).hexdigest(),
                            now_iso,
                        ),
                    )
                connection.commit()
            except Exception as exc:
                connection.rollback()
                raise SQLiteMetadataError(
                    self.db_path,
                    "sqlite_write_failed",
                    f"SQLite 元数据写入失败：{descriptor.name}，{exc}",
                ) from exc

    def snapshot_hash(self, descriptor: CollectionDescriptor) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_hash FROM collection_snapshots WHERE collection_name = ?",
                (descriptor.name,),
            ).fetchone()
        if row is None:
            return None
        return str(row["payload_hash"])
