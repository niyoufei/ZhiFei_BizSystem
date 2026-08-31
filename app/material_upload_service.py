from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Callable, Dict, List, Tuple
from uuid import UUID, uuid4

Record = Dict[str, object]


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    directory_fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _write_versioned_file(project_dir: Path, normalized_name: str, content: bytes) -> Path:
    objects_dir = project_dir / ".objects"
    target_dir = objects_dir / uuid4().hex
    target_dir.mkdir(parents=True, exist_ok=False)
    _fsync_directory(project_dir)
    _fsync_directory(objects_dir)
    target = target_dir / normalized_name
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
        _fsync_directory(target.parent)
    finally:
        if fd >= 0:
            os.close(fd)
        temp_path.unlink(missing_ok=True)
    return target


def _cleanup_superseded_files(
    superseded_paths: List[Path],
    *,
    project_dir: Path,
    normalized_name: str,
    current_target: Path,
) -> None:
    project_root = project_dir.resolve()
    legacy_target = project_root / normalized_name
    objects_dir = (project_root / ".objects").resolve()
    current_target = Path(os.path.abspath(current_target))
    for candidate in superseded_paths:
        try:
            candidate = candidate.expanduser()
            if not candidate.is_absolute():
                continue
            candidate = Path(os.path.abspath(candidate))
            candidate_parent = candidate.parent.resolve()
            version_name = candidate_parent.name
            versioned_target = (
                candidate.name == normalized_name
                and candidate_parent.parent == objects_dir
                and UUID(version_name).hex == version_name.lower()
            )
        except (OSError, ValueError):
            continue
        if candidate == current_target or not (candidate == legacy_target or versioned_target):
            continue
        try:
            candidate.unlink(missing_ok=True)
            _fsync_directory(candidate.parent)
        except OSError:
            continue


def write_material_file_and_record(
    *,
    project_id: str,
    normalized_material_type: str,
    normalized_name: str,
    materials_dir: Path,
    content: bytes,
    commit_uploaded_material_record: Callable[[str, str, str, Path], Tuple[Record, List[Path]]],
) -> Record:
    project_dir = materials_dir / project_id / normalized_material_type
    project_dir.mkdir(parents=True, exist_ok=True)
    materials_root = project_dir.parents[1]
    _fsync_directory(materials_root)
    _fsync_directory(project_dir.parent)
    target = _write_versioned_file(project_dir, normalized_name, content)
    record, superseded_paths = commit_uploaded_material_record(
        project_id,
        normalized_material_type,
        normalized_name,
        target,
    )
    _cleanup_superseded_files(
        superseded_paths,
        project_dir=project_dir,
        normalized_name=normalized_name,
        current_target=target,
    )
    return record


def build_material_upload_response(
    *,
    project_id: str,
    record: Record,
    invalidate_material_index_cache: Callable[[str], None],
    rebuild_project_anchors_and_requirements: Callable[[str], Tuple[List[Record], List[Record]]],
    sync_constraints: bool = True,
) -> Record:
    invalidate_material_index_cache(project_id)

    if not sync_constraints:
        return {
            "status": "ok",
            "material": record,
            "constraint_sync": {"rebuilt": False, "deferred": True},
        }

    constraint_sync: Record = {"rebuilt": False}
    try:
        anchors, requirements = rebuild_project_anchors_and_requirements(project_id)
        constraint_sync = {
            "rebuilt": True,
            "anchors": len(anchors),
            "requirements": len(requirements),
        }
    except Exception as exc:
        constraint_sync = {"rebuilt": False, "error": f"{type(exc).__name__}: {exc}"}

    return {"status": "ok", "material": record, "constraint_sync": constraint_sync}
