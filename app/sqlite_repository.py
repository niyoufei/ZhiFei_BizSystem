from __future__ import annotations

import functools
import inspect
import json
import sqlite3
import threading
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Mapping

from app.repository_uow import RepositorySet, repositories_from_callbacks

STORE_TABLE = "qingtian_json_stores"


class SQLitePayloadError(ValueError):
    pass


@dataclass
class _TransactionState:
    connection: sqlite3.Connection
    allowed_stores: frozenset[str]
    savepoint_counter: int = 0


class SQLiteRepositoryBackend:
    def __init__(
        self,
        database_path: Path,
        *,
        store_defaults: Mapping[str, Any],
        busy_timeout_ms: int = 5000,
    ) -> None:
        if not store_defaults:
            raise ValueError("at least one SQLite store is required")
        self.database_path = Path(database_path).expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._store_defaults = {name: deepcopy(default) for name, default in store_defaults.items()}
        self._busy_timeout_ms = max(1, int(busy_timeout_ms))
        self._local = threading.local()
        self._initialize()

    @property
    def store_names(self) -> tuple[str, ...]:
        return tuple(self._store_defaults)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=self._busy_timeout_ms / 1000.0,
            isolation_level=None,
        )
        connection.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            mode = str(connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower()
            if mode != "wal":
                raise RuntimeError(f"SQLite WAL mode unavailable: {mode}")
            connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {STORE_TABLE} (
                    name TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
                    updated_at TEXT NOT NULL
                ) WITHOUT ROWID
                """
            )
        finally:
            connection.close()

    def _validate_store(self, name: str) -> None:
        if name not in self._store_defaults:
            raise KeyError(f"unknown SQLite repository: {name}")

    def _active_state(self) -> _TransactionState | None:
        return getattr(self._local, "transaction", None)

    def _connection_for_active_store(self, name: str) -> sqlite3.Connection | None:
        state = self._active_state()
        if state is None:
            return None
        if name not in state.allowed_stores:
            raise RuntimeError(f"store not declared in SQLite transaction: {name}")
        return state.connection

    def _load_with_connection(self, connection: sqlite3.Connection, name: str) -> Any:
        row = connection.execute(
            f"SELECT payload FROM {STORE_TABLE} WHERE name = ?",
            (name,),
        ).fetchone()
        if row is None:
            return deepcopy(self._store_defaults[name])
        try:
            return json.loads(str(row[0]))
        except (TypeError, json.JSONDecodeError) as exc:
            raise SQLitePayloadError(f"invalid JSON payload for SQLite store: {name}") from exc

    def load(self, name: str) -> Any:
        self._validate_store(name)
        active_connection = self._connection_for_active_store(name)
        if active_connection is not None:
            return self._load_with_connection(active_connection, name)
        connection = self._connect()
        try:
            return self._load_with_connection(connection, name)
        finally:
            connection.close()

    def _save_with_connection(
        self,
        connection: sqlite3.Connection,
        name: str,
        value: Any,
    ) -> None:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        connection.execute(
            f"""
            INSERT INTO {STORE_TABLE} (name, payload, revision, updated_at)
            VALUES (?, ?, 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            ON CONFLICT(name) DO UPDATE SET
                payload = excluded.payload,
                revision = {STORE_TABLE}.revision + 1,
                updated_at = excluded.updated_at
            """,
            (name, payload),
        )

    def save(self, name: str, value: Any) -> None:
        self._validate_store(name)
        active_connection = self._connection_for_active_store(name)
        if active_connection is not None:
            self._save_with_connection(active_connection, name, value)
            return
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._save_with_connection(connection, name, value)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def transaction_factory(self, *store_names: str):
        names = tuple(store_names)
        if len(set(names)) != len(names):
            raise ValueError("SQLite transaction store names must be unique")
        for name in names:
            self._validate_store(name)

        def decorate(func: Callable[..., Any]):
            if inspect.iscoroutinefunction(func):
                raise TypeError("SQLite transactions only support synchronous functions")

            @functools.wraps(func)
            def wrapped(*args: Any, **kwargs: Any) -> Any:
                state = self._active_state()
                if state is not None:
                    undeclared = set(names).difference(state.allowed_stores)
                    if undeclared:
                        raise RuntimeError(
                            "nested SQLite transaction expands store scope: "
                            f"{sorted(undeclared)}"
                        )
                    state.savepoint_counter += 1
                    savepoint = f"qingtian_nested_{state.savepoint_counter}"
                    state.connection.execute(f"SAVEPOINT {savepoint}")
                    try:
                        result = func(*args, **kwargs)
                    except BaseException:
                        state.connection.execute(f"ROLLBACK TO {savepoint}")
                        state.connection.execute(f"RELEASE {savepoint}")
                        raise
                    state.connection.execute(f"RELEASE {savepoint}")
                    return result

                connection = self._connect()
                self._local.transaction = _TransactionState(
                    connection=connection,
                    allowed_stores=frozenset(names),
                )
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    result = func(*args, **kwargs)
                    connection.commit()
                    return result
                except BaseException:
                    connection.rollback()
                    raise
                finally:
                    del self._local.transaction
                    connection.close()

            wrapped.__signature__ = inspect.signature(func, eval_str=True)
            return wrapped

        return decorate

    def repositories(self) -> RepositorySet:
        loaders: Dict[str, Callable[[], Any]] = {}
        savers: Dict[str, Callable[[Any], None]] = {}
        for name in self.store_names:

            def load_store(*, _name=name) -> Any:
                return self.load(_name)

            def save_store(value: Any, *, _name=name) -> None:
                self.save(_name, value)

            loaders[name] = load_store
            savers[name] = save_store
        return repositories_from_callbacks(loaders=loaders, savers=savers)

    def revision(self, name: str) -> int:
        self._validate_store(name)
        active_connection = self._connection_for_active_store(name)
        connection = active_connection or self._connect()
        try:
            row = connection.execute(
                f"SELECT revision FROM {STORE_TABLE} WHERE name = ?",
                (name,),
            ).fetchone()
            return int(row[0]) if row is not None else 0
        finally:
            if active_connection is None:
                connection.close()

    def journal_mode(self) -> str:
        connection = self._connect()
        try:
            return str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        finally:
            connection.close()

    def integrity_check(self) -> str:
        connection = self._connect()
        try:
            return str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        finally:
            connection.close()
