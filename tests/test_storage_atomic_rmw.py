from __future__ import annotations

import contextlib
import csv
import importlib
import json
import multiprocessing
import os
import stat
import threading
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


def _append_history_in_process(
    data_dir: str,
    barrier: Any,
    entry: dict[str, str],
) -> None:
    os.environ["QINGTIAN_DATA_DIR"] = data_dir

    from app import storage

    storage = importlib.reload(storage)
    original_load = storage.load_score_history

    def synchronized_load() -> list[dict[str, Any]]:
        rows = original_load()
        barrier.wait()
        return rows

    storage.load_score_history = synchronized_load
    storage.append_score_history(entry)


def _set_cache_in_process(
    data_dir: str,
    barrier: Any,
    text: str,
    value: dict[str, str],
) -> None:
    os.environ["QINGTIAN_DATA_DIR"] = data_dir

    from app import cache, storage

    importlib.reload(storage)
    cache = importlib.reload(cache)
    instance = cache.ScoreCache(persist=True)
    barrier.wait()
    instance.set(text, value)


def test_append_score_history_preserves_concurrent_thread_updates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app import storage

    history_path = tmp_path / "score_history.json"
    history_path.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(storage, "HISTORY_PATH", history_path)

    barrier = threading.Barrier(2, timeout=5)
    original_load = storage.load_score_history
    errors: list[BaseException] = []

    def synchronized_load() -> list[dict[str, Any]]:
        rows = original_load()
        barrier.wait()
        return rows

    monkeypatch.setattr(storage, "load_score_history", synchronized_load)

    def append(entry: dict[str, str]) -> None:
        try:
            storage.append_score_history(entry)
        except BaseException as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=append, args=({"id": "first"},)),
        threading.Thread(target=append, args=({"id": "second"},)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert {row["id"] for row in json.loads(history_path.read_text(encoding="utf-8"))} == {
        "first",
        "second",
    }


@pytest.mark.skipif(os.name != "posix", reason="production file locking uses POSIX flock")
def test_append_score_history_preserves_concurrent_process_updates(tmp_path: Path) -> None:
    data_dir = tmp_path / "external-data"
    data_dir.mkdir()
    history_path = data_dir / "score_history.json"
    history_path.write_text("[]", encoding="utf-8")

    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2, timeout=10)
    processes = [
        context.Process(
            target=_append_history_in_process,
            args=(str(data_dir), barrier, {"id": "first"}),
        ),
        context.Process(
            target=_append_history_in_process,
            args=(str(data_dir), barrier, {"id": "second"}),
        ),
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)

    assert not any(process.is_alive() for process in processes)
    assert [process.exitcode for process in processes] == [0, 0]
    assert {row["id"] for row in json.loads(history_path.read_text(encoding="utf-8"))} == {
        "first",
        "second",
    }


