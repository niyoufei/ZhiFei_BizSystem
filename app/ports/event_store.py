from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence


@dataclass(frozen=True)
class EventEnvelope:
    event_id: str
    aggregate_type: str
    aggregate_id: str
    event_type: str
    event_version: int
    payload: dict[str, Any]
    occurred_at: str
    actor_type: str = "system"
    actor_id: str = "system"
    causation_id: str | None = None
    correlation_id: str | None = None
    idempotency_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    sequence_no: int | None = None


@dataclass(frozen=True)
class AppendResult:
    event: EventEnvelope
    inserted: bool


@dataclass(frozen=True)
class ProjectionSnapshot:
    name: str
    last_sequence: int
    snapshot: dict[str, Any]
    updated_at: str


class EventStore(Protocol):
    def append(self, event: EventEnvelope) -> AppendResult:
        ...

    def list_events(
        self,
        *,
        after_sequence: int = 0,
        event_types: Sequence[str] | None = None,
        aggregate_id: str | None = None,
        aggregate_type: str | None = None,
    ) -> list[EventEnvelope]:
        ...

    def save_projection_snapshot(
        self,
        *,
        name: str,
        last_sequence: int,
        snapshot: dict[str, Any],
    ) -> None:
        ...

    def load_projection_snapshot(self, name: str) -> ProjectionSnapshot | None:
        ...
