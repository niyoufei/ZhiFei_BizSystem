from __future__ import annotations

import sqlite3
import threading

import pytest

from app.repository_uow import RepositoryUnitOfWork
from app.sqlite_repository import (
    STORE_TABLE,
    SQLitePayloadError,
    SQLiteRepositoryBackend,
)


@pytest.fixture
def backend(tmp_path):
    return SQLiteRepositoryBackend(
        tmp_path / "qingtian.sqlite3",
        store_defaults={
            "projects": [],
            "submissions": [],
            "settings": {},
        },
    )


def test_backend_enables_wal_and_passes_integrity_check(backend):
    assert backend.journal_mode() == "wal"
    assert backend.integrity_check() == "ok"
    assert backend.checkpoint()[0] == 0


def test_round_trip_persists_across_backend_instances_and_increments_revision(
    tmp_path,
):
    database_path = tmp_path / "qingtian.sqlite3"
    first = SQLiteRepositoryBackend(
        database_path,
        store_defaults={"projects": []},
    )
    first.save("projects", [{"id": "p1", "name": "中文项目"}])
    first.save("projects", [{"id": "p2"}])

    second = SQLiteRepositoryBackend(
        database_path,
        store_defaults={"projects": []},
    )

    assert second.load("projects") == [{"id": "p2"}]
    assert second.revision("projects") == 2


def test_multi_store_transaction_commits_atomically(backend):
    @backend.transaction_factory("projects", "submissions")
    def commit():
        backend.save("projects", [{"id": "p1"}])
        backend.save("submissions", [{"id": "s1"}])
        return "done"

    assert commit() == "done"
    assert backend.load("projects") == [{"id": "p1"}]
    assert backend.load("submissions") == [{"id": "s1"}]


def test_multi_store_transaction_rolls_back_every_write_on_error(backend):
    @backend.transaction_factory("projects", "submissions")
    def commit():
        backend.save("projects", [{"id": "partial-project"}])
        backend.save("submissions", [{"id": "partial-submission"}])
        raise RuntimeError("controlled failure")

    with pytest.raises(RuntimeError, match="controlled failure"):
        commit()

    assert backend.load("projects") == []
    assert backend.load("submissions") == []
    assert backend.revision("projects") == 0
    assert backend.revision("submissions") == 0


def test_transaction_rejects_access_to_undeclared_store(backend):
    @backend.transaction_factory("projects")
    def commit():
        backend.save("submissions", [{"id": "forbidden"}])

    with pytest.raises(
        RuntimeError,
        match="store not declared in SQLite transaction: submissions",
    ):
        commit()

    assert backend.load("submissions") == []


def test_transaction_allows_undeclared_read_only_dependency(backend):
    backend.save("submissions", [{"id": "s1"}])

    @backend.transaction_factory("projects")
    def commit():
        assert backend.load("submissions") == [{"id": "s1"}]
        backend.save("projects", [{"id": "p1"}])

    commit()

    assert backend.load("projects") == [{"id": "p1"}]


def test_nested_transaction_uses_savepoint_for_declared_subset(backend):
    @backend.transaction_factory("projects")
    def inner():
        backend.save("projects", [{"id": "nested"}])

    @backend.transaction_factory("projects", "submissions")
    def outer():
        backend.save("submissions", [{"id": "outer"}])
        inner()

    outer()

    assert backend.load("projects") == [{"id": "nested"}]
    assert backend.load("submissions") == [{"id": "outer"}]


def test_nested_transaction_cannot_expand_outer_store_scope(backend):
    @backend.transaction_factory("projects", "submissions")
    def inner():
        pass

    @backend.transaction_factory("projects")
    def outer():
        backend.save("projects", [{"id": "partial"}])
        inner()

    with pytest.raises(RuntimeError, match="expands store scope.*submissions"):
        outer()

    assert backend.load("projects") == []


def test_repository_unit_of_work_runs_on_sqlite_backend(backend):
    unit_of_work = RepositoryUnitOfWork(
        backend.repositories(),
        transaction_factory=backend.transaction_factory,
    )

    def operation(repositories):
        repositories["projects"].save([{"id": "p1"}])
        repositories["submissions"].save([{"id": "s1"}])

    unit_of_work.run(("projects", "submissions"), operation)

    assert backend.load("projects") == [{"id": "p1"}]
    assert backend.load("submissions") == [{"id": "s1"}]


def test_wal_reader_sees_last_commit_while_writer_transaction_is_open(backend):
    backend.save("projects", [{"id": "committed"}])
    write_visible = threading.Event()
    finish_write = threading.Event()
    errors = []

    def writer():
        try:

            @backend.transaction_factory("projects")
            def commit():
                backend.save("projects", [{"id": "uncommitted"}])
                write_visible.set()
                assert finish_write.wait(timeout=5)

            commit()
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=writer)
    thread.start()
    assert write_visible.wait(timeout=5)

    assert backend.load("projects") == [{"id": "committed"}]
    finish_write.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert errors == []
    assert backend.load("projects") == [{"id": "uncommitted"}]


def test_invalid_json_payload_fails_closed(backend):
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

    with pytest.raises(SQLitePayloadError, match="projects"):
        backend.load("projects")


def test_unknown_store_and_duplicate_transaction_names_fail_fast(backend):
    with pytest.raises(KeyError, match="unknown SQLite repository: missing"):
        backend.load("missing")
    with pytest.raises(ValueError, match="must be unique"):
        backend.transaction_factory("projects", "projects")
