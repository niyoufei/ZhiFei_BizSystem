from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path

from app.ports.artifact_store import StoredArtifact


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class FileArtifactStore:
    def __init__(self, root_dir: Path):
        self.root_dir = Path(root_dir)

    def ensure_roots(self) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def store_file(
        self,
        *,
        project_id: str,
        artifact_type: str,
        source_path: Path,
        filename: str,
    ) -> StoredArtifact:
        self.ensure_roots()
        target_dir = self.root_dir / str(project_id) / str(artifact_type)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / filename
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{filename}.",
            suffix=".tmp",
            dir=str(target_dir),
        )
        os.close(fd)
        temp_path = Path(temp_name)
        try:
            shutil.copy2(source_path, temp_path)
            os.replace(temp_path, target)
        finally:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
        return StoredArtifact(
            project_id=str(project_id),
            artifact_type=str(artifact_type),
            filename=str(filename),
            path=target,
            size_bytes=int(target.stat().st_size),
            content_hash=_file_sha256(target),
        )

    def remove_project(self, project_id: str) -> None:
        shutil.rmtree(self.root_dir / str(project_id), ignore_errors=True)
