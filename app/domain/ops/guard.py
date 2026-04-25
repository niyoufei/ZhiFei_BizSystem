from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.contracts.agents import OpsDiagnosticItem

DEFAULT_OPS_AGENTS_PATH = Path("build/ops_agents_status.json")
DEFAULT_DOCTOR_PATH = Path("build/doctor_summary.json")
DEFAULT_SOAK_PATH = Path("build/stability_soak_latest.json")
DEFAULT_PREFLIGHT_PATH = Path("build/trial_preflight_latest.json")
DEFAULT_ACCEPTANCE_PATH = Path("build/acceptance_summary.json")


def _read_json_payload(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _normalize_status(value: object) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"pass", "passed", "ok", "healthy", "ready", "success"}:
        return "pass"
    if raw in {"warn", "warning", "watch", "degraded"}:
        return "warn"
    if raw in {"fail", "failed", "error", "critical", "unhealthy", "not_ready"}:
        return "fail"
    return "unknown"


def _severity_from_status(status: str) -> str:
    return {
        "pass": "low",
        "warn": "medium",
        "fail": "high",
    }.get(status, "medium")


def _extract_status(payload: dict[str, Any] | None) -> str:
    if not isinstance(payload, dict):
        return "unknown"
    overall = payload.get("overall")
    if isinstance(overall, dict):
        normalized = _normalize_status(overall.get("status"))
        if normalized != "unknown":
            return normalized
    summary = payload.get("summary")
    if isinstance(summary, dict):
        normalized = _normalize_status(summary.get("status"))
        if normalized != "unknown":
            return normalized
    gate = payload.get("gate")
    if isinstance(gate, dict):
        normalized = _normalize_status(gate.get("status"))
        if normalized != "unknown":
            return normalized
    return _normalize_status(payload.get("status"))


def _extract_summary(source: str, payload: dict[str, Any] | None, status: str) -> str:
    if not isinstance(payload, dict):
        return f"{source} 未生成可读快照。"
    for key in ("message", "detail", "summary_text", "recommendation"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if source == "ops_agents":
        agents = payload.get("agents")
        if isinstance(agents, dict):
            failing = [
                name
                for name, row in agents.items()
                if isinstance(row, dict) and _normalize_status(row.get("status")) == "fail"
            ]
            warning = [
                name
                for name, row in agents.items()
                if isinstance(row, dict) and _normalize_status(row.get("status")) == "warn"
            ]
            if failing:
                return "ops_agents 检测到失败子项：" + "、".join(failing[:4])
            if warning:
                return "ops_agents 检测到告警子项：" + "、".join(warning[:4])
    return f"{source} 状态为 {status}。"


def _extract_recommendation(source: str, payload: dict[str, Any] | None, status: str) -> str:
    if isinstance(payload, dict):
        for key in ("recommendations", "actions", "next_steps"):
            value = payload.get(key)
            if isinstance(value, list):
                texts = [str(item).strip() for item in value if str(item).strip()]
                if texts:
                    return texts[0]
            if isinstance(value, dict):
                texts = [str(item).strip() for item in value.values() if str(item).strip()]
                if texts:
                    return texts[0]
        overall = payload.get("overall")
        if isinstance(overall, dict):
            recommendation = overall.get("recommendation")
            if isinstance(recommendation, str) and recommendation.strip():
                return recommendation.strip()
    if status == "fail":
        return f"优先处理 {source} 失败项，确认阻塞原因后再继续评分闭环。"
    if status == "warn":
        return f"复核 {source} 告警项，确认是否需要人工介入或补充材料。"
    return f"{source} 无阻断项，保持观测。"


class OpsGuardService:
    """运维巡检层只做读快照、归一化和守门，不参与评分裁决。"""

    def _resolve_payload(
        self,
        *,
        inline_payload: dict[str, Any] | None,
        explicit_path: str | None,
        default_path: Path,
    ) -> dict[str, Any] | None:
        if isinstance(inline_payload, dict):
            return dict(inline_payload)
        if explicit_path:
            return _read_json_payload(Path(explicit_path))
        return _read_json_payload(default_path)

    def build_triage_snapshot(
        self,
        *,
        ops_agents_payload: dict[str, Any] | None = None,
        doctor_payload: dict[str, Any] | None = None,
        soak_payload: dict[str, Any] | None = None,
        preflight_payload: dict[str, Any] | None = None,
        acceptance_payload: dict[str, Any] | None = None,
        ops_agents_json_path: str | None = None,
        doctor_json_path: str | None = None,
        soak_json_path: str | None = None,
        preflight_json_path: str | None = None,
        acceptance_json_path: str | None = None,
    ) -> dict[str, Any]:
        sources = {
            "ops_agents": self._resolve_payload(
                inline_payload=ops_agents_payload,
                explicit_path=ops_agents_json_path,
                default_path=DEFAULT_OPS_AGENTS_PATH,
            ),
            "doctor": self._resolve_payload(
                inline_payload=doctor_payload,
                explicit_path=doctor_json_path,
                default_path=DEFAULT_DOCTOR_PATH,
            ),
            "soak": self._resolve_payload(
                inline_payload=soak_payload,
                explicit_path=soak_json_path,
                default_path=DEFAULT_SOAK_PATH,
            ),
            "trial_preflight": self._resolve_payload(
                inline_payload=preflight_payload,
                explicit_path=preflight_json_path,
                default_path=DEFAULT_PREFLIGHT_PATH,
            ),
            "acceptance": self._resolve_payload(
                inline_payload=acceptance_payload,
                explicit_path=acceptance_json_path,
                default_path=DEFAULT_ACCEPTANCE_PATH,
            ),
        }
        diagnostics: list[OpsDiagnosticItem] = []
        for source, payload in sources.items():
            status = _extract_status(payload)
            diagnostics.append(
                OpsDiagnosticItem(
                    source=source,
                    status=status,
                    severity=_severity_from_status(status),
                    summary=_extract_summary(source, payload, status),
                    recommendation=_extract_recommendation(source, payload, status),
                )
            )
        statuses = [item.status for item in diagnostics]
        if any(status == "fail" for status in statuses):
            overall_status = "fail"
        elif any(status == "warn" for status in statuses):
            overall_status = "warn"
        elif any(status == "pass" for status in statuses):
            overall_status = "pass"
        else:
            overall_status = "unknown"
        recommended_actions: list[str] = []
        for item in diagnostics:
            if item.recommendation not in recommended_actions:
                recommended_actions.append(item.recommendation)
        return {
            "overall_status": overall_status,
            "severity": _severity_from_status(overall_status),
            "diagnostics": [item.model_dump(mode="json") for item in diagnostics],
            "recommended_actions": recommended_actions[:8],
        }
