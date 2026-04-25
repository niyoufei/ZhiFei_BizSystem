from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel

from app.application.task_runtime import classify_failure, emit_task_state, ensure_correlation_id
from app.contracts.agents import (
    AgentAuditFields,
    AgentExecutionRecord,
    AgentExecutionStatus,
    AgentRunResult,
    AgentSpec,
)
from app.contracts.task_runtime import FailureCategory, TaskState
from app.infrastructure.storage.agent_audit import AgentAuditStore


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _payload_digest(data: Any) -> str:
    return hashlib.sha256(_canonical_json(data).encode("utf-8")).hexdigest()


class ControlledAgent(ABC):
    spec: AgentSpec
    input_model: type[BaseModel]
    output_model: type[BaseModel]

    def build_idempotency_key(self, validated_input: BaseModel) -> str:
        payload = validated_input.model_dump(mode="json")
        return f"{self.spec.agent_name}:{_payload_digest(payload)[:24]}"

    @abstractmethod
    def execute(self, validated_input: BaseModel) -> BaseModel | dict[str, Any]:
        raise NotImplementedError


class ControlledAgentRunner:
    def __init__(self, *, audit_store: AgentAuditStore | None = None):
        self.audit_store = audit_store or AgentAuditStore()

    def _build_audit(
        self,
        *,
        agent: ControlledAgent,
        validated_input: BaseModel,
        run_id: str,
        status: AgentExecutionStatus,
        task_state: TaskState,
        failure_category: FailureCategory,
        started_at: str,
        finished_at: str,
        idempotency_key: str,
        input_digest: str,
        output_payload: dict[str, Any] | None,
        attempt_count: int,
        dry_run: bool,
        project_id: str | None,
        correlation_id: str | None,
        cache_hit_from_run_id: str | None = None,
    ) -> AgentAuditFields:
        output_digest = _payload_digest(output_payload) if output_payload is not None else None
        actor_type = str(getattr(validated_input, "actor_type", "system") or "system")
        actor_id = str(getattr(validated_input, "actor_id", "system") or "system")
        trigger_event = str(
            getattr(validated_input, "trigger_event", "manual_dry_run") or "manual_dry_run"
        )
        return AgentAuditFields(
            run_id=run_id,
            agent_name=agent.spec.agent_name,
            trigger_event=trigger_event,
            idempotency_key=idempotency_key,
            input_digest=input_digest,
            output_digest=output_digest,
            started_at=started_at,
            finished_at=finished_at,
            status=status,
            attempt_count=max(1, int(attempt_count)),
            dry_run=bool(dry_run),
            actor_type=actor_type,
            actor_id=actor_id,
            model_provider="rules",
            model_name="deterministic",
            prompt_version=None,
            cache_hit_from_run_id=cache_hit_from_run_id,
            task_state=task_state,
            failure_category=failure_category,
            correlation_id=correlation_id,
            project_id=project_id,
        )

    def _persist_record(
        self,
        *,
        agent: ControlledAgent,
        audit: AgentAuditFields,
        input_payload: dict[str, Any],
        output_payload: dict[str, Any] | None,
        error: str | None,
    ) -> None:
        self.audit_store.append(
            AgentExecutionRecord(
                audit=audit,
                spec=agent.spec,
                input_payload=input_payload,
                output_payload=output_payload,
                error=error,
            )
        )

    def run(
        self,
        *,
        agent: ControlledAgent,
        payload: dict[str, Any],
        reuse_cached: bool = True,
        dry_run: bool = True,
    ) -> AgentRunResult:
        validated_input = agent.input_model.model_validate(payload)
        input_payload = validated_input.model_dump(mode="json")
        input_digest = _payload_digest(input_payload)
        idempotency_key = agent.build_idempotency_key(validated_input)
        project_id = str(input_payload.get("project_id") or "").strip() or None
        correlation_id = ensure_correlation_id()
        if reuse_cached:
            cached = self.audit_store.find_latest_success(
                agent_name=agent.spec.agent_name,
                idempotency_key=idempotency_key,
            )
            if cached is not None:
                started_at = _utc_now_iso()
                finished_at = _utc_now_iso()
                run_id = str(uuid.uuid4())
                audit = self._build_audit(
                    agent=agent,
                    validated_input=validated_input,
                    run_id=run_id,
                    status="cached",
                    task_state="cached",
                    failure_category="none",
                    started_at=started_at,
                    finished_at=finished_at,
                    idempotency_key=idempotency_key,
                    input_digest=input_digest,
                    output_payload=dict(cached.output_payload or {}),
                    attempt_count=1,
                    dry_run=dry_run,
                    project_id=project_id,
                    correlation_id=correlation_id,
                    cache_hit_from_run_id=cached.audit.run_id,
                )
                self._persist_record(
                    agent=agent,
                    audit=audit,
                    input_payload=input_payload,
                    output_payload=dict(cached.output_payload or {}),
                    error=None,
                )
                emit_task_state(
                    logging.getLogger(__name__),
                    task_kind="agent",
                    task_name=agent.spec.agent_name,
                    state="cached",
                    project_id=project_id,
                    run_id=run_id,
                    cached_from_run_id=cached.audit.run_id,
                )
                return AgentRunResult(
                    spec=agent.spec,
                    audit=audit,
                    output=dict(cached.output_payload or {}),
                    cached=True,
                    error=None,
                )

        attempts = max(1, int(agent.spec.retry_policy.max_attempts))
        last_error: str | None = None
        last_status: AgentExecutionStatus = "error"
        last_failure_category: FailureCategory = "unknown"
        logger = logging.getLogger(__name__)
        for attempt in range(1, attempts + 1):
            started_at = _utc_now_iso()
            run_id = str(uuid.uuid4())
            emit_task_state(
                logger,
                task_kind="agent",
                task_name=agent.spec.agent_name,
                state="running",
                project_id=project_id,
                run_id=run_id,
                attempt=attempt,
                max_attempts=attempts,
            )
            try:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(agent.execute, validated_input)
                    raw_output = future.result(timeout=float(agent.spec.timeout_seconds))
                if isinstance(raw_output, BaseModel):
                    validated_output = agent.output_model.model_validate(
                        raw_output.model_dump(mode="json")
                    )
                else:
                    validated_output = agent.output_model.model_validate(raw_output)
                output_payload = validated_output.model_dump(mode="json")
                finished_at = _utc_now_iso()
                audit = self._build_audit(
                    agent=agent,
                    validated_input=validated_input,
                    run_id=run_id,
                    status="success",
                    task_state="succeeded",
                    failure_category="none",
                    started_at=started_at,
                    finished_at=finished_at,
                    idempotency_key=idempotency_key,
                    input_digest=input_digest,
                    output_payload=output_payload,
                    attempt_count=attempt,
                    dry_run=dry_run,
                    project_id=project_id,
                    correlation_id=correlation_id,
                )
                self._persist_record(
                    agent=agent,
                    audit=audit,
                    input_payload=input_payload,
                    output_payload=output_payload,
                    error=None,
                )
                emit_task_state(
                    logger,
                    task_kind="agent",
                    task_name=agent.spec.agent_name,
                    state="succeeded",
                    project_id=project_id,
                    run_id=run_id,
                    attempt=attempt,
                )
                return AgentRunResult(
                    spec=agent.spec,
                    audit=audit,
                    output=output_payload,
                    cached=False,
                    error=None,
                )
            except FuturesTimeoutError:
                last_status = "timeout"
                last_failure_category = "timeout"
                last_error = f"agent_timeout: {agent.spec.agent_name} exceeded {agent.spec.timeout_seconds:.1f}s"
                emit_task_state(
                    logger,
                    task_kind="agent",
                    task_name=agent.spec.agent_name,
                    state="timed_out",
                    project_id=project_id,
                    run_id=run_id,
                    failure_category="timeout",
                    attempt=attempt,
                    error=last_error,
                )
            except Exception as exc:  # noqa: BLE001
                last_status = "error"
                last_failure_category = classify_failure(exc)
                last_error = f"{type(exc).__name__}: {exc}"
                emit_task_state(
                    logger,
                    task_kind="agent",
                    task_name=agent.spec.agent_name,
                    state="failed",
                    project_id=project_id,
                    run_id=run_id,
                    failure_category=last_failure_category,
                    attempt=attempt,
                    error=last_error,
                )
            if attempt < attempts and agent.spec.retry_policy.backoff_seconds > 0:
                time.sleep(float(agent.spec.retry_policy.backoff_seconds))

        finished_at = _utc_now_iso()
        final_task_state: TaskState = "timed_out" if last_status == "timeout" else "failed"
        audit = self._build_audit(
            agent=agent,
            validated_input=validated_input,
            run_id=run_id,
            status=last_status,
            task_state=final_task_state,
            failure_category=last_failure_category,
            started_at=started_at,
            finished_at=finished_at,
            idempotency_key=idempotency_key,
            input_digest=input_digest,
            output_payload=None,
            attempt_count=attempts,
            dry_run=dry_run,
            project_id=project_id,
            correlation_id=correlation_id,
        )
        self._persist_record(
            agent=agent,
            audit=audit,
            input_payload=input_payload,
            output_payload=None,
            error=last_error,
        )
        return AgentRunResult(
            spec=agent.spec,
            audit=audit,
            output=None,
            cached=False,
            error=last_error,
        )
