from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal, Protocol

CollectionShape = Literal["list", "dict"]


@dataclass(frozen=True)
class CollectionDescriptor:
    name: str
    path_getter: Callable[[], Path]
    default_factory: Callable[[], Any]
    shape: CollectionShape
    entity_kind: str
    keep_history: bool = False
    record_id_field: str = "id"
    project_id_field: str = "project_id"
    storage_tier: str = "metadata"


class Repository(Protocol):
    def load(self, descriptor: CollectionDescriptor) -> Any:
        ...

    def save(
        self, descriptor: CollectionDescriptor, data: Any, *, keep_history: bool = False
    ) -> None:
        ...

    def exists(self, descriptor: CollectionDescriptor) -> bool:
        ...

    def snapshot_hash(self, descriptor: CollectionDescriptor) -> str | None:
        ...
