from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from app.ports.event_store import AppendResult, EventEnvelope, ProjectionSnapshot


class SQLiteEventStoreError(RuntimeError):
    def __init__(self, db_path: Path, code: str, detail: str):
        super().__init__(detail)
        self.db_path = db_path
        self.code = code
        self.detail = detail


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteEventStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.db_path), timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        self._initialize(connection)
        return connection

    def _initialize(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS event_log (
                sequence_no INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                aggregate_type TEXT NOT NULL,
                aggregate_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                event_version INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                actor_type TEXT,
                actor_id TEXT,
                causation_id TEXT,
                correlation_id TEXT,
                idempotency_key TEXT,
                metadata_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_event_log_idempotency
            ON event_log (idempotency_key)
            WHERE idempotency_key IS NOT NULL
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS projection_snapshots (
                name TEXT PRIMARY KEY,
                last_sequence INTEGER NOT NULL,
                snapshot_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

    def _row_to_event(self, row: sqlite3.Row) -> EventEnvelope:
        return EventEnvelope(
            event_id=str(row["event_id"]),
            aggregate_type=str(row["aggregate_type"]),
            aggregate_id=str(row["aggregate_id"]),
            event_type=str(row["event_type"]),
            event_version=int(row["event_version"]),
            payload=json.loads(str(row["payload_json"])),
            occurred_at=str(row["occurred_at"]),
            actor_type=str(row["actor_type"] or "system"),
            actor_id=str(row["actor_id"] or "system"),
            causation_id=str(row["causation_id"] or "") or None,
            correlation_id=str(row["correlation_id"] or "") or None,
            idempotency_key=str(row["idempotency_key"] or "") or None,
            metadata=json.loads(str(row["metadata_json"] or "{}")),
            sequence_no=int(row["sequence_no"]),
        )

    def append(self, event: EventEnvelope) -> AppendResult:
        payload_json = json.dumps(event.payload, ensure_ascii=False, sort_keys=True)
        metadata_json = json.dumps(event.metadata, ensure_ascii=False, sort_keys=True)
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                if event.idempotency_key:
                    existing = connection.execute(
                        "SELECT * FROM event_log WHERE idempotency_key = ?",
                        (event.idempotency_key,),
                    ).fetchone()
                    if existing is not None:
                        connection.commit()
                        return AppendResult(event=self._row_to_event(existing), inserted=False)
                existing = connection.execute(
                    "SELECT * FROM event_log WHERE event_id = ?",
                    (event.event_id,),
                ).fetchone()
                if existing is not None:
                    connection.commit()
                    return AppendResult(event=self._row_to_event(existing), inserted=False)
                cursor = connection.execute(
                    """
                    INSERT INTO event_log (
                        event_id,
                        aggregate_type,
                        aggregate_id,
                        event_type,
                        event_version,
                        payload_json,
                        occurred_at,
                        actor_type,
                        actor_id,
                        causation_id,
                        correlation_id,
                        idempotency_key,
                        metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        event.aggregate_type,
                        event.aggregate_id,
                        event.event_type,
                        event.event_version,
                        payload_json,
                        event.occurred_at,
                        event.actor_type,
                        event.actor_id,
                        event.causation_id,
                        event.correlation_id,
                        event.idempotency_key,
                        metadata_json,
                    ),
                )
                sequence_no = int(cursor.lastrowid)
                connection.commit()
                return AppendResult(
                    event=replace(event, sequence_no=sequence_no),
                    inserted=True,
                )
            except sqlite3.IntegrityError:
                connection.rollback()
                existing = None
                if event.idempotency_key:
                    existing = connection.execute(
                        "SELECT * FROM event_log WHERE idempotency_key = ?",
                        (event.idempotency_key,),
                    ).fetchone()
                if existing is None:
                    existing = connection.execute(
                        "SELECT * FROM event_log WHERE event_id = ?",
                        (event.event_id,),
                    ).fetchone()
                if existing is not None:
                    return AppendResult(event=self._row_to_event(existing), inserted=False)
                raise
            except Exception as exc:
                connection.rollback()
                raise SQLiteEventStoreError(
                    self.db_path,
                    "sqlite_event_append_failed",
                    f"事件写入失败：{event.event_type}，{exc}",
                ) from exc

    def list_events(
        self,
        *,
        after_sequence: int = 0,
        event_types: Sequence[str] | None = None,
        aggregate_id: str | None = None,
        aggregate_type: str | None = None,
    ) -> list[EventEnvelope]:
        clauses = ["sequence_no > ?"]
        params: list[object] = [int(after_sequence)]
        if event_types:
            placeholders = ",".join("?" for _ in event_types)
            clauses.append(f"event_type IN ({placeholders})")
            params.extend(str(item) for item in event_types)
        if aggregate_id:
            clauses.append("aggregate_id = ?")
            params.append(str(aggregate_id))
        if aggregate_type:
            clauses.append("aggregate_type = ?")
            params.append(str(aggregate_type))
        query = (
            "SELECT * FROM event_log WHERE " + " AND ".join(clauses) + " ORDER BY sequence_no ASC"
        )
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [self._row_to_event(row) for row in rows]

    def save_projection_snapshot(
        self,
        *,
        name: str,
        last_sequence: int,
        snapshot: dict[str, object],
    ) -> None:
        now_iso = _utc_now_iso()
        snapshot_json = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO projection_snapshots (name, last_sequence, snapshot_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    last_sequence = excluded.last_sequence,
                    snapshot_json = excluded.snapshot_json,
                    updated_at = excluded.updated_at
                """,
                (name, int(last_sequence), snapshot_json, now_iso),
            )
            connection.commit()

    def load_projection_snapshot(self, name: str) -> ProjectionSnapshot | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM projection_snapshots WHERE name = ?",
                (name,),
            ).fetchone()
        if row is None:
            return None
        return ProjectionSnapshot(
            name=str(row["name"]),
            last_sequence=int(row["last_sequence"]),
            snapshot=json.loads(str(row["snapshot_json"] or "{}")),
            updated_at=str(row["updated_at"] or _utc_now_iso()),
        )
