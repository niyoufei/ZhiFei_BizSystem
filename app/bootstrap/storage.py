from __future__ import annotations

from pathlib import Path
from typing import Any

from app import storage as storage_runtime
from app.application.storage_access import StorageAccess
from app.ports.repositories import CollectionDescriptor


class RuntimeCollectionRepository:
    def load(self, descriptor: CollectionDescriptor) -> Any:
        return storage_runtime.load_collection(descriptor)

    def save(
        self, descriptor: CollectionDescriptor, data: Any, *, keep_history: bool = False
    ) -> None:
        storage_runtime.save_collection(descriptor, data, keep_history=keep_history)

    def exists(self, descriptor: CollectionDescriptor) -> bool:
        runtime = storage_runtime.get_storage_runtime()
        if runtime.config.primary_backend == "sqlite" and runtime.sqlite_repository is not None:
            try:
                if runtime.sqlite_repository.exists(descriptor):
                    return True
            except Exception:
                pass
        return runtime.json_repository.exists(descriptor)

    def snapshot_hash(self, descriptor: CollectionDescriptor) -> str | None:
        return storage_runtime.collection_snapshot_hash(descriptor)


_REPOSITORY = RuntimeCollectionRepository()


def _load_json(path: Path, default: Any) -> Any:
    return storage_runtime.load_json(path, default)


def _save_json(path: Path, payload: Any) -> None:
    storage_runtime.save_json(path, payload)


def get_storage_access() -> StorageAccess:
    runtime = storage_runtime.get_storage_runtime()
    return StorageAccess(
        repository=_REPOSITORY,
        event_store=runtime.event_store,
        artifact_store=runtime.artifact_store,
        descriptors=storage_runtime.get_registered_collection_descriptors(),
        load_json=_load_json,
        save_json=_save_json,
        list_json_versions=storage_runtime.list_json_versions,
        load_json_version=storage_runtime.load_json_version,
        restore_json_version=storage_runtime.restore_json_version,
    )


__all__ = ["RuntimeCollectionRepository", "get_storage_access"]
