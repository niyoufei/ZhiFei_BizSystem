from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List
from uuid import uuid4

import yaml

from app.storage import atomic_write_text

Record = Dict[str, object]
Records = List[Record]
Callback = Callable[..., Any]

_CONFIGURATION_WRITE_LOCK = threading.RLock()


@contextmanager
def _exclusive_configuration_lock(lock_path: Path) -> Iterator[None]:
    lock_path = lock_path.expanduser().resolve()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _CONFIGURATION_WRITE_LOCK, lock_path.open("a+b") as handle:
        if os.name == "posix":
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "posix":
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    directory_fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _write_text_create_once(path: Path, payload: str) -> None:
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_directory(path.parent)
    except BaseException:
        if fd >= 0:
            os.close(fd)
        path.unlink(missing_ok=True)
        _fsync_directory(path.parent)
        raise


def _remove_file_durably(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    _fsync_directory(path.parent)


def _create_backup_pair(
    *,
    resources_dir: Path,
    backup_timestamp: str,
    lexicon_text: str,
    rubric_text: str,
) -> tuple[Path, Path, str]:
    generation = f"{backup_timestamp}_{uuid4().hex}"
    lexicon_backup = resources_dir / f"lexicon.yaml.bak_{generation}"
    rubric_backup = resources_dir / f"rubric.yaml.bak_{generation}"
    _write_text_create_once(lexicon_backup, lexicon_text)
    try:
        _write_text_create_once(rubric_backup, rubric_text)
    except BaseException:
        _remove_file_durably(lexicon_backup)
        raise
    return lexicon_backup, rubric_backup, generation


def _restore_configuration_pair(
    *,
    active_config_path: Path,
    original_active_snapshot: str | None,
    reload_config: Callback,
    publish_error: BaseException,
) -> None:
    rollback_errors: list[BaseException] = []
    try:
        if original_active_snapshot is None:
            _remove_file_durably(active_config_path)
        else:
            atomic_write_text(active_config_path, original_active_snapshot)
    except BaseException as rollback_error:
        rollback_errors.append(rollback_error)
    try:
        reload_config()
    except BaseException as reload_error:
        rollback_errors.append(reload_error)
    for rollback_error in rollback_errors:
        note = (
            "adaptive configuration rollback also failed: "
            f"{type(rollback_error).__name__}: {rollback_error}"
        )
        add_note = getattr(publish_error, "add_note", None)
        if callable(add_note):
            add_note(note)
        else:
            notes = list(getattr(publish_error, "__notes__", []))
            notes.append(note)
            publish_error.__notes__ = notes


def apply_and_persist(
    project_id: str,
    submissions: Records,
    *,
    resources_dir: Path,
    backup_timestamp: str,
    load_config: Callback,
    reload_config: Callback,
    build_adaptive_suggestions: Callback,
    build_adaptive_patch: Callback,
    apply_adaptive_patch: Callback,
    apply_rubric_patch: Callback,
) -> Record:
    resources_dir = resources_dir.expanduser().resolve()
    lexicon_path = resources_dir / "lexicon.yaml"
    rubric_path = resources_dir / "rubric.yaml"
    active_config_path = resources_dir / "active_config.yaml"
    lock_path = resources_dir / ".adaptive_configuration.lock"

    with _exclusive_configuration_lock(lock_path):
        config = load_config()
        current_lexicon = deepcopy(config.lexicon)
        current_rubric = deepcopy(config.rubric)
        original_active_snapshot = (
            active_config_path.read_text(encoding="utf-8") if active_config_path.exists() else None
        )
        if original_active_snapshot is None:
            backup_lexicon = lexicon_path.read_text(encoding="utf-8")
            backup_rubric = rubric_path.read_text(encoding="utf-8")
        else:
            backup_lexicon = yaml.safe_dump(current_lexicon, allow_unicode=True)
            backup_rubric = yaml.safe_dump(current_rubric, allow_unicode=True)

        stats_result = build_adaptive_suggestions(submissions, current_lexicon)
        patch = build_adaptive_patch(current_lexicon, stats_result["penalty_stats"])
        updated_lexicon, lexicon_changes = apply_adaptive_patch(current_lexicon, patch)
        updated_rubric, rubric_changes = apply_rubric_patch(
            current_rubric,
            patch.get("rubric_adjustments", {}),
        )
        lexicon_backup, _rubric_backup, generation = _create_backup_pair(
            resources_dir=resources_dir,
            backup_timestamp=backup_timestamp,
            lexicon_text=backup_lexicon,
            rubric_text=backup_rubric,
        )
        active_payload = yaml.safe_dump(
            {
                "generation": generation,
                "lexicon": updated_lexicon,
                "rubric": updated_rubric,
            },
            allow_unicode=True,
        )

        try:
            atomic_write_text(active_config_path, active_payload)
            reload_config()
        except BaseException as publish_error:
            _restore_configuration_pair(
                active_config_path=active_config_path,
                original_active_snapshot=original_active_snapshot,
                reload_config=reload_config,
                publish_error=publish_error,
            )
            raise

    return {
        "project_id": project_id,
        "applied": True,
        "changes": [*lexicon_changes, *rubric_changes],
        "backup_path": str(lexicon_backup),
        "source": stats_result.get("source") or {},
    }
