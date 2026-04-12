from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, Iterator
from uuid import uuid4

from fastapi import HTTPException

from app.contracts.task_runtime import FailureCategory, TaskKind, TaskState

_CORRELATION_ID_VAR: ContextVar[str | None] = ContextVar("zhifei_correlation_id", default=None)
_PROJECT_ID_VAR: ContextVar[str | None] = ContextVar("zhifei_project_id", default=None)
_RUN_ID_VAR: ContextVar[str | None] = ContextVar("zhifei_run_id", default=None)
_TASK_KIND_VAR: ContextVar[TaskKind | None] = ContextVar("zhifei_task_kind", default=None)
_TASK_NAME_VAR: ContextVar[str | None] = ContextVar("zhifei_task_name", default=None)


@dataclass(frozen=True)
class RuntimeContextSnapshot:
    correlation_id: str | None
    project_id: str | None
    run_id: str | None
    task_kind: TaskKind | None
    task_name: str | None


def _clean_str(value: object | None) -> str | None:
    text = str(value or "").strip()
    return text or None


def ensure_correlation_id() -> str:
    existing = _clean_str(_CORRELATION_ID_VAR.get())
    if existing:
        return existing
    generated = uuid4().hex
    _CORRELATION_ID_VAR.set(generated)
    return generated


def current_runtime_context() -> dict[str, str]:
    payload = {
        "correlation_id": _clean_str(_CORRELATION_ID_VAR.get()),
        "project_id": _clean_str(_PROJECT_ID_VAR.get()),
        "run_id": _clean_str(_RUN_ID_VAR.get()),
        "task_kind": _clean_str(_TASK_KIND_VAR.get()),
        "task_name": _clean_str(_TASK_NAME_VAR.get()),
    }
    return {key: value for key, value in payload.items() if value is not None}


@contextmanager
def runtime_context(
    *,
    correlation_id: str | None = None,
    project_id: str | None = None,
    run_id: str | None = None,
    task_kind: TaskKind | None = None,
    task_name: str | None = None,
) -> Iterator[RuntimeContextSnapshot]:
    tokens: list[tuple[ContextVar[Any], Token[Any]]] = []
    try:
        normalized_correlation_id = _clean_str(correlation_id) or ensure_correlation_id()
        tokens.append((_CORRELATION_ID_VAR, _CORRELATION_ID_VAR.set(normalized_correlation_id)))
        if project_id is not None:
            tokens.append((_PROJECT_ID_VAR, _PROJECT_ID_VAR.set(_clean_str(project_id))))
        if run_id is not None:
            tokens.append((_RUN_ID_VAR, _RUN_ID_VAR.set(_clean_str(run_id))))
        if task_kind is not None:
            tokens.append((_TASK_KIND_VAR, _TASK_KIND_VAR.set(task_kind)))
        if task_name is not None:
            tokens.append((_TASK_NAME_VAR, _TASK_NAME_VAR.set(_clean_str(task_name))))
        yield RuntimeContextSnapshot(
            correlation_id=_clean_str(_CORRELATION_ID_VAR.get()),
            project_id=_clean_str(_PROJECT_ID_VAR.get()),
            run_id=_clean_str(_RUN_ID_VAR.get()),
            task_kind=_TASK_KIND_VAR.get(),
            task_name=_clean_str(_TASK_NAME_VAR.get()),
        )
    finally:
        while tokens:
            variable, token = tokens.pop()
            variable.reset(token)


def log_structured(
    logger: logging.Logger,
    *,
    level: int = logging.INFO,
    event: str,
    **fields: Any,
) -> None:
    payload: dict[str, Any] = {"event": event}
    payload.update(current_runtime_context())
    payload.update(
        {
            key: value
            for key, value in fields.items()
            if value is not None and not (isinstance(value, str) and not value.strip())
        }
    )
    message = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    if level == logging.DEBUG:
        logger.debug(message)
        return
    if level == logging.INFO:
        logger.info(message)
        return
    if level == logging.WARNING:
        logger.warning(message)
        return
    if level == logging.ERROR:
        logger.error(message)
        return
    if level == logging.CRITICAL:
        logger.critical(message)
        return
    logger.log(level, message)


def classify_failure(exc: BaseException) -> FailureCategory:
    detail = f"{type(exc).__name__}: {exc}".lower()
    if isinstance(exc, HTTPException):
        if exc.status_code in {401, 403}:
            return "permission"
        if exc.status_code == 422:
            return "validation"
        if exc.status_code in {400, 404, 409}:
            return "data_integrity"
        if exc.status_code >= 500:
            return "dependency_unavailable"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, PermissionError):
        return "permission"
    if isinstance(exc, FileNotFoundError):
        return "storage"
    if isinstance(exc, OSError):
        if "locked" in detail or "busy" in detail:
            return "locking"
        return "storage"
    if "sqlite_event" in detail or "event" in detail and "append" in detail:
        return "event_log"
    if "projection" in detail:
        return "projection"
    if "replay" in detail and "consisten" in detail:
        return "replay_consistency"
    if "config" in detail or "missing api keys" in detail:
        return "configuration"
    return "unknown"


def map_failure_to_state(category: FailureCategory) -> TaskState:
    if category == "timeout":
        return "timed_out"
    return "failed"


def emit_task_state(
    logger: logging.Logger,
    *,
    task_kind: TaskKind,
    task_name: str,
    state: TaskState,
    project_id: str | None = None,
    run_id: str | None = None,
    failure_category: FailureCategory = "none",
    level: int | None = None,
    **fields: Any,
) -> None:
    effective_level = level
    if effective_level is None:
        effective_level = logging.ERROR if state in {"failed", "timed_out"} else logging.INFO
    with runtime_context(
        correlation_id=ensure_correlation_id(),
        project_id=project_id,
        run_id=run_id,
        task_kind=task_kind,
        task_name=task_name,
    ):
        log_structured(
            logger,
            level=effective_level,
            event="task_state_changed",
            task_kind=task_kind,
            task_name=task_name,
            task_state=state,
            failure_category=failure_category,
            **fields,
        )


@contextmanager
def tracked_task(
    logger: logging.Logger,
    *,
    task_kind: TaskKind,
    task_name: str,
    project_id: str | None = None,
    run_id: str | None = None,
    **fields: Any,
) -> Iterator[RuntimeContextSnapshot]:
    effective_run_id = _clean_str(run_id) or uuid4().hex
    with runtime_context(
        correlation_id=ensure_correlation_id(),
        project_id=project_id,
        run_id=effective_run_id,
        task_kind=task_kind,
        task_name=task_name,
    ) as snapshot:
        log_structured(
            logger,
            event="task_state_changed",
            task_kind=task_kind,
            task_name=task_name,
            task_state="running",
            failure_category="none",
            **fields,
        )
        try:
            yield snapshot
        except Exception as exc:
            failure_category = classify_failure(exc)
            log_structured(
                logger,
                level=logging.ERROR,
                event="task_state_changed",
                task_kind=task_kind,
                task_name=task_name,
                task_state=map_failure_to_state(failure_category),
                failure_category=failure_category,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise
        else:
            log_structured(
                logger,
                event="task_state_changed",
                task_kind=task_kind,
                task_name=task_name,
                task_state="succeeded",
                failure_category="none",
            )


__all__ = [
    "RuntimeContextSnapshot",
    "classify_failure",
    "current_runtime_context",
    "emit_task_state",
    "ensure_correlation_id",
    "log_structured",
    "map_failure_to_state",
    "runtime_context",
    "tracked_task",
]
