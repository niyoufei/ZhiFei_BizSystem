from __future__ import annotations

from typing import Literal

TaskKind = Literal["scoring", "learning", "governance", "agent", "ops"]
TaskState = Literal[
    "queued",
    "running",
    "succeeded",
    "failed",
    "timed_out",
    "degraded",
    "cached",
]
FailureCategory = Literal[
    "none",
    "validation",
    "configuration",
    "storage",
    "locking",
    "event_log",
    "projection",
    "replay_consistency",
    "dependency_unavailable",
    "timeout",
    "data_integrity",
    "permission",
    "unknown",
]

__all__ = ["FailureCategory", "TaskKind", "TaskState"]
