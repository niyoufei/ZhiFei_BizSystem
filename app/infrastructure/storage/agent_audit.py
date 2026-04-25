from __future__ import annotations

import json
from pathlib import Path

from app import storage as storage_runtime
from app.contracts.agents import AgentExecutionRecord


def _audit_log_path() -> Path:
    return storage_runtime.DATA_DIR / "agent_audit_log.jsonl"


class AgentAuditStore:
    def append(self, record: AgentExecutionRecord) -> None:
        storage_runtime.ensure_data_dirs()
        path = _audit_log_path()
        payload = json.dumps(record.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
        lock = storage_runtime._get_path_lock(path)
        with lock, storage_runtime._exclusive_file_lock(path):
            with path.open("a", encoding="utf-8") as handle:
                handle.write(payload + "\n")
                handle.flush()

    def find_latest_success(
        self,
        *,
        agent_name: str,
        idempotency_key: str,
    ) -> AgentExecutionRecord | None:
        path = _audit_log_path()
        if not path.exists():
            return None
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return None
        for line in reversed(lines):
            raw = line.strip()
            if not raw:
                continue
            try:
                parsed = AgentExecutionRecord.model_validate_json(raw)
            except Exception:
                continue
            audit = parsed.audit
            if (
                audit.agent_name == agent_name
                and audit.idempotency_key == idempotency_key
                and audit.status in {"success", "cached"}
                and isinstance(parsed.output_payload, dict)
            ):
                return parsed
        return None
