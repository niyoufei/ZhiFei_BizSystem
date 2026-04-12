from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from app.ports.artifact_store import ArtifactStore
from app.ports.event_store import AppendResult, EventEnvelope, EventStore
from app.ports.repositories import CollectionDescriptor, Repository


@dataclass(frozen=True)
class StorageAccess:
    repository: Repository
    event_store: EventStore
    artifact_store: ArtifactStore
    descriptors: Mapping[str, CollectionDescriptor]
    load_json: Callable[[Path, Any], Any]
    save_json: Callable[[Path, Any], None]
    list_json_versions: Callable[[Path], list[dict[str, Any]]]
    load_json_version: Callable[[Path, str, Any], Any]
    restore_json_version: Callable[[Path, str], dict[str, Any]]

    def descriptor(self, name: str) -> CollectionDescriptor:
        descriptor = self.descriptors.get(str(name))
        if descriptor is None:
            raise KeyError(f"unknown_storage_collection:{name}")
        return descriptor

    def load(self, name: str) -> Any:
        return self.repository.load(self.descriptor(name))

    def save(self, name: str, data: Any, *, keep_history: bool | None = None) -> None:
        descriptor = self.descriptor(name)
        effective_keep_history = descriptor.keep_history if keep_history is None else keep_history
        self.repository.save(descriptor, data, keep_history=effective_keep_history)

    def exists(self, name: str) -> bool:
        return self.repository.exists(self.descriptor(name))

    def snapshot_hash(self, name: str) -> str | None:
        return self.repository.snapshot_hash(self.descriptor(name))

    def load_projects(self) -> list[dict[str, Any]]:
        return self.load("projects")

    def save_projects(self, data: list[dict[str, Any]]) -> None:
        self.save("projects", data)

    def load_submissions(self) -> list[dict[str, Any]]:
        return self.load("submissions")

    def save_submissions(self, data: list[dict[str, Any]]) -> None:
        self.save("submissions", data)

    def load_materials(self) -> list[dict[str, Any]]:
        return self.load("materials")

    def save_materials(self, data: list[dict[str, Any]]) -> None:
        self.save("materials", data)

    def load_ground_truth(self) -> list[dict[str, Any]]:
        return self.load("ground_truth")

    def save_ground_truth(self, data: list[dict[str, Any]]) -> None:
        self.save("ground_truth", data)

    def load_qingtian_results(self) -> list[dict[str, Any]]:
        return self.load("qingtian_results")

    def save_qingtian_results(self, data: list[dict[str, Any]]) -> None:
        self.save("qingtian_results", data)

    def load_score_reports(self) -> list[dict[str, Any]]:
        return self.load("score_reports")

    def save_score_reports(self, data: list[dict[str, Any]]) -> None:
        self.save("score_reports", data)

    def load_evidence_units(self) -> list[dict[str, Any]]:
        return self.load("evidence_units")

    def save_evidence_units(self, data: list[dict[str, Any]]) -> None:
        self.save("evidence_units", data)

    def append_domain_event(
        self,
        *,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: dict[str, Any],
        actor_type: str = "system",
        actor_id: str = "system",
        correlation_id: str | None = None,
        causation_id: str | None = None,
        idempotency_key: str | None = None,
        metadata: dict[str, Any] | None = None,
        event_version: int = 1,
    ) -> dict[str, Any]:
        try:
            from app.application.task_runtime import current_runtime_context

            runtime_context = current_runtime_context()
        except Exception:
            runtime_context = {}
        effective_correlation_id = (
            correlation_id or str(runtime_context.get("correlation_id") or "").strip() or None
        )
        effective_metadata = dict(metadata or {})
        for key in ("run_id", "task_kind", "task_name"):
            value = str(runtime_context.get(key) or "").strip()
            if value and key not in effective_metadata:
                effective_metadata[key] = value
        event = EventEnvelope(
            event_id=str(os.urandom(16).hex()),
            aggregate_type=str(aggregate_type),
            aggregate_id=str(aggregate_id),
            event_type=str(event_type),
            event_version=int(event_version),
            payload=dict(payload or {}),
            occurred_at=datetime.now(timezone.utc).isoformat(),
            actor_type=str(actor_type),
            actor_id=str(actor_id),
            causation_id=causation_id,
            correlation_id=effective_correlation_id,
            idempotency_key=idempotency_key,
            metadata=effective_metadata,
        )
        try:
            result: AppendResult = self.event_store.append(event)
        except Exception as exc:
            return {
                "inserted": False,
                "disabled": False,
                "event_type": event_type,
                "error": str(exc),
            }
        return {
            "inserted": result.inserted,
            "sequence_no": result.event.sequence_no,
            "event_id": result.event.event_id,
            "event_type": result.event.event_type,
        }

    def list_events(
        self,
        *,
        after_sequence: int = 0,
        event_types: Sequence[str] | None = None,
        aggregate_id: str | None = None,
        aggregate_type: str | None = None,
    ) -> list[EventEnvelope]:
        return self.event_store.list_events(
            after_sequence=after_sequence,
            event_types=event_types,
            aggregate_id=aggregate_id,
            aggregate_type=aggregate_type,
        )
