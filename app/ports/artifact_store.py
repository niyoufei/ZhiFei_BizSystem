from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class StoredArtifact:
    project_id: str
    artifact_type: str
    filename: str
    path: Path
    size_bytes: int
    content_hash: str


class ArtifactStore(Protocol):
    def ensure_roots(self) -> None:
        ...

    def store_file(
        self,
        *,
        project_id: str,
        artifact_type: str,
        source_path: Path,
        filename: str,
    ) -> StoredArtifact:
        ...

    def remove_project(self, project_id: str) -> None:
        ...