def test_csv_rmw_serialization_failure_preserves_original(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app import app

    data_path = tmp_path / "projects.csv"
    original = "project_id,project_name,client_name\n" "P001,项目1,客户1\n"
    data_path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(app, "DATA", str(data_path))

    def fail_rows(_writer: csv.DictWriter, _rows: Any) -> None:
        raise RuntimeError("controlled serialization failure")

    monkeypatch.setattr(csv.DictWriter, "writerows", fail_rows)

    with pytest.raises(RuntimeError, match="controlled serialization failure"):
        app.save_project(
            {
                "project_id": "P002",
                "project_name": "项目2",
                "client_name": "客户2",
            }
        )

    assert data_path.read_text(encoding="utf-8") == original


def test_update_json_initializes_missing_file_and_updates_valid_file(tmp_path: Path) -> None:
    from app import storage

    path = tmp_path / "state.json"
    created = storage.update_json(path, [], lambda rows: [*rows, {"id": "first"}])
    updated = storage.update_json(path, [], lambda rows: [*rows, {"id": "second"}])

    assert created == [{"id": "first"}]
    assert updated == [{"id": "first"}, {"id": "second"}]
    assert json.loads(path.read_text(encoding="utf-8")) == updated


def test_update_json_rejects_corrupt_file_without_overwriting(
    tmp_path: Path,
) -> None:
    from app import storage

    path = tmp_path / "state.json"
    original = b'{"valid": true'
    path.write_bytes(original)
    update_called = False

    def update(data: dict[str, Any]) -> dict[str, Any]:
        nonlocal update_called
        update_called = True
        return data

    with pytest.raises(json.JSONDecodeError):
        storage.update_json(path, {}, update)

    assert update_called is False
    assert path.read_bytes() == original


def test_update_json_serialization_failure_preserves_original(tmp_path: Path) -> None:
    from app import storage

    path = tmp_path / "state.json"
    original = '{"valid": true}'
    path.write_text(original, encoding="utf-8")

    with pytest.raises(TypeError):
        storage.update_json(path, {}, lambda _data: {"invalid": object()})

    assert path.read_text(encoding="utf-8") == original
    assert list(tmp_path.glob(".state.json.*.tmp")) == []


def test_atomic_write_failure_preserves_original_and_cleans_temp(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app import storage

    path = tmp_path / "state.json"
    original = '{"valid": true}'
    path.write_text(original, encoding="utf-8")
    original_fdopen = storage.os.fdopen

    class FailingWriter:
        def __init__(self, handle: Any) -> None:
            self.handle = handle

        def __enter__(self) -> "FailingWriter":
            return self

        def __exit__(self, *_args: Any) -> None:
            self.handle.close()

        def write(self, _payload: str) -> None:
            raise OSError("controlled temporary write failure")

    def failing_fdopen(fd: int, *args: Any, **kwargs: Any) -> FailingWriter:
        return FailingWriter(original_fdopen(fd, *args, **kwargs))

    monkeypatch.setattr(storage.os, "fdopen", failing_fdopen)

    with pytest.raises(OSError, match="controlled temporary write failure"):
        storage.save_json(path, {"valid": False})

    assert path.read_text(encoding="utf-8") == original
    assert list(tmp_path.glob(".state.json.*.tmp")) == []


def test_atomic_flush_failure_preserves_original_and_cleans_temp(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app import storage

    path = tmp_path / "state.json"
    original = '{"valid": true}'
    path.write_text(original, encoding="utf-8")

    def fail_fsync(_fd: int) -> None:
        raise OSError("controlled flush failure")

    monkeypatch.setattr(storage.os, "fsync", fail_fsync)

    with pytest.raises(OSError, match="controlled flush failure"):
        storage.save_json(path, {"valid": False})

    assert path.read_text(encoding="utf-8") == original
    assert list(tmp_path.glob(".state.json.*.tmp")) == []


def test_atomic_save_preserves_existing_file_mode(tmp_path: Path) -> None:
    from app import storage

    path = tmp_path / "state.json"
    path.write_text('{"version": "old"}', encoding="utf-8")
    path.chmod(0o644)

    storage.save_json(path, {"version": "new"})

    assert json.loads(path.read_text(encoding="utf-8")) == {"version": "new"}
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    assert list(tmp_path.glob(".state.json.*.tmp")) == []


def test_parent_directory_fsync_failure_propagates_after_replace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app import storage

    path = tmp_path / "state.json"
    path.write_text('{"version": "old"}', encoding="utf-8")
    path.chmod(0o644)
    original_close = storage.os.close
    original_fsync = storage.os.fsync
    original_replace = storage.os.replace
    directory_close_attempts = 0
    file_fsyncs = 0
    directory_fsyncs = 0
    replaced = False
    directory_fd: int | None = None

    def record_replace(source: str, destination: Path) -> None:
        nonlocal replaced
        original_replace(source, destination)
        replaced = True

    def fail_directory_fsync(fd: int) -> None:
        nonlocal directory_fd, directory_fsyncs, file_fsyncs
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            directory_fd = fd
            directory_fsyncs += 1
            assert replaced
            raise OSError("controlled parent directory fsync failure")
        file_fsyncs += 1
        original_fsync(fd)

    def fail_after_closing_directory(fd: int) -> None:
        nonlocal directory_close_attempts
        original_close(fd)
        if fd == directory_fd:
            directory_close_attempts += 1
            raise OSError("controlled parent directory close failure")

    monkeypatch.setattr(storage.os, "close", fail_after_closing_directory)
    monkeypatch.setattr(storage.os, "replace", record_replace)
    monkeypatch.setattr(storage.os, "fsync", fail_directory_fsync)

    with pytest.raises(
        OSError,
        match="controlled parent directory fsync failure",
    ) as exc_info:
        storage.save_json(path, {"version": "new"})

    assert file_fsyncs == 1
    assert directory_fsyncs == 1
    assert directory_close_attempts == 1
    assert replaced
    assert directory_fd is not None
    assert exc_info.value.__notes__ == [
        (
            "closing parent directory descriptor also failed: "
            "controlled parent directory close failure"
        )
    ]
    with pytest.raises(OSError):
        os.fstat(directory_fd)
    assert json.loads(path.read_text(encoding="utf-8")) == {"version": "new"}
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    assert list(tmp_path.glob(".state.json.*.tmp")) == []


def test_parent_directory_open_failure_propagates_after_replace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app import storage

    path = tmp_path / "state.json"
    path.write_text('{"version": "old"}', encoding="utf-8")
    path.chmod(0o644)
    original_open = storage.os.open
    original_replace = storage.os.replace
    replaced = False

    def record_replace(source: str, destination: Path) -> None:
        nonlocal replaced
        original_replace(source, destination)
        replaced = True

    def fail_directory_open(
        target: str,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if Path(target) == tmp_path:
            assert replaced
            raise OSError("controlled parent directory open failure")
        return original_open(target, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(storage.os, "replace", record_replace)
    monkeypatch.setattr(storage.os, "open", fail_directory_open)

    with pytest.raises(
        OSError,
        match="controlled parent directory open failure",
    ):
        storage.save_json(path, {"version": "new"})

    assert replaced
    assert json.loads(path.read_text(encoding="utf-8")) == {"version": "new"}
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    assert list(tmp_path.glob(".state.json.*.tmp")) == []


def test_atomic_replace_failure_preserves_original_and_cleans_temp(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app import storage

    path = tmp_path / "state.json"
    original = '{"valid": true}'
    path.write_text(original, encoding="utf-8")

    def fail_replace(_source: str, _destination: Path) -> None:
        raise OSError("controlled replace failure")

    monkeypatch.setattr(storage.os, "replace", fail_replace)

    with pytest.raises(OSError, match="controlled replace failure"):
        storage.save_json(path, {"valid": False})

    assert path.read_text(encoding="utf-8") == original
    assert list(tmp_path.glob(".state.json.*.tmp")) == []


def test_lock_acquisition_failure_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import fcntl

    from app import storage

    path = tmp_path / "state.json"
    original = '{"valid": true}'
    path.write_text(original, encoding="utf-8")
    original_flock = fcntl.flock

    def fail_lock(fd: int, operation: int) -> None:
        if operation == fcntl.LOCK_EX:
            raise OSError("controlled lock failure")
        original_flock(fd, operation)

    monkeypatch.setattr(fcntl, "flock", fail_lock)

    with pytest.raises(OSError, match="controlled lock failure"):
        storage.load_json(path, {})

    assert path.read_text(encoding="utf-8") == original


def test_different_target_files_are_not_globally_serialized(tmp_path: Path) -> None:
    from app import storage

    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    first_path.write_text("[]", encoding="utf-8")
    second_path.write_text("[]", encoding="utf-8")
    first_entered = threading.Event()
    release_first = threading.Event()
    second_done = threading.Event()
    errors: list[BaseException] = []

    def hold_first(rows: list[str]) -> list[str]:
        first_entered.set()
        if not release_first.wait(timeout=5):
            raise RuntimeError("first update was not released")
        return [*rows, "first"]

    def update_first() -> None:
        try:
            storage.update_json(first_path, [], hold_first)
        except BaseException as exc:
            errors.append(exc)

    def update_second() -> None:
        try:
            storage.update_json(second_path, [], lambda rows: [*rows, "second"])
            second_done.set()
        except BaseException as exc:
            errors.append(exc)

    first_thread = threading.Thread(target=update_first)
    second_thread = threading.Thread(target=update_second)
    first_thread.start()
    assert first_entered.wait(timeout=5)
    second_thread.start()
    try:
        assert second_done.wait(timeout=5)
    finally:
        release_first.set()
    first_thread.join(timeout=10)
    second_thread.join(timeout=10)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert errors == []
    assert json.loads(first_path.read_text(encoding="utf-8")) == ["first"]
    assert json.loads(second_path.read_text(encoding="utf-8")) == ["second"]


def test_reader_sees_complete_content_before_and_after_replace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app import storage

    path = tmp_path / "state.json"
    old_value = {"version": "old", "payload": "a" * 100_000}
    new_value = {"version": "new", "payload": "b" * 100_000}
    storage.save_json(path, old_value)
    replace_entered = threading.Event()
    allow_replace = threading.Event()
    errors: list[BaseException] = []
    original_replace = storage.os.replace

    def controlled_replace(source: str, destination: Path) -> None:
        replace_entered.set()
        if not allow_replace.wait(timeout=5):
            raise RuntimeError("replace was not released")
        original_replace(source, destination)

    monkeypatch.setattr(storage.os, "replace", controlled_replace)

    def write_new_value() -> None:
        try:
            storage.save_json(path, new_value)
        except BaseException as exc:
            errors.append(exc)

    writer = threading.Thread(target=write_new_value)
    writer.start()
    assert replace_entered.wait(timeout=5)
    observed_before = json.loads(path.read_text(encoding="utf-8"))
    allow_replace.set()
    writer.join(timeout=10)
    observed_after = json.loads(path.read_text(encoding="utf-8"))

    assert not writer.is_alive()
    assert errors == []
    assert observed_before == old_value
    assert observed_after == new_value


@pytest.mark.skipif(os.name != "posix", reason="production file locking uses POSIX flock")
def test_score_cache_preserves_concurrent_process_updates(tmp_path: Path) -> None:
    data_dir = tmp_path / "external-data"
    data_dir.mkdir()
    (data_dir / "score_cache.json").write_text("{}", encoding="utf-8")

    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2, timeout=10)
    processes = [
        context.Process(
            target=_set_cache_in_process,
            args=(str(data_dir), barrier, "first", {"id": "first"}),
        ),
        context.Process(
            target=_set_cache_in_process,
            args=(str(data_dir), barrier, "second", {"id": "second"}),
        ),
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)

    assert not any(process.is_alive() for process in processes)
    assert [process.exitcode for process in processes] == [0, 0]
    persisted = json.loads((data_dir / "score_cache.json").read_text(encoding="utf-8"))
    assert {entry["value"]["id"] for entry in persisted.values()} == {"first", "second"}


def test_score_cache_rejects_corrupt_existing_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app import cache

    path = tmp_path / "score_cache.json"
    original = b'{"invalid"'
    path.write_bytes(original)
    monkeypatch.setattr(cache, "CACHE_PATH", path)

    with pytest.raises(json.JSONDecodeError):
        cache.ScoreCache(persist=True)

    assert path.read_bytes() == original


def test_nested_read_of_earlier_atomic_store_does_not_reverse_lock_order(
    tmp_path: Path,
) -> None:
    from app import storage

    earlier = tmp_path / "a.json"
    later = tmp_path / "z.json"
    earlier.write_text('{"version": "complete"}', encoding="utf-8")
    later.write_text("{}", encoding="utf-8")

    with storage.path_transaction(later):
        assert storage.load_json(earlier, {}) == {"version": "complete"}


def test_multi_path_transaction_acquires_canonical_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app import storage

    earlier = tmp_path / "a.json"
    later = tmp_path / "z.json"
    acquired: list[Path] = []

    @contextlib.contextmanager
    def record_lock(path: Path):
        acquired.append(path.resolve())
        yield

    monkeypatch.setattr(storage, "_exclusive_file_lock", record_lock)

    with storage.path_transaction(later, earlier):
        pass

    assert acquired == [earlier.resolve(), later.resolve()]


def test_cross_dependency_transactions_do_not_deadlock_and_keep_updates(
    tmp_path: Path,
) -> None:
    from app import storage

    first = tmp_path / "a.json"
    second = tmp_path / "z.json"
    first.write_text("[]", encoding="utf-8")
    second.write_text("[]", encoding="utf-8")
    barrier = threading.Barrier(2, timeout=5)
    errors: list[BaseException] = []

    def update_both(paths: tuple[Path, Path], value: str) -> None:
        try:
            barrier.wait()
            with storage.path_transaction(*paths):
                first_rows = storage.load_json(first, [])
                second_rows = storage.load_json(second, [])
                first_rows.append(value)
                second_rows.append(value)
                storage.save_json(first, first_rows)
                storage.save_json(second, second_rows)
        except BaseException as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=update_both, args=((first, second), "first")),
        threading.Thread(target=update_both, args=((second, first), "second")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert set(json.loads(first.read_text(encoding="utf-8"))) == {"first", "second"}
    assert set(json.loads(second.read_text(encoding="utf-8"))) == {"first", "second"}


def test_explicit_nested_reverse_lock_order_is_rejected(tmp_path: Path) -> None:
    from app import storage

    earlier = tmp_path / "a.json"
    later = tmp_path / "z.json"

    with storage.path_transaction(later):
        with pytest.raises(
            RuntimeError,
            match="nested storage transaction violates canonical lock order",
        ):
            with storage.path_transaction(earlier):
                pass


def test_upload_prepares_outside_lock_and_merges_latest_submission(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app import main, storage

    projects_path = tmp_path / "projects.json"
    submissions_path = tmp_path / "submissions.json"
    monkeypatch.setattr(storage, "PROJECTS_PATH", projects_path)
    monkeypatch.setattr(storage, "SUBMISSIONS_PATH", submissions_path)

    project = {"id": "p1", "scoring_engine_version_locked": "v1"}
    concurrent = {
        "id": "concurrent",
        "project_id": "p1",
        "filename": "other.txt",
        "text": "other",
        "created_at": "2020-01-01T00:00:00+00:00",
    }
    saved: list[dict[str, object]] = []
    load_submission_calls = 0

    def parse_content(content: bytes, filename: str) -> str:
        assert storage._held_path_keys() == set()
        assert content == b"prepared"
        assert filename == "submission.txt"
        return "prepared text"

    def load_latest_submissions() -> list[dict[str, object]]:
        nonlocal load_submission_calls
        load_submission_calls += 1
        return [dict(concurrent)]

    def save_latest_submissions(rows: list[dict[str, object]]) -> None:
        held = storage._held_path_keys()
        assert str(projects_path.resolve()) in held
        assert str(submissions_path.resolve()) in held
        saved[:] = rows

    monkeypatch.setattr(main, "ensure_data_dirs", lambda: None)
    monkeypatch.setattr(main, "load_projects", lambda: [dict(project)])
    monkeypatch.setattr(main, "load_submissions", load_latest_submissions)
    monkeypatch.setattr(main, "save_submissions", save_latest_submissions)
    monkeypatch.setattr(main, "_read_uploaded_file_content", parse_content)
    monkeypatch.setattr(
        main,
        "_resolve_project_scoring_context",
        lambda _project_id: ({}, None, dict(project)),
    )

    result = main.upload_shigong(
        project_id="p1",
        file=SimpleNamespace(filename="submission.txt", file=BytesIO(b"prepared")),
        api_key=None,
        locale="zh",
    )

    assert load_submission_calls == 1
    assert result.filename == "submission.txt"
    assert {row["id"] for row in saved} == {"concurrent", result.id}
