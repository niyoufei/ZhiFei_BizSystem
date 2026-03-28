from __future__ import annotations

import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib import error, request
from urllib.parse import urlparse

OPS_AUDIT_PREPARATION_STATUSES = {"scoring_preparation", "draft", "created"}
OPS_AUDIT_SYNTHETIC_PREFIXES = ("ops_", "ops招标项目_", "e2e_")
OPS_RUNTIME_REPAIRABLE_ITEMS = frozenset(
    {
        "data_hygiene",
        "vision_parse_queue_healthy",
        "material_parse_backlog_ok",
    }
)
OPS_RUNTIME_RESTART_ITEMS = frozenset(
    {
        "vision_parse_queue_healthy",
        "material_parse_backlog_ok",
    }
)
OPS_SMOKE_RUNTIME_RETRY_SECONDS = 1800.0
OPS_SMOKE_RUNTIME_RETRY_STATE_PATH = Path("build/ops_agents_smoke_retry_state.json")
OPS_LEARNING_CALIBRATION_RETRY_SECONDS = 3600.0
OPS_LEARNING_CALIBRATION_DRIFT_ALERT_LEVELS = frozenset({"watch", "medium", "high"})
OPS_AGENT_NAMES = (
    "sre_watchdog",
    "data_hygiene",
    "runtime_repair",
    "project_flow",
    "tender_project_flow",
    "upload_flow",
    "scoring_quality",
    "evolution",
    "learning_calibration",
)
OPS_AGENT_DEFAULT_INTERVAL_SECONDS = 60.0
OPS_AGENT_STALE_GRACE_SECONDS = 90.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _health_learning_min_samples(summary: Dict[str, Any], fallback: int) -> int:
    return max(
        1,
        _to_int(
            summary.get("evolution_weight_min_samples"),
            max(1, int(fallback)),
        ),
    )


def _normalize_projects(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("projects", "items", "data"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return [x for x in rows if isinstance(x, dict)]
    return []


def _normalize_project_status(value: Any) -> str:
    return str(value or "").strip().lower()


def _normalize_project_name(value: Any) -> str:
    return str(value or "").strip().lower()


def _project_name_is_synthetic(name: Any) -> bool:
    normalized = _normalize_project_name(name)
    return any(normalized.startswith(prefix) for prefix in OPS_AUDIT_SYNTHETIC_PREFIXES)


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _project_updated_at(project: Dict[str, Any]) -> Optional[datetime]:
    return _parse_iso_datetime(project.get("updated_at") or project.get("created_at"))


def _select_projects_for_ops_audit(
    projects: List[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
    recent_hours: float = 72.0,
    max_projects: int = 24,
) -> List[Dict[str, Any]]:
    if not projects:
        return []
    current = now or datetime.now(timezone.utc)
    cutoff = current - timedelta(hours=max(1.0, float(recent_hours)))
    selected: List[Dict[str, Any]] = []
    for project in projects:
        if not isinstance(project, dict):
            continue
        if _project_name_is_synthetic(project.get("name")):
            continue
        updated_at = _project_updated_at(project)
        is_recent = bool(updated_at and updated_at >= cutoff)
        if is_recent:
            selected.append(project)
    selected.sort(
        key=lambda row: (
            _project_updated_at(row) or datetime.min.replace(tzinfo=timezone.utc),
            str(row.get("id") or ""),
        ),
        reverse=True,
    )
    return selected[: max(1, int(max_projects))]


def _is_local_url(url: str) -> bool:
    try:
        host = str(urlparse(url).hostname or "").strip().lower()
    except Exception:
        return False
    return host in {"127.0.0.1", "localhost", "::1"} or host.startswith("127.")


def _should_send_api_key(url: str, api_key: Optional[str]) -> bool:
    return bool(api_key) and not _is_local_url(url)


def _request_json(
    *,
    method: str,
    url: str,
    api_key: Optional[str] = None,
    timeout: float = 8.0,
    payload: Optional[Dict[str, Any]] = None,
    form_fields: Optional[Dict[str, Any]] = None,
    files: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    headers: Dict[str, str] = {"Accept": "application/json"}
    body: Optional[bytes] = None
    if _should_send_api_key(url, api_key):
        headers["X-API-Key"] = api_key
    if files:
        boundary = f"----CodexBoundary{int(time.time() * 1000)}"
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        chunks: List[bytes] = []
        for key, value in (form_fields or {}).items():
            chunks.extend(
                [
                    f"--{boundary}\r\n".encode("utf-8"),
                    (f'Content-Disposition: form-data; name="{key}"\r\n\r\n').encode("utf-8"),
                    str(value).encode("utf-8"),
                    b"\r\n",
                ]
            )
        for spec in files:
            field = str(spec.get("field") or "file")
            filename = str(spec.get("filename") or "upload.bin")
            content_type = str(spec.get("content_type") or "application/octet-stream")
            content = spec.get("content") or b""
            if isinstance(content, str):
                content = content.encode("utf-8")
            chunks.extend(
                [
                    f"--{boundary}\r\n".encode("utf-8"),
                    (
                        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
                    ).encode("utf-8"),
                    f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"),
                    bytes(content),
                    b"\r\n",
                ]
            )
        chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
        body = b"".join(chunks)
    elif payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(url=url, method=method, headers=headers, data=body)
    start = time.monotonic()
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            elapsed = int((time.monotonic() - start) * 1000)
            parsed: Any
            try:
                parsed = json.loads(text) if text.strip() else {}
            except Exception:
                parsed = {"_raw_text": text}
            return {
                "ok": True,
                "status_code": int(resp.status),
                "elapsed_ms": elapsed,
                "json": parsed,
                "error": None,
            }
    except error.HTTPError as exc:
        elapsed = int((time.monotonic() - start) * 1000)
        raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        parsed: Any
        try:
            parsed = json.loads(raw) if raw.strip() else {}
        except Exception:
            parsed = {"_raw_text": raw}
        return {
            "ok": False,
            "status_code": int(exc.code),
            "elapsed_ms": elapsed,
            "json": parsed,
            "error": f"HTTPError: {exc.code}",
        }
    except Exception as exc:  # noqa: BLE001
        elapsed = int((time.monotonic() - start) * 1000)
        return {
            "ok": False,
            "status_code": 0,
            "elapsed_ms": elapsed,
            "json": {},
            "error": f"{type(exc).__name__}: {exc}",
        }


def _status(pass_flag: bool, warn_flag: bool = False) -> str:
    if pass_flag:
        return "pass"
    if warn_flag:
        return "warn"
    return "fail"


def _placeholder_agent(name: str, message: str) -> Dict[str, Any]:
    return {
        "name": name,
        "status": "fail",
        "duration_ms": 0,
        "checks": {},
        "actions": {},
        "metrics": {},
        "recommendations": [message],
    }


def _ensure_agent_coverage(agents: Dict[str, Dict[str, Any]], *, reason: str) -> List[str]:
    missing = [name for name in OPS_AGENT_NAMES if name not in agents]
    for name in missing:
        agents[name] = _placeholder_agent(name, reason)
    return missing


def _json_object(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _artifact_latest_created_at(payload: Dict[str, Any], artifact: str) -> Optional[datetime]:
    rows = payload.get("version_history")
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("artifact") or "").strip() != str(artifact or "").strip():
            continue
        return _parse_iso_datetime(row.get("latest_created_at"))
    return None


def _retry_due(
    last_run_at: Any,
    *,
    cooldown_seconds: float = OPS_LEARNING_CALIBRATION_RETRY_SECONDS,
    now: Optional[datetime] = None,
) -> bool:
    last_dt = _parse_iso_datetime(last_run_at)
    if last_dt is None:
        return True
    current = now or datetime.now(timezone.utc)
    return (current - last_dt).total_seconds() >= max(60.0, float(cooldown_seconds))


def _has_project_specific_calibrator(version: Any) -> bool:
    text = str(version or "").strip()
    return bool(text) and not text.startswith("prior_")


def _self_check_failed_items(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    items = payload.get("items")
    if not isinstance(items, list):
        return []
    return [row for row in items if isinstance(row, dict) and not bool(row.get("ok"))]


def _self_check_failed_names(payload: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for row in _self_check_failed_items(payload):
        name = str(row.get("name") or "").strip()
        if name and name not in out:
            out.append(name)
    return out


def _self_check_required_fail_names(payload: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for row in _self_check_failed_items(payload):
        name = str(row.get("name") or "").strip()
        if not name or name in out:
            continue
        if bool(row.get("required")):
            out.append(name)
    return out


def _self_check_failed_optional_items(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in _self_check_failed_items(payload):
        if bool(row.get("required")):
            continue
        out.append(row)
    return out


def _self_check_item(payload: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
    target = str(name or "").strip()
    if not target:
        return None
    for row in _self_check_failed_items(payload):
        row_name = str(row.get("name") or "").strip()
        if row_name == target:
            return row
    return None


def _parse_bool_detail_flag(detail: Any, key: str) -> Optional[bool]:
    text = str(detail or "").strip().lower()
    target = str(key or "").strip().lower()
    if not text or not target:
        return None
    for suffix in ("=true", ":true", ": true"):
        if f"{target}{suffix}" in text:
            return True
    for suffix in ("=false", ":false", ": false"):
        if f"{target}{suffix}" in text:
            return False
    return None


def _material_parse_queue_is_busy(payload: Dict[str, Any]) -> bool:
    failed_names = set(_self_check_failed_names(payload))
    if not failed_names.intersection(OPS_RUNTIME_RESTART_ITEMS):
        return False
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        return False
    parse_summary = summary.get("parse_job_summary")
    if not isinstance(parse_summary, dict):
        return False
    backlog = _to_int(parse_summary.get("backlog"), 0)
    status_counts = parse_summary.get("status_counts")
    if not isinstance(status_counts, dict):
        return False
    processing = _to_int(status_counts.get("processing"), 0)
    vision_item = _self_check_item(payload, "vision_parse_queue_healthy")
    worker_alive = _parse_bool_detail_flag((vision_item or {}).get("detail"), "worker")
    return worker_alive is True and backlog > 0 and processing > 0


def _runtime_restart_items_needing_restart(
    payload: Dict[str, Any],
    repairable_names: List[str],
) -> List[str]:
    candidates = [name for name in repairable_names if name in OPS_RUNTIME_RESTART_ITEMS]
    if not candidates:
        return []
    if _material_parse_queue_is_busy(payload):
        return []
    return candidates


def _self_check_item_category(row: Dict[str, Any]) -> str:
    category = str(row.get("category") or "").strip().lower()
    if category:
        return category
    name = str(row.get("name") or "").strip().lower()
    if name.startswith("parser_"):
        return "parser"
    if name.startswith("openai_") or name.startswith("gemini_"):
        return "llm"
    if name.startswith("auth_") or name.startswith("rate_limit_") or name.startswith("runtime_"):
        return "security"
    if name.startswith("project_"):
        return "project"
    if (
        name.startswith("vision_")
        or name.startswith("material_parse_")
        or name.startswith("gpt_parse_")
        or name.startswith("structured_summary_")
    ):
        return "async_parse"
    if name.startswith("data_"):
        return "data"
    return "other"


def _classify_optional_warning_items(items: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    groups: Dict[str, List[str]] = {
        "repairable": [],
        "async_parse": [],
        "parser": [],
        "llm": [],
        "security": [],
        "project": [],
        "data": [],
        "other": [],
    }
    for row in items:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        category = _self_check_item_category(row)
        if name in OPS_RUNTIME_REPAIRABLE_ITEMS:
            groups["repairable"].append(name)
        if category in groups:
            groups[category].append(name)
        else:
            groups["other"].append(name)
    return groups


def _run_restart_command(restart_cmd: List[str]) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "attempted": False,
        "ok": False,
        "returncode": None,
        "error": None,
    }
    if not restart_cmd:
        return result
    result["attempted"] = True
    try:
        proc = subprocess.run(
            restart_cmd,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        result["returncode"] = int(proc.returncode)
        result["ok"] = proc.returncode == 0
        if proc.returncode != 0:
            result["error"] = (proc.stderr or proc.stdout or "").strip()[:600]
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def _load_smoke_runtime_retry_state(path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    retry_path = path or OPS_SMOKE_RUNTIME_RETRY_STATE_PATH
    try:
        payload = json.loads(retry_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for key, value in payload.items():
        if isinstance(key, str) and isinstance(value, dict):
            out[key] = dict(value)
    return out


def _save_smoke_runtime_retry_state(
    state: Dict[str, Dict[str, Any]],
    path: Optional[Path] = None,
) -> None:
    retry_path = path or OPS_SMOKE_RUNTIME_RETRY_STATE_PATH
    retry_path.parent.mkdir(parents=True, exist_ok=True)
    retry_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _smoke_runtime_retry_cooldown_remaining(
    name: str,
    *,
    cooldown_seconds: float,
    path: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> float:
    state = _load_smoke_runtime_retry_state(path)
    item = state.get(str(name or "").strip()) or {}
    attempted_at = _parse_iso_datetime(item.get("attempted_at"))
    if attempted_at is None:
        return 0.0
    current = now or datetime.now(timezone.utc)
    elapsed = max(0.0, (current - attempted_at).total_seconds())
    return max(0.0, float(cooldown_seconds) - elapsed)


def _record_smoke_runtime_retry_attempt(
    name: str,
    *,
    outcome: str,
    path: Optional[Path] = None,
) -> None:
    key = str(name or "").strip()
    if not key:
        return
    state = _load_smoke_runtime_retry_state(path)
    state[key] = {
        "attempted_at": _now_iso(),
        "outcome": str(outcome or "").strip() or "attempted",
    }
    _save_smoke_runtime_retry_state(state, path)


def _run_smoke_agent_with_runtime_retry(
    *,
    name: str,
    runner: Callable[..., Dict[str, Any]],
    kwargs: Dict[str, Any],
    auto_repair: bool,
    restart_cmd: List[str],
    retry_budget: Dict[str, bool],
    retry_cooldown_seconds: float = OPS_SMOKE_RUNTIME_RETRY_SECONDS,
    retry_state_path: Optional[Path] = None,
) -> Dict[str, Any]:
    try:
        first_result = runner(**kwargs)
    except Exception as exc:  # noqa: BLE001
        first_result = _placeholder_agent(name, f"agent_exception: {type(exc).__name__}: {exc}")
    actions = dict(first_result.get("actions") or {})
    retry_info: Dict[str, Any] = {
        "attempted": False,
        "budget_consumed": bool(retry_budget.get("used")),
        "restart": {
            "attempted": False,
            "ok": False,
            "returncode": None,
            "error": None,
        },
        "initial_status": str(first_result.get("status") or "fail"),
        "retry_status": None,
        "recovered": False,
        "cooldown_skipped": False,
        "cooldown_remaining_seconds": 0,
    }
    if (
        str(first_result.get("status") or "") != "fail"
        or not auto_repair
        or retry_budget.get("used")
    ):
        actions["runtime_retry"] = retry_info
        first_result["actions"] = actions
        return first_result

    cooldown_remaining = _smoke_runtime_retry_cooldown_remaining(
        name,
        cooldown_seconds=retry_cooldown_seconds,
        path=retry_state_path,
    )
    if cooldown_remaining > 0:
        retry_info["cooldown_skipped"] = True
        retry_info["cooldown_remaining_seconds"] = int(round(cooldown_remaining))
        actions["runtime_retry"] = retry_info
        first_result["actions"] = actions
        recommendations = list(first_result.get("recommendations") or [])
        cooldown_minutes = max(1, int((cooldown_remaining + 59.0) // 60.0))
        cooldown_msg = (
            "运行态自动重启重试仍处于冷却期，已跳过重复重启以避免周期性打断页面；"
            f"约 {cooldown_minutes} 分钟后才会再次尝试。"
        )
        if cooldown_msg not in recommendations:
            recommendations.append(cooldown_msg)
        first_result["recommendations"] = recommendations
        return first_result

    retry_budget["used"] = True
    retry_info["attempted"] = True
    retry_info["budget_consumed"] = True
    restart_result = _run_restart_command(restart_cmd)
    retry_info["restart"] = restart_result
    _record_smoke_runtime_retry_attempt(
        name,
        outcome="restart_ok" if restart_result.get("ok") else "restart_failed",
        path=retry_state_path,
    )
    if restart_result.get("ok"):
        try:
            retried = runner(**kwargs)
        except Exception as exc:  # noqa: BLE001
            retried = _placeholder_agent(
                name, f"agent_exception_after_retry: {type(exc).__name__}: {exc}"
            )
        retry_info["retry_status"] = str(retried.get("status") or "fail")
        retry_info["recovered"] = str(retried.get("status") or "") != "fail"
        retry_actions = dict(retried.get("actions") or {})
        retry_actions["runtime_retry"] = retry_info
        retried["actions"] = retry_actions
        if retry_info["recovered"]:
            recommendations = list(retried.get("recommendations") or [])
            recovery_msg = "运行态已自动重启并完成 smoke 重试恢复。"
            if recovery_msg not in recommendations:
                recommendations.insert(0, recovery_msg)
            retried["recommendations"] = recommendations
        else:
            recommendations = list(retried.get("recommendations") or [])
            retry_fail_msg = "运行态已自动重启并重试 smoke，但主链仍未恢复。"
            if retry_fail_msg not in recommendations:
                recommendations.append(retry_fail_msg)
            retried["recommendations"] = recommendations
        return retried

    recommendations = list(first_result.get("recommendations") or [])
    restart_fail_msg = "已尝试自动重启后重试 smoke，但运行态重启失败。"
    if restart_fail_msg not in recommendations:
        recommendations.append(restart_fail_msg)
    first_result["recommendations"] = recommendations
    actions["runtime_retry"] = retry_info
    first_result["actions"] = actions
    return first_result


def ops_agents_snapshot_is_stale(
    generated_at: Any,
    *,
    now: Optional[datetime] = None,
    interval_seconds: Optional[float] = None,
    grace_seconds: float = OPS_AGENT_STALE_GRACE_SECONDS,
) -> bool:
    generated = _parse_iso_datetime(generated_at)
    if generated is None:
        return True
    current = now or datetime.now(timezone.utc)
    interval = max(1.0, float(interval_seconds or OPS_AGENT_DEFAULT_INTERVAL_SECONDS))
    grace = max(30.0, float(grace_seconds))
    age_seconds = max(0.0, (current - generated).total_seconds())
    stale_after = max(interval * 2.0, interval + grace)
    return age_seconds > stale_after


def _run_sre_watchdog(
    *,
    base_url: str,
    api_key: Optional[str],
    timeout: float,
    auto_repair: bool,
    restart_cmd: List[str],
    requester: Callable[..., Dict[str, Any]],
) -> Dict[str, Any]:
    started = time.monotonic()
    checks: Dict[str, Dict[str, Any]] = {}
    for path in ("/health", "/ready", "/api/v1/system/self_check"):
        checks[path] = requester(
            method="GET",
            url=f"{base_url}{path}",
            api_key=api_key,
            timeout=timeout,
        )
    self_check_ok = bool((checks.get("/api/v1/system/self_check") or {}).get("json", {}).get("ok"))
    pass_flag = (
        all(
            int((checks.get(path) or {}).get("status_code") or 0) == 200
            for path in ("/health", "/ready", "/api/v1/system/self_check")
        )
        and self_check_ok
    )

    restart_result: Dict[str, Any] = {
        "attempted": False,
        "ok": False,
        "returncode": None,
        "error": None,
    }
    if not pass_flag and auto_repair and restart_cmd:
        restart_result = _run_restart_command(restart_cmd)
        # restart 后复测
        checks["/health_after_restart"] = requester(
            method="GET",
            url=f"{base_url}/health",
            api_key=api_key,
            timeout=timeout,
        )
        checks["/ready_after_restart"] = requester(
            method="GET",
            url=f"{base_url}/ready",
            api_key=api_key,
            timeout=timeout,
        )
        checks["/self_check_after_restart"] = requester(
            method="GET",
            url=f"{base_url}/api/v1/system/self_check",
            api_key=api_key,
            timeout=timeout,
        )
        self_check_ok = bool(
            (checks.get("/self_check_after_restart") or {}).get("json", {}).get("ok")
        )
        pass_flag = (
            int((checks.get("/health_after_restart") or {}).get("status_code") or 0) == 200
            and int((checks.get("/ready_after_restart") or {}).get("status_code") or 0) == 200
            and int((checks.get("/self_check_after_restart") or {}).get("status_code") or 0) == 200
            and self_check_ok
        )

    recommendations: List[str] = []
    if not pass_flag:
        recommendations.append("SRE监控发现服务不可用，建议检查启动日志并修复运行环境。")
    elif restart_result["attempted"]:
        recommendations.append("SRE监控已自动完成重启恢复，请关注近期稳定性波动。")

    return {
        "name": "sre_watchdog",
        "status": _status(pass_flag),
        "duration_ms": int((time.monotonic() - started) * 1000),
        "checks": checks,
        "actions": {"restart": restart_result},
        "recommendations": recommendations,
    }


def _run_data_hygiene_agent(
    *,
    base_url: str,
    api_key: Optional[str],
    timeout: float,
    auto_repair: bool,
    requester: Callable[..., Dict[str, Any]],
) -> Dict[str, Any]:
    started = time.monotonic()
    audit_before = requester(
        method="GET",
        url=f"{base_url}/api/v1/system/data_hygiene",
        api_key=api_key,
        timeout=timeout,
    )
    if int(audit_before.get("status_code") or 0) != 200:
        return {
            "name": "data_hygiene",
            "status": "fail",
            "duration_ms": int((time.monotonic() - started) * 1000),
            "checks": {"audit_before": audit_before},
            "actions": {"repair": {"attempted": False, "ok": False}},
            "recommendations": ["数据卫生接口不可用，建议先修复系统状态接口。"],
        }

    orphan_before = _to_int((audit_before.get("json") or {}).get("orphan_records_total"))
    repair_action = {"attempted": False, "ok": False, "status_code": None}
    if orphan_before > 0 and auto_repair:
        repair_action["attempted"] = True
        repair_resp = requester(
            method="POST",
            url=f"{base_url}/api/v1/system/data_hygiene/repair",
            api_key=api_key,
            timeout=timeout,
        )
        repair_action["status_code"] = int(repair_resp.get("status_code") or 0)
        repair_action["ok"] = int(repair_resp.get("status_code") or 0) == 200

    audit_after = requester(
        method="GET",
        url=f"{base_url}/api/v1/system/data_hygiene",
        api_key=api_key,
        timeout=timeout,
    )
    orphan_after = _to_int((audit_after.get("json") or {}).get("orphan_records_total"))
    pass_flag = int(audit_after.get("status_code") or 0) == 200 and orphan_after == 0
    warn_flag = orphan_after > 0 and not auto_repair

    recommendations: List[str] = []
    if orphan_after > 0:
        recommendations.append(f"仍有孤儿数据 {orphan_after} 条，建议执行数据卫生修复。")
    elif orphan_before > 0 and pass_flag:
        recommendations.append("数据卫生已自动修复，建议纳入定时巡检。")

    return {
        "name": "data_hygiene",
        "status": _status(pass_flag, warn_flag=warn_flag),
        "duration_ms": int((time.monotonic() - started) * 1000),
        "checks": {"audit_before": audit_before, "audit_after": audit_after},
        "actions": {"repair": repair_action},
        "metrics": {"orphan_before": orphan_before, "orphan_after": orphan_after},
        "recommendations": recommendations,
    }


def _run_runtime_repair_agent(
    *,
    base_url: str,
    api_key: Optional[str],
    timeout: float,
    auto_repair: bool,
    restart_cmd: List[str],
    requester: Callable[..., Dict[str, Any]],
) -> Dict[str, Any]:
    started = time.monotonic()
    checks: Dict[str, Any] = {}
    actions: Dict[str, Any] = {
        "repair_data_hygiene": {"attempted": False, "ok": False, "status_code": None},
        "restart_runtime": {
            "attempted": False,
            "ok": False,
            "returncode": None,
            "error": None,
        },
    }
    self_check_before = requester(
        method="GET",
        url=f"{base_url}/api/v1/system/self_check",
        api_key=api_key,
        timeout=timeout,
    )
    checks["self_check_before"] = self_check_before
    if int(self_check_before.get("status_code") or 0) != 200:
        return {
            "name": "runtime_repair",
            "status": "fail",
            "duration_ms": int((time.monotonic() - started) * 1000),
            "checks": checks,
            "actions": actions,
            "metrics": {},
            "recommendations": ["运行态自修复无法读取 self_check，建议先恢复系统状态接口。"],
        }

    payload_before = self_check_before.get("json") or {}
    failed_before = _self_check_failed_names(payload_before)
    required_failed_before = _self_check_required_fail_names(payload_before)
    optional_failed_before = _self_check_failed_optional_items(payload_before)
    optional_groups_before = _classify_optional_warning_items(optional_failed_before)
    repairable_before = list(optional_groups_before.get("repairable") or [])
    restartable_before = _runtime_restart_items_needing_restart(payload_before, repairable_before)
    non_repairable_before = [
        name for name in failed_before if name not in OPS_RUNTIME_REPAIRABLE_ITEMS
    ]
    async_parse_busy_before = _material_parse_queue_is_busy(payload_before)

    if auto_repair and "data_hygiene" in repairable_before:
        repair_resp = requester(
            method="POST",
            url=f"{base_url}/api/v1/system/data_hygiene/repair",
            api_key=api_key,
            timeout=max(10.0, timeout),
        )
        actions["repair_data_hygiene"] = {
            "attempted": True,
            "ok": int(repair_resp.get("status_code") or 0) == 200,
            "status_code": int(repair_resp.get("status_code") or 0),
        }
        checks["data_hygiene_repair"] = repair_resp

    if auto_repair and restartable_before:
        actions["restart_runtime"] = _run_restart_command(restart_cmd)

    self_check_after = self_check_before
    if actions["repair_data_hygiene"]["attempted"] or actions["restart_runtime"]["attempted"]:
        self_check_after = requester(
            method="GET",
            url=f"{base_url}/api/v1/system/self_check",
            api_key=api_key,
            timeout=timeout,
        )
        checks["self_check_after"] = self_check_after

    if int(self_check_after.get("status_code") or 0) != 200:
        return {
            "name": "runtime_repair",
            "status": "fail",
            "duration_ms": int((time.monotonic() - started) * 1000),
            "checks": checks,
            "actions": actions,
            "metrics": {
                "failed_before_count": len(failed_before),
                "repairable_before_count": len(repairable_before),
                "required_failed_before_count": len(required_failed_before),
            },
            "recommendations": [
                "运行态自修复后仍无法读取 self_check，建议检查服务日志和启动链路。"
            ],
        }

    payload_after = self_check_after.get("json") or {}
    failed_after = _self_check_failed_names(payload_after)
    required_failed_after = _self_check_required_fail_names(payload_after)
    optional_failed_after = _self_check_failed_optional_items(payload_after)
    optional_groups_after = _classify_optional_warning_items(optional_failed_after)
    repairable_after = list(optional_groups_after.get("repairable") or [])
    restartable_after = _runtime_restart_items_needing_restart(payload_after, repairable_after)
    non_repairable_after = [
        name for name in failed_after if name not in OPS_RUNTIME_REPAIRABLE_ITEMS
    ]
    async_parse_busy_after = _material_parse_queue_is_busy(payload_after)
    auto_fixed = [name for name in failed_before if name not in failed_after]

    pass_flag = not failed_after
    warn_flag = False
    if not pass_flag:
        if not repairable_after and not required_failed_after:
            warn_flag = True
        elif repairable_after and not restartable_after and async_parse_busy_after:
            warn_flag = True
        elif repairable_after and not auto_repair:
            warn_flag = True

    recommendations: List[str] = []
    if required_failed_after:
        recommendations.append(
            "运行态仍存在必需项失败，说明服务主链异常，建议优先检查 health/ready/self_check 依赖。"
        )
    elif repairable_after and not restartable_after and async_parse_busy_after:
        recommendations.append(
            "解析队列当前处于繁忙处理态，已跳过自动重启以避免打断多文件上传；"
            "建议继续观察队列是否持续消化。"
        )
    elif repairable_after:
        recommendations.append(
            "运行态自修复未完全恢复："
            + "、".join(repairable_after)
            + " 仍异常，建议人工检查解析队列与服务日志。"
        )
    elif non_repairable_after:
        category_summaries = []
        for label, key in (
            ("解析依赖", "parser"),
            ("LLM配置", "llm"),
            ("安全配置", "security"),
            ("项目态", "project"),
            ("数据态", "data"),
            ("其他", "other"),
        ):
            names = optional_groups_after.get(key) or []
            if names:
                category_summaries.append(label + ":" + "、".join(names))
        recommendations.append(
            "运行态已完成可修复项处理，但仍有仅告警项："
            + "；".join(category_summaries or ["、".join(non_repairable_after)])
            + "，当前未执行自动修复。"
        )
    elif auto_fixed:
        recommendations.append(
            "运行态问题已自动修复：" + "、".join(auto_fixed) + "，建议继续观察后续自检快照。"
        )
    else:
        recommendations.append("运行态巡检正常，未发现需要自动修复的问题。")

    return {
        "name": "runtime_repair",
        "status": _status(pass_flag, warn_flag=warn_flag),
        "duration_ms": int((time.monotonic() - started) * 1000),
        "checks": checks,
        "actions": actions,
        "metrics": {
            "failed_before_count": len(failed_before),
            "failed_after_count": len(failed_after),
            "required_failed_before_count": len(required_failed_before),
            "required_failed_after_count": len(required_failed_after),
            "repairable_before_count": len(repairable_before),
            "repairable_after_count": len(repairable_after),
            "restartable_before_count": len(restartable_before),
            "restartable_after_count": len(restartable_after),
            "optional_before_count": len(optional_failed_before),
            "optional_after_count": len(optional_failed_after),
            "non_repairable_before_count": len(non_repairable_before),
            "non_repairable_after_count": len(non_repairable_after),
            "optional_parser_after_count": len(optional_groups_after.get("parser") or []),
            "optional_llm_after_count": len(optional_groups_after.get("llm") or []),
            "optional_security_after_count": len(optional_groups_after.get("security") or []),
            "optional_project_after_count": len(optional_groups_after.get("project") or []),
            "optional_data_after_count": len(optional_groups_after.get("data") or []),
            "optional_other_after_count": len(optional_groups_after.get("other") or []),
            "auto_fixed_count": len(auto_fixed),
            "async_parse_busy_before_count": int(async_parse_busy_before),
            "async_parse_busy_after_count": int(async_parse_busy_after),
        },
        "recommendations": recommendations,
    }


def _run_project_flow_agent(
    *,
    base_url: str,
    api_key: Optional[str],
    timeout: float,
    requester: Callable[..., Dict[str, Any]],
) -> Dict[str, Any]:
    started = time.monotonic()
    checks: Dict[str, Any] = {}
    actions: Dict[str, Any] = {"create": {}, "delete": {}}
    smoke_name = f"OPS_SMOKE_{int(time.time() * 1000)}"

    projects_before = requester(
        method="GET",
        url=f"{base_url}/api/v1/projects",
        api_key=api_key,
        timeout=timeout,
    )
    checks["projects_before"] = projects_before
    if int(projects_before.get("status_code") or 0) != 200:
        return {
            "name": "project_flow",
            "status": "fail",
            "duration_ms": int((time.monotonic() - started) * 1000),
            "checks": checks,
            "actions": actions,
            "metrics": {},
            "recommendations": ["无法读取项目列表，项目主链 smoke 中断。"],
        }

    create_resp = requester(
        method="POST",
        url=f"{base_url}/api/v1/projects",
        api_key=api_key,
        timeout=max(10.0, timeout),
        payload={"name": smoke_name},
    )
    checks["create"] = create_resp
    actions["create"] = {
        "project_name": smoke_name,
        "ok": int(create_resp.get("status_code") or 0) == 200,
        "status_code": int(create_resp.get("status_code") or 0),
    }

    project_id = str((create_resp.get("json") or {}).get("id") or "").strip()
    created_ok = bool(project_id) and int(create_resp.get("status_code") or 0) == 200
    listed_after_create = False
    removed_after_delete = False

    if created_ok:
        projects_after_create = requester(
            method="GET",
            url=f"{base_url}/api/v1/projects",
            api_key=api_key,
            timeout=timeout,
        )
        checks["projects_after_create"] = projects_after_create
        projects_rows = _normalize_projects(projects_after_create.get("json"))
        listed_after_create = any(
            str(row.get("id") or "").strip() == project_id for row in projects_rows
        )

        delete_resp = requester(
            method="DELETE",
            url=f"{base_url}/api/v1/projects/{project_id}",
            api_key=api_key,
            timeout=max(10.0, timeout),
        )
        checks["delete"] = delete_resp
        delete_ok = int(delete_resp.get("status_code") or 0) in {200, 204}
        actions["delete"] = {
            "project_id": project_id,
            "ok": delete_ok,
            "status_code": int(delete_resp.get("status_code") or 0),
        }
        if delete_ok:
            projects_after_delete = requester(
                method="GET",
                url=f"{base_url}/api/v1/projects",
                api_key=api_key,
                timeout=timeout,
            )
            checks["projects_after_delete"] = projects_after_delete
            projects_rows = _normalize_projects(projects_after_delete.get("json"))
            removed_after_delete = not any(
                str(row.get("id") or "").strip() == project_id for row in projects_rows
            )
    else:
        delete_ok = False

    pass_flag = created_ok and listed_after_create and delete_ok and removed_after_delete
    recommendations: List[str] = []
    if not pass_flag:
        create_status = int(create_resp.get("status_code") or 0)
        if create_status in {401, 403}:
            recommendations.append("项目主链 smoke 缺少足够权限，请使用本机可信请求或 admin 权限。")
        elif create_status >= 400:
            recommendations.append("项目创建主链 smoke 失败，说明创建项目链路未通过自动巡检。")
        elif created_ok and not listed_after_create:
            recommendations.append("项目创建后未能在列表中回显，项目选择链路存在异常。")
        elif created_ok and delete_ok and not removed_after_delete:
            recommendations.append("项目 smoke 删除后仍残留在列表中，项目清理链路存在异常。")
        else:
            recommendations.append("项目主链 smoke 失败，请检查项目创建/删除接口和历史数据。")
    else:
        recommendations.append("项目创建/删除主链 smoke 正常。")

    return {
        "name": "project_flow",
        "status": _status(pass_flag),
        "duration_ms": int((time.monotonic() - started) * 1000),
        "checks": checks,
        "actions": actions,
        "metrics": {
            "created_ok": int(created_ok),
            "listed_after_create": int(listed_after_create),
            "delete_ok": int(delete_ok),
            "removed_after_delete": int(removed_after_delete),
        },
        "recommendations": recommendations,
    }


def _run_tender_project_flow_agent(
    *,
    base_url: str,
    api_key: Optional[str],
    timeout: float,
    requester: Callable[..., Dict[str, Any]],
    max_elapsed_ms: int = 15000,
) -> Dict[str, Any]:
    started = time.monotonic()
    checks: Dict[str, Any] = {}
    actions: Dict[str, Any] = {"create_from_tender": {}, "delete": {}}
    smoke_name = f"OPS招标项目_{int(time.time() * 1000)}"
    create_resp = requester(
        method="POST",
        url=f"{base_url}/api/v1/projects/create_from_tender",
        api_key=api_key,
        timeout=max(15.0, timeout),
        files=[
            {
                "field": "file",
                "filename": "ops_tender_smoke.txt",
                "content_type": "text/plain",
                "content": f"项目名称：{smoke_name}\n工程名称：{smoke_name}\n招标范围：测试范围\n",
            }
        ],
    )
    checks["create_from_tender"] = create_resp
    create_json = create_resp.get("json") or {}
    project_row = create_json.get("project") if isinstance(create_json, dict) else {}
    project_id = str(((project_row or {}).get("id")) or "").strip()
    inferred_name = str((create_json or {}).get("inferred_name") or "").strip()
    created_ok = bool(project_id) and int(create_resp.get("status_code") or 0) == 200
    inferred_ok = inferred_name == smoke_name
    elapsed_ok = int(create_resp.get("elapsed_ms") or 0) <= int(max_elapsed_ms)
    actions["create_from_tender"] = {
        "project_name": smoke_name,
        "project_id": project_id,
        "ok": created_ok,
        "elapsed_ms": int(create_resp.get("elapsed_ms") or 0),
        "elapsed_ok": elapsed_ok,
        "status_code": int(create_resp.get("status_code") or 0),
    }

    listed_after_create = False
    removed_after_delete = False
    delete_ok = False
    if created_ok:
        projects_after_create = requester(
            method="GET",
            url=f"{base_url}/api/v1/projects",
            api_key=api_key,
            timeout=timeout,
        )
        checks["projects_after_create"] = projects_after_create
        projects_rows = _normalize_projects(projects_after_create.get("json"))
        listed_after_create = any(
            str(row.get("id") or "").strip() == project_id for row in projects_rows
        )
        delete_resp = requester(
            method="DELETE",
            url=f"{base_url}/api/v1/projects/{project_id}",
            api_key=api_key,
            timeout=max(10.0, timeout),
        )
        checks["delete"] = delete_resp
        delete_ok = int(delete_resp.get("status_code") or 0) in {200, 204}
        actions["delete"] = {
            "project_id": project_id,
            "ok": delete_ok,
            "status_code": int(delete_resp.get("status_code") or 0),
        }
        if delete_ok:
            projects_after_delete = requester(
                method="GET",
                url=f"{base_url}/api/v1/projects",
                api_key=api_key,
                timeout=timeout,
            )
            checks["projects_after_delete"] = projects_after_delete
            projects_rows = _normalize_projects(projects_after_delete.get("json"))
            removed_after_delete = not any(
                str(row.get("id") or "").strip() == project_id for row in projects_rows
            )

    pass_flag = (
        created_ok
        and inferred_ok
        and listed_after_create
        and delete_ok
        and removed_after_delete
        and elapsed_ok
    )
    recommendations: List[str] = []
    if not pass_flag:
        status_code = int(create_resp.get("status_code") or 0)
        if status_code in {401, 403}:
            recommendations.append(
                "招标文件自动创建 smoke 缺少足够权限，请使用本机可信请求或 admin 权限。"
            )
        elif status_code >= 400:
            recommendations.append(
                "招标文件自动创建主链 smoke 失败，说明 create_from_tender 链路未通过自动巡检。"
            )
        elif not elapsed_ok:
            recommendations.append("招标文件自动创建接口耗时过长，已超过 smoke 阈值。")
        elif not inferred_ok:
            recommendations.append("招标文件自动创建未正确识别项目名称，项目名推断链路存在异常。")
        else:
            recommendations.append(
                "招标文件自动创建主链 smoke 失败，请检查 create_from_tender、项目回显和项目删除链路。"
            )
    else:
        recommendations.append("招标文件自动创建主链 smoke 正常。")

    return {
        "name": "tender_project_flow",
        "status": _status(pass_flag),
        "duration_ms": int((time.monotonic() - started) * 1000),
        "checks": checks,
        "actions": actions,
        "metrics": {
            "created_ok": int(created_ok),
            "inferred_ok": int(inferred_ok),
            "elapsed_ok": int(elapsed_ok),
            "listed_after_create": int(listed_after_create),
            "delete_ok": int(delete_ok),
            "removed_after_delete": int(removed_after_delete),
            "elapsed_ms": int(create_resp.get("elapsed_ms") or 0),
        },
        "recommendations": recommendations,
    }


def _run_upload_flow_agent(
    *,
    base_url: str,
    api_key: Optional[str],
    timeout: float,
    requester: Callable[..., Dict[str, Any]],
) -> Dict[str, Any]:
    started = time.monotonic()
    checks: Dict[str, Any] = {}
    actions: Dict[str, Any] = {
        "create": {},
        "upload_material": {},
        "upload_shigong": {},
        "delete": {},
    }
    smoke_name = f"OPS上传项目_{int(time.time() * 1000)}"

    create_resp = requester(
        method="POST",
        url=f"{base_url}/api/v1/projects",
        api_key=api_key,
        timeout=max(10.0, timeout),
        payload={"name": smoke_name},
    )
    checks["create"] = create_resp
    project_id = str(((create_resp.get("json") or {}).get("id")) or "").strip()
    created_ok = bool(project_id) and int(create_resp.get("status_code") or 0) == 200
    actions["create"] = {
        "project_id": project_id,
        "project_name": smoke_name,
        "ok": created_ok,
        "status_code": int(create_resp.get("status_code") or 0),
    }
    material_upload_ok = False
    material_listed = False
    shigong_upload_ok = False
    submission_listed = False
    delete_ok = False
    removed_after_delete = False
    if created_ok:
        material_resp = requester(
            method="POST",
            url=f"{base_url}/api/v1/projects/{project_id}/materials",
            api_key=api_key,
            timeout=max(10.0, timeout),
            form_fields={"material_type": "tender_qa"},
            files=[
                {
                    "field": "file",
                    "filename": "ops_material.txt",
                    "content_type": "text/plain",
                    "content": f"项目名称：{smoke_name}\n招标范围：测试资料上传\n",
                }
            ],
        )
        checks["upload_material"] = material_resp
        material_upload_ok = int(material_resp.get("status_code") or 0) == 200
        actions["upload_material"] = {
            "ok": material_upload_ok,
            "status_code": int(material_resp.get("status_code") or 0),
        }
        materials_list_resp = requester(
            method="GET",
            url=f"{base_url}/api/v1/projects/{project_id}/materials",
            api_key=api_key,
            timeout=timeout,
        )
        checks["materials_after_upload"] = materials_list_resp
        material_rows = materials_list_resp.get("json")
        if isinstance(material_rows, list):
            material_listed = any(
                str(row.get("filename") or "").strip() == "ops_material.txt"
                for row in material_rows
                if isinstance(row, dict)
            )

        shigong_resp = requester(
            method="POST",
            url=f"{base_url}/api/v1/projects/{project_id}/shigong",
            api_key=api_key,
            timeout=max(10.0, timeout),
            files=[
                {
                    "field": "file",
                    "filename": "ops_shigong.txt",
                    "content_type": "text/plain",
                    "content": "施工组织设计\n一、工程概况\n二、施工部署\n",
                }
            ],
        )
        checks["upload_shigong"] = shigong_resp
        shigong_upload_ok = int(shigong_resp.get("status_code") or 0) == 200
        actions["upload_shigong"] = {
            "ok": shigong_upload_ok,
            "status_code": int(shigong_resp.get("status_code") or 0),
        }
        submissions_resp = requester(
            method="GET",
            url=f"{base_url}/api/v1/projects/{project_id}/submissions",
            api_key=api_key,
            timeout=timeout,
        )
        checks["submissions_after_upload"] = submissions_resp
        submission_rows = submissions_resp.get("json")
        if isinstance(submission_rows, list):
            submission_listed = any(
                str(row.get("filename") or "").strip() == "ops_shigong.txt"
                for row in submission_rows
                if isinstance(row, dict)
            )

        delete_resp = requester(
            method="DELETE",
            url=f"{base_url}/api/v1/projects/{project_id}",
            api_key=api_key,
            timeout=max(10.0, timeout),
        )
        checks["delete"] = delete_resp
        delete_ok = int(delete_resp.get("status_code") or 0) in {200, 204}
        actions["delete"] = {
            "project_id": project_id,
            "ok": delete_ok,
            "status_code": int(delete_resp.get("status_code") or 0),
        }
        if delete_ok:
            projects_after_delete = requester(
                method="GET",
                url=f"{base_url}/api/v1/projects",
                api_key=api_key,
                timeout=timeout,
            )
            checks["projects_after_delete"] = projects_after_delete
            projects_rows = _normalize_projects(projects_after_delete.get("json"))
            removed_after_delete = not any(
                str(row.get("id") or "").strip() == project_id for row in projects_rows
            )

    pass_flag = (
        created_ok
        and material_upload_ok
        and material_listed
        and shigong_upload_ok
        and submission_listed
        and delete_ok
        and removed_after_delete
    )
    recommendations: List[str] = []
    if not pass_flag:
        if not created_ok:
            recommendations.append("上传链 smoke 失败：项目创建失败。")
        elif not material_upload_ok:
            recommendations.append("上传链 smoke 失败：项目资料上传接口异常。")
        elif not material_listed:
            recommendations.append("上传链 smoke 失败：资料上传后未在列表回显。")
        elif not shigong_upload_ok:
            recommendations.append("上传链 smoke 失败：施组上传接口异常。")
        elif not submission_listed:
            recommendations.append("上传链 smoke 失败：施组上传后未在列表回显。")
        elif not removed_after_delete:
            recommendations.append("上传链 smoke 失败：清理测试项目后仍残留。")
        else:
            recommendations.append("上传链 smoke 失败：请检查上传区前端与上传接口契约。")
    else:
        recommendations.append("资料上传/施组上传/评分主链 smoke 正常。")

    return {
        "name": "upload_flow",
        "status": _status(pass_flag),
        "duration_ms": int((time.monotonic() - started) * 1000),
        "checks": checks,
        "actions": actions,
        "metrics": {
            "created_ok": int(created_ok),
            "material_upload_ok": int(material_upload_ok),
            "material_listed": int(material_listed),
            "shigong_upload_ok": int(shigong_upload_ok),
            "submission_listed": int(submission_listed),
            "delete_ok": int(delete_ok),
            "removed_after_delete": int(removed_after_delete),
        },
        "recommendations": recommendations,
    }


def _mece_dimension_status(payload: Dict[str, Any], key: str) -> str:
    dimensions = payload.get("dimensions")
    if not isinstance(dimensions, list):
        return ""
    for row in dimensions:
        if not isinstance(row, dict):
            continue
        if str(row.get("key") or "").strip() != key:
            continue
        return str(row.get("status") or "").strip().lower()
    return ""


def _scoring_quality_level_from_mece_audit(payload: Dict[str, Any]) -> str:
    """
    scoring_quality 只关注评分主链本身：
    - input_chain
    - scoring_validity

    自我进化与运行稳定性分别由 evolution / sre_watchdog / data_hygiene 负责，
    不应反向把评分质量误判成 warn/fail。
    """
    statuses = [
        _mece_dimension_status(payload, "input_chain"),
        _mece_dimension_status(payload, "scoring_validity"),
    ]
    focused = [item for item in statuses if item]
    if not focused:
        return str((payload.get("overall") or {}).get("level") or "").strip().lower()
    if any(item == "fail" for item in focused):
        return "critical"
    if any(item == "warn" for item in focused):
        return "watch"
    return "good"


def _run_scoring_quality_agent(
    *,
    base_url: str,
    api_key: Optional[str],
    timeout: float,
    requester: Callable[..., Dict[str, Any]],
) -> Dict[str, Any]:
    started = time.monotonic()
    projects_resp = requester(
        method="GET",
        url=f"{base_url}/api/v1/projects",
        api_key=api_key,
        timeout=timeout,
    )
    if int(projects_resp.get("status_code") or 0) != 200:
        return {
            "name": "scoring_quality",
            "status": "fail",
            "duration_ms": int((time.monotonic() - started) * 1000),
            "checks": {"projects": projects_resp},
            "metrics": {},
            "recommendations": ["无法读取项目列表，评分质量巡检中断。"],
        }

    projects = _normalize_projects(projects_resp.get("json"))
    monitored_projects = _select_projects_for_ops_audit(projects)
    if not monitored_projects:
        return {
            "name": "scoring_quality",
            "status": "pass",
            "duration_ms": int((time.monotonic() - started) * 1000),
            "checks": {"projects": projects_resp},
            "metrics": {"project_count": len(projects), "monitored_project_count": 0},
            "recommendations": ["当前无需要纳入评分质量巡检的真实项目。"],
        }

    audits: List[Dict[str, Any]] = []
    critical = 0
    preparation_critical = 0
    watch = 0
    good = 0
    ignored_non_scoring_issue_count = 0
    for project in monitored_projects:
        pid = str(project.get("id") or "").strip()
        if not pid:
            continue
        project_status = _normalize_project_status(project.get("status"))
        resp = requester(
            method="GET",
            url=f"{base_url}/api/v1/projects/{pid}/mece_audit",
            api_key=api_key,
            timeout=timeout,
        )
        audits.append({"project_id": pid, "response": resp})
        if int(resp.get("status_code") or 0) != 200:
            critical += 1
            continue
        payload = resp.get("json") or {}
        level = _scoring_quality_level_from_mece_audit(payload)
        overall_level = str((payload.get("overall") or {}).get("level") or "").lower()
        summary = payload.get("summary") or {}
        submission_scored = _to_int(summary.get("submission_scored"))
        # 处于准备阶段且尚未完成评分的项目，不应把运维状态打成 fail。
        in_preparation = project_status in OPS_AUDIT_PREPARATION_STATUSES and submission_scored == 0
        if overall_level in {"watch", "critical"} and level == "good":
            ignored_non_scoring_issue_count += 1
        if level == "critical":
            if in_preparation:
                preparation_critical += 1
            else:
                critical += 1
        elif level == "watch":
            watch += 1
        else:
            good += 1

    pass_flag = critical == 0 and watch == 0
    warn_flag = critical == 0 and watch > 0
    recommendations: List[str] = []
    if critical > 0:
        recommendations.append(f"存在 {critical} 个项目处于 critical，请优先补齐资料与评分门禁。")
    elif watch > 0:
        recommendations.append(
            f"存在 {watch} 个项目处于 watch，建议优先复核评分区分度、资料门禁和证据链。"
        )
    elif preparation_critical > 0:
        recommendations.append(
            f"有 {preparation_critical} 个项目处于准备阶段（未上传施组/未评分），不计入故障。"
        )
    elif ignored_non_scoring_issue_count > 0:
        recommendations.append(
            f"有 {ignored_non_scoring_issue_count} 个项目仅存在进化/运行提示，不计入评分质量故障。"
        )
    else:
        recommendations.append("所有项目评分质量状态良好。")

    return {
        "name": "scoring_quality",
        "status": _status(pass_flag, warn_flag=warn_flag),
        "duration_ms": int((time.monotonic() - started) * 1000),
        "checks": {"projects": projects_resp, "audits": audits[:20]},
        "metrics": {
            "project_count": len(projects),
            "monitored_project_count": len(monitored_projects),
            "good_count": good,
            "watch_count": watch,
            "critical_count": critical,
            "preparation_critical_count": preparation_critical,
            "ignored_non_scoring_issue_count": ignored_non_scoring_issue_count,
        },
        "recommendations": recommendations,
    }


def _run_evolution_agent(
    *,
    base_url: str,
    api_key: Optional[str],
    timeout: float,
    auto_evolve: bool,
    min_samples: int,
    requester: Callable[..., Dict[str, Any]],
) -> Dict[str, Any]:
    started = time.monotonic()
    projects_resp = requester(
        method="GET",
        url=f"{base_url}/api/v1/projects",
        api_key=api_key,
        timeout=timeout,
    )
    if int(projects_resp.get("status_code") or 0) != 200:
        return {
            "name": "evolution",
            "status": "fail",
            "duration_ms": int((time.monotonic() - started) * 1000),
            "checks": {"projects": projects_resp},
            "metrics": {},
            "recommendations": ["无法读取项目列表，进化巡检中断。"],
        }

    projects = _normalize_projects(projects_resp.get("json"))
    monitored_projects = _select_projects_for_ops_audit(projects)
    checks: List[Dict[str, Any]] = []
    evolve_actions: List[Dict[str, Any]] = []
    mature_projects = 0
    insufficient_projects = 0
    preparation_insufficient = 0
    started_but_insufficient = 0
    pending_evolve: List[str] = []
    failed_count = 0
    max_required_min_samples = max(1, int(min_samples))
    if not monitored_projects:
        return {
            "name": "evolution",
            "status": "pass",
            "duration_ms": int((time.monotonic() - started) * 1000),
            "checks": {"projects": projects_resp, "health": []},
            "actions": {"evolve": []},
            "metrics": {
                "project_count": len(projects),
                "monitored_project_count": 0,
                "mature_projects": 0,
                "insufficient_projects": 0,
                "preparation_insufficient_count": 0,
                "started_but_insufficient_count": 0,
                "pending_evolve_before": 0,
                "pending_evolve_after": 0,
                "failed_count": 0,
            },
            "recommendations": ["当前无需要纳入进化巡检的真实项目。"],
        }
    for project in monitored_projects:
        pid = str(project.get("id") or "").strip()
        if not pid:
            continue
        project_status = _normalize_project_status(project.get("status"))
        resp = requester(
            method="GET",
            url=f"{base_url}/api/v1/projects/{pid}/evolution/health",
            api_key=api_key,
            timeout=timeout,
        )
        checks.append({"project_id": pid, "response": resp})
        if int(resp.get("status_code") or 0) != 200:
            failed_count += 1
            continue
        summary = (resp.get("json") or {}).get("summary") or {}
        gt_count = _to_int(summary.get("ground_truth_count"))
        project_min_samples = _health_learning_min_samples(summary, min_samples)
        max_required_min_samples = max(max_required_min_samples, project_min_samples)
        has_mult = bool(summary.get("has_evolved_multipliers"))
        if gt_count >= int(project_min_samples):
            mature_projects += 1
            if not has_mult:
                pending_evolve.append(pid)
        else:
            insufficient_projects += 1
            if project_status in OPS_AUDIT_PREPARATION_STATUSES and gt_count <= 0:
                preparation_insufficient += 1
            else:
                started_but_insufficient += 1

    if auto_evolve and pending_evolve:
        for pid in pending_evolve:
            evolve_resp = requester(
                method="POST",
                url=f"{base_url}/api/v1/projects/{pid}/evolve",
                api_key=api_key,
                timeout=max(30.0, timeout),
            )
            ok = int(evolve_resp.get("status_code") or 0) == 200
            evolve_actions.append({"project_id": pid, "ok": ok, "response": evolve_resp})
            if not ok:
                failed_count += 1

    remaining_pending = 0
    if pending_evolve:
        for pid in pending_evolve:
            verify = requester(
                method="GET",
                url=f"{base_url}/api/v1/projects/{pid}/evolution/health",
                api_key=api_key,
                timeout=timeout,
            )
            summary = (verify.get("json") or {}).get("summary") or {}
            has_mult = bool(summary.get("has_evolved_multipliers"))
            if not has_mult:
                remaining_pending += 1

    pass_flag = (
        failed_count == 0
        and remaining_pending == 0
        and (mature_projects > 0 or started_but_insufficient == 0)
    )
    warn_flag = failed_count == 0 and (
        (remaining_pending == 0 and not pass_flag and started_but_insufficient > 0)
        or (remaining_pending > 0 and not auto_evolve)
    )
    recommendations: List[str] = []
    if failed_count > 0:
        recommendations.append(
            f"进化链路存在 {failed_count} 处失败，建议检查真实评分样本与API日志。"
        )
    elif remaining_pending > 0:
        if auto_evolve:
            recommendations.append(f"仍有 {remaining_pending} 个项目未产出进化权重，请人工复核。")
        else:
            recommendations.append(
                f"仍有 {remaining_pending} 个项目待学习校准 agent 产出进化权重。"
            )
    elif started_but_insufficient > 0:
        recommendations.append(
            "当前项目真实评分样本不足，建议每项目至少录入 "
            f"{max_required_min_samples} 条后再观察进化效果。"
        )
    elif preparation_insufficient > 0:
        recommendations.append("当前真实项目仍处于准备阶段，尚未进入可学习进化区间。")
    else:
        recommendations.append("进化链路状态正常。")

    return {
        "name": "evolution",
        "status": _status(pass_flag, warn_flag=warn_flag),
        "duration_ms": int((time.monotonic() - started) * 1000),
        "checks": {"projects": projects_resp, "health": checks[:20]},
        "actions": {"evolve": evolve_actions[:20]},
        "metrics": {
            "project_count": len(projects),
            "monitored_project_count": len(monitored_projects),
            "mature_projects": mature_projects,
            "insufficient_projects": insufficient_projects,
            "preparation_insufficient_count": preparation_insufficient,
            "started_but_insufficient_count": started_but_insufficient,
            "pending_evolve_before": len(pending_evolve),
            "pending_evolve_after": remaining_pending,
            "failed_count": failed_count,
        },
        "recommendations": recommendations,
    }


def _run_learning_calibration_agent(
    *,
    base_url: str,
    api_key: Optional[str],
    timeout: float,
    auto_evolve: bool,
    min_samples: int,
    requester: Callable[..., Dict[str, Any]],
) -> Dict[str, Any]:
    started = time.monotonic()
    projects_resp = requester(
        method="GET",
        url=f"{base_url}/api/v1/projects",
        api_key=api_key,
        timeout=timeout,
    )
    if int(projects_resp.get("status_code") or 0) != 200:
        return {
            "name": "learning_calibration",
            "status": "fail",
            "duration_ms": int((time.monotonic() - started) * 1000),
            "checks": {"projects": projects_resp},
            "actions": {"evolve": [], "reflection_auto_run": []},
            "metrics": {},
            "recommendations": ["无法读取项目列表，学习校准巡检中断。"],
        }

    projects = _normalize_projects(projects_resp.get("json"))
    monitored_projects = _select_projects_for_ops_audit(projects)
    if not monitored_projects:
        return {
            "name": "learning_calibration",
            "status": "pass",
            "duration_ms": int((time.monotonic() - started) * 1000),
            "checks": {"projects": projects_resp, "projects_health": []},
            "actions": {"evolve": [], "reflection_auto_run": []},
            "metrics": {
                "project_count": len(projects),
                "monitored_project_count": 0,
                "mature_projects": 0,
                "insufficient_projects": 0,
                "preparation_insufficient_count": 0,
                "started_but_insufficient_count": 0,
                "reflection_ready_projects": 0,
                "reflection_not_ready_count": 0,
                "pending_evolve_before": 0,
                "pending_evolve_after": 0,
                "pending_calibration_before": 0,
                "pending_calibration_after": 0,
                "evolve_attempted_count": 0,
                "evolve_success_count": 0,
                "reflection_attempted_count": 0,
                "reflection_success_count": 0,
                "manual_confirmation_required_count": 0,
                "few_shot_pending_review_count": 0,
                "few_shot_pending_project_count": 0,
                "drift_alert_before_count": 0,
                "drift_alert_after_count": 0,
                "evolve_cooldown_skipped_count": 0,
                "reflection_cooldown_skipped_count": 0,
                "calibrator_deployed_count": 0,
                "bootstrap_active_count": 0,
                "bootstrap_monitoring_count": 0,
                "bootstrap_review_failed_count": 0,
                "patch_deployed_count": 0,
                "patch_rollback_count": 0,
                "post_verify_failed_count": 0,
                "failed_count": 0,
            },
            "recommendations": ["当前无需要纳入学习校准巡检的真实项目。"],
        }

    checks: List[Dict[str, Any]] = []
    evolve_actions: List[Dict[str, Any]] = []
    reflection_actions: List[Dict[str, Any]] = []
    mature_projects = 0
    insufficient_projects = 0
    preparation_insufficient = 0
    started_but_insufficient = 0
    reflection_ready_projects = 0
    reflection_not_ready_count = 0
    pending_evolve_before = 0
    pending_evolve_after = 0
    pending_calibration_before = 0
    pending_calibration_after = 0
    evolve_attempted_count = 0
    evolve_success_count = 0
    reflection_attempted_count = 0
    reflection_success_count = 0
    manual_confirmation_required_count = 0
    few_shot_pending_review_count = 0
    few_shot_pending_project_count = 0
    drift_alert_before_count = 0
    drift_alert_after_count = 0
    evolve_cooldown_skipped_count = 0
    reflection_cooldown_skipped_count = 0
    calibrator_deployed_count = 0
    bootstrap_active_count = 0
    bootstrap_monitoring_count = 0
    bootstrap_review_failed_count = 0
    patch_deployed_count = 0
    patch_rollback_count = 0
    post_verify_failed_count = 0
    failed_count = 0

    for project in monitored_projects:
        pid = str(project.get("id") or "").strip()
        if not pid:
            continue
        project_status = _normalize_project_status(project.get("status"))
        project_manual_confirmation_required = False
        project_checks: Dict[str, Any] = {"project_id": pid}
        checks.append(project_checks)

        health_resp = requester(
            method="GET",
            url=f"{base_url}/api/v1/projects/{pid}/evolution/health",
            api_key=api_key,
            timeout=timeout,
        )
        project_checks["health"] = health_resp
        if int(health_resp.get("status_code") or 0) != 200:
            failed_count += 1
            continue

        health_payload = _json_object(health_resp.get("json"))
        health_summary = _json_object(health_payload.get("summary"))
        health_drift = _json_object(health_payload.get("drift"))
        project_min_samples = _health_learning_min_samples(health_summary, min_samples)
        gt_count = _to_int(health_summary.get("ground_truth_count"))
        eligible_learning_count = _to_int(
            health_summary.get("eligible_learning_ground_truth_count") or gt_count
        )
        matched_prediction_count = _to_int(health_summary.get("matched_prediction_count"))
        guardrail_blocked_count = _to_int(health_summary.get("guardrail_blocked_count"))
        has_evolved_multipliers = bool(
            health_summary.get("has_evolved_multipliers")
            or health_summary.get("evolution_weights_usable")
        )
        drift_level = str(health_drift.get("level") or "").strip().lower()
        drift_alert_before = drift_level in OPS_LEARNING_CALIBRATION_DRIFT_ALERT_LEVELS
        if drift_alert_before:
            drift_alert_before_count += 1

        if eligible_learning_count >= int(project_min_samples):
            mature_projects += 1
        else:
            insufficient_projects += 1
            if project_status in OPS_AUDIT_PREPARATION_STATUSES and gt_count <= 0:
                preparation_insufficient += 1
            else:
                started_but_insufficient += 1
            continue

        reflection_ready = matched_prediction_count >= int(project_min_samples)
        if reflection_ready:
            reflection_ready_projects += 1
        else:
            reflection_not_ready_count += 1

        governance_resp = requester(
            method="GET",
            url=f"{base_url}/api/v1/projects/{pid}/feedback/governance",
            api_key=api_key,
            timeout=max(12.0, timeout),
        )
        project_checks["governance"] = governance_resp
        if int(governance_resp.get("status_code") or 0) != 200:
            failed_count += 1
            continue

        governance_payload = _json_object(governance_resp.get("json"))
        governance_summary = _json_object(governance_payload.get("summary"))
        governance_score_preview = _json_object(governance_payload.get("score_preview"))
        current_calibrator_version = str(
            governance_score_preview.get("current_calibrator_version") or ""
        ).strip()
        has_project_calibrator = _has_project_specific_calibrator(current_calibrator_version)
        manual_confirmation_required = (
            bool(governance_summary.get("manual_confirmation_required"))
            or guardrail_blocked_count > 0
        )
        if manual_confirmation_required:
            project_manual_confirmation_required = True
        pending_few_shot_count = _to_int(governance_summary.get("few_shot_pending_review_count"))
        few_shot_pending_review_count += pending_few_shot_count
        if pending_few_shot_count > 0:
            few_shot_pending_project_count += 1

        needs_evolve = not has_evolved_multipliers
        needs_reflection = reflection_ready and (not has_project_calibrator or drift_alert_before)
        if needs_evolve:
            pending_evolve_before += 1
        if reflection_ready and not has_project_calibrator:
            pending_calibration_before += 1

        if auto_evolve and needs_evolve and not manual_confirmation_required:
            evolve_due = _retry_due(health_summary.get("last_evolution_updated_at"))
            if evolve_due:
                evolve_attempted_count += 1
                evolve_resp = requester(
                    method="POST",
                    url=f"{base_url}/api/v1/projects/{pid}/evolve",
                    api_key=api_key,
                    timeout=max(30.0, timeout),
                )
                evolve_ok = int(evolve_resp.get("status_code") or 0) == 200
                if evolve_ok:
                    evolve_success_count += 1
                elif int(evolve_resp.get("status_code") or 0) == 409:
                    project_manual_confirmation_required = True
                else:
                    failed_count += 1
                evolve_actions.append(
                    {
                        "project_id": pid,
                        "attempted": True,
                        "ok": evolve_ok,
                        "response": evolve_resp,
                    }
                )
            else:
                evolve_cooldown_skipped_count += 1
                evolve_actions.append(
                    {
                        "project_id": pid,
                        "attempted": False,
                        "ok": False,
                        "reason": "cooldown",
                    }
                )

        if auto_evolve and needs_reflection and not manual_confirmation_required:
            reflection_due = _retry_due(
                _artifact_latest_created_at(governance_payload, "calibration_models")
            )
            if reflection_due:
                reflection_attempted_count += 1
                reflection_resp = requester(
                    method="POST",
                    url=f"{base_url}/api/v1/projects/{pid}/reflection/auto_run",
                    api_key=api_key,
                    timeout=max(45.0, timeout),
                )
                reflection_payload = _json_object(reflection_resp.get("json"))
                reflection_ok = int(reflection_resp.get("status_code") or 0) == 200
                if reflection_ok:
                    reflection_success_count += 1
                    if bool(reflection_payload.get("calibrator_deployed")):
                        calibrator_deployed_count += 1
                    if bool(reflection_payload.get("patch_deployed")):
                        patch_deployed_count += 1
                    patch_auto_govern = _json_object(reflection_payload.get("patch_auto_govern"))
                    if str(patch_auto_govern.get("action") or "").strip().lower() == "rollback":
                        patch_rollback_count += 1
                elif int(reflection_resp.get("status_code") or 0) == 409:
                    project_manual_confirmation_required = True
                else:
                    failed_count += 1
                reflection_actions.append(
                    {
                        "project_id": pid,
                        "attempted": True,
                        "ok": reflection_ok,
                        "response": reflection_resp,
                    }
                )
            else:
                reflection_cooldown_skipped_count += 1
                reflection_actions.append(
                    {
                        "project_id": pid,
                        "attempted": False,
                        "ok": False,
                        "reason": "cooldown",
                    }
                )

        verify_health = requester(
            method="GET",
            url=f"{base_url}/api/v1/projects/{pid}/evolution/health",
            api_key=api_key,
            timeout=timeout,
        )
        project_checks["verify_health"] = verify_health
        if int(verify_health.get("status_code") or 0) != 200:
            failed_count += 1
            continue
        verify_health_payload = _json_object(verify_health.get("json"))
        verify_health_summary = _json_object(verify_health_payload.get("summary"))
        verify_health_drift = _json_object(verify_health_payload.get("drift"))
        has_evolved_after = bool(
            verify_health_summary.get("has_evolved_multipliers")
            or verify_health_summary.get("evolution_weights_usable")
        )
        if str(verify_health_drift.get("level") or "").strip().lower() in (
            OPS_LEARNING_CALIBRATION_DRIFT_ALERT_LEVELS
        ):
            drift_alert_after_count += 1
        if not has_evolved_after:
            pending_evolve_after += 1

        verify_governance = requester(
            method="GET",
            url=f"{base_url}/api/v1/projects/{pid}/feedback/governance",
            api_key=api_key,
            timeout=max(12.0, timeout),
        )
        project_checks["verify_governance"] = verify_governance
        if int(verify_governance.get("status_code") or 0) != 200:
            failed_count += 1
            continue
        verify_governance_payload = _json_object(verify_governance.get("json"))
        verify_governance_summary = _json_object(verify_governance_payload.get("summary"))
        verify_governance_score_preview = _json_object(
            verify_governance_payload.get("score_preview")
        )
        verify_calibrator_version = str(
            verify_governance_score_preview.get("current_calibrator_version") or ""
        ).strip()
        has_project_calibrator_after = _has_project_specific_calibrator(verify_calibrator_version)
        latest_project_deployment_mode = str(
            verify_governance_summary.get("latest_project_calibrator_deployment_mode") or ""
        ).strip()
        latest_project_auto_review = _json_object(
            verify_governance_summary.get("latest_project_calibrator_auto_review")
        )
        if bool(verify_governance_summary.get("current_calibrator_bootstrap_small_sample")):
            bootstrap_active_count += 1
            if (
                str(
                    verify_governance_summary.get("current_calibrator_deployment_mode") or ""
                ).strip()
                == "bootstrap_auto_deploy"
            ):
                bootstrap_monitoring_count += 1
        if reflection_ready and not has_project_calibrator_after:
            pending_calibration_after += 1
            if (
                latest_project_deployment_mode == "bootstrap_candidate_only"
                and str(latest_project_auto_review.get("action") or "").strip() == "rollback"
            ):
                bootstrap_review_failed_count += 1

        latest_reflection_action = next(
            (
                row
                for row in reversed(reflection_actions)
                if str(row.get("project_id") or "") == pid and bool(row.get("attempted"))
            ),
            None,
        )
        if (
            latest_reflection_action
            and bool(latest_reflection_action.get("ok"))
            and bool(
                _json_object(
                    _json_object(latest_reflection_action.get("response")).get("json")
                ).get("calibrator_deployed")
            )
            and not has_project_calibrator_after
        ):
            post_verify_failed_count += 1

        latest_evolve_action = next(
            (
                row
                for row in reversed(evolve_actions)
                if str(row.get("project_id") or "") == pid and bool(row.get("attempted"))
            ),
            None,
        )
        if latest_evolve_action and bool(latest_evolve_action.get("ok")) and not has_evolved_after:
            post_verify_failed_count += 1

        if bool(verify_governance_summary.get("manual_confirmation_required")):
            project_manual_confirmation_required = True
        if project_manual_confirmation_required:
            manual_confirmation_required_count += 1

    total_failure_count = failed_count + post_verify_failed_count
    pass_flag = (
        total_failure_count == 0
        and pending_evolve_after == 0
        and pending_calibration_after == 0
        and manual_confirmation_required_count == 0
        and reflection_not_ready_count == 0
        and started_but_insufficient == 0
        and evolve_cooldown_skipped_count == 0
        and reflection_cooldown_skipped_count == 0
    )
    warn_flag = total_failure_count == 0 and not pass_flag

    recommendations: List[str] = []
    if total_failure_count > 0:
        recommendations.append(
            f"学习校准链路存在 {total_failure_count} 处执行/复核失败，建议检查 reflection 日志与项目治理产物。"
        )
    if manual_confirmation_required_count > 0:
        recommendations.append(
            f"有 {manual_confirmation_required_count} 个项目存在极端偏差样本，需人工确认后才能继续自动学习。"
        )
    if pending_evolve_after > 0:
        recommendations.append(
            f"仍有 {pending_evolve_after} 个项目未形成可用进化权重，当前评分尚未完全进入学习态。"
        )
    if pending_calibration_after > 0:
        recommendations.append(
            f"仍有 {pending_calibration_after} 个项目未形成项目级校准器，当前可能仍在使用 prior 兜底逼近。"
        )
    if bootstrap_monitoring_count > 0:
        recommendations.append(
            f"有 {bootstrap_monitoring_count} 个项目当前使用小样本 bootstrap 校准，已自动部署但仍需继续补录真实评分样本。"
        )
    if bootstrap_review_failed_count > 0:
        recommendations.append(
            f"有 {bootstrap_review_failed_count} 个项目的小样本 bootstrap 校准在只读偏差复核中变差，系统已自动阻止部署。"
        )
    if reflection_not_ready_count > 0:
        recommendations.append(
            f"有 {reflection_not_ready_count} 个项目真实样本已达学习门槛，但可关联预测样本不足，建议优先用步骤4施组下拉录入真实评标。"
        )
    if few_shot_pending_project_count > 0:
        recommendations.append(
            f"有 {few_shot_pending_project_count} 个项目存在 few-shot 待审核样本，建议人工确认后再观察编制指导收敛。"
        )
    if drift_alert_after_count > 0:
        recommendations.append(
            f"有 {drift_alert_after_count} 个项目误差仍处于 watch/medium/high，建议继续补录最新真实评分。"
        )
    if evolve_cooldown_skipped_count > 0 or reflection_cooldown_skipped_count > 0:
        recommendations.append(
            "部分项目距上次自动学习/校准未满 60 分钟，本轮已跳过重复训练以避免版本噪音。"
        )
    if not recommendations:
        recommendations.append("学习校准链路状态正常。")

    return {
        "name": "learning_calibration",
        "status": _status(pass_flag, warn_flag=warn_flag),
        "duration_ms": int((time.monotonic() - started) * 1000),
        "checks": {"projects": projects_resp, "projects_health": checks[:20]},
        "actions": {
            "evolve": evolve_actions[:20],
            "reflection_auto_run": reflection_actions[:20],
        },
        "metrics": {
            "project_count": len(projects),
            "monitored_project_count": len(monitored_projects),
            "mature_projects": mature_projects,
            "insufficient_projects": insufficient_projects,
            "preparation_insufficient_count": preparation_insufficient,
            "started_but_insufficient_count": started_but_insufficient,
            "reflection_ready_projects": reflection_ready_projects,
            "reflection_not_ready_count": reflection_not_ready_count,
            "pending_evolve_before": pending_evolve_before,
            "pending_evolve_after": pending_evolve_after,
            "pending_calibration_before": pending_calibration_before,
            "pending_calibration_after": pending_calibration_after,
            "evolve_attempted_count": evolve_attempted_count,
            "evolve_success_count": evolve_success_count,
            "reflection_attempted_count": reflection_attempted_count,
            "reflection_success_count": reflection_success_count,
            "manual_confirmation_required_count": manual_confirmation_required_count,
            "few_shot_pending_review_count": few_shot_pending_review_count,
            "few_shot_pending_project_count": few_shot_pending_project_count,
            "drift_alert_before_count": drift_alert_before_count,
            "drift_alert_after_count": drift_alert_after_count,
            "evolve_cooldown_skipped_count": evolve_cooldown_skipped_count,
            "reflection_cooldown_skipped_count": reflection_cooldown_skipped_count,
            "calibrator_deployed_count": calibrator_deployed_count,
            "bootstrap_active_count": bootstrap_active_count,
            "bootstrap_monitoring_count": bootstrap_monitoring_count,
            "bootstrap_review_failed_count": bootstrap_review_failed_count,
            "patch_deployed_count": patch_deployed_count,
            "patch_rollback_count": patch_rollback_count,
            "post_verify_failed_count": post_verify_failed_count,
            "failed_count": failed_count,
        },
        "recommendations": recommendations[:20],
    }


def run_ops_agents_cycle(
    *,
    base_url: str = "http://127.0.0.1:8000",
    api_key: Optional[str] = None,
    auto_repair: bool = True,
    auto_evolve: bool = True,
    min_evolve_samples: int = 1,
    restart_cmd: Optional[List[str]] = None,
    timeout: float = 8.0,
    max_workers: int = 3,
) -> Dict[str, Any]:
    """
    运行一轮“多智能体运维闭环”。
    - sre_watchdog
    - data_hygiene
    - runtime_repair
    - project_flow
    - tender_project_flow
    - upload_flow
    - scoring_quality
    - evolution
    - learning_calibration
    """
    cycle_started = time.monotonic()
    restart_cmd = restart_cmd or ["./scripts/restart_server.sh"]
    requester = _request_json
    smoke_retry_budget = {"used": False}

    sre = _run_sre_watchdog(
        base_url=base_url,
        api_key=api_key,
        timeout=timeout,
        auto_repair=auto_repair,
        restart_cmd=restart_cmd,
        requester=requester,
    )

    agents: Dict[str, Dict[str, Any]] = {"sre_watchdog": sre}
    if sre.get("status") == "fail":
        for name in OPS_AGENT_NAMES[1:]:
            agents[name] = _placeholder_agent(name, "SRE未恢复服务，跳过本轮执行。")
    else:
        agents["data_hygiene"] = _run_data_hygiene_agent(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            auto_repair=auto_repair,
            requester=requester,
        )
        agents["runtime_repair"] = _run_runtime_repair_agent(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            auto_repair=auto_repair,
            restart_cmd=restart_cmd,
            requester=requester,
        )
        for name, runner, kwargs in (
            (
                "project_flow",
                _run_project_flow_agent,
                {
                    "base_url": base_url,
                    "api_key": api_key,
                    "timeout": timeout,
                    "requester": requester,
                },
            ),
            (
                "tender_project_flow",
                _run_tender_project_flow_agent,
                {
                    "base_url": base_url,
                    "api_key": api_key,
                    "timeout": timeout,
                    "requester": requester,
                },
            ),
            (
                "upload_flow",
                _run_upload_flow_agent,
                {
                    "base_url": base_url,
                    "api_key": api_key,
                    "timeout": timeout,
                    "requester": requester,
                },
            ),
        ):
            agents[name] = _run_smoke_agent_with_runtime_retry(
                name=name,
                runner=runner,
                kwargs=kwargs,
                auto_repair=auto_repair,
                restart_cmd=restart_cmd,
                retry_budget=smoke_retry_budget,
                retry_state_path=OPS_SMOKE_RUNTIME_RETRY_STATE_PATH,
            )

        agents["learning_calibration"] = _run_learning_calibration_agent(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            auto_evolve=auto_evolve,
            min_samples=min_evolve_samples,
            requester=requester,
        )

        futures = {}
        with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as executor:
            futures[
                executor.submit(
                    _run_scoring_quality_agent,
                    base_url=base_url,
                    api_key=api_key,
                    timeout=timeout,
                    requester=requester,
                )
            ] = "scoring_quality"
            futures[
                executor.submit(
                    _run_evolution_agent,
                    base_url=base_url,
                    api_key=api_key,
                    timeout=timeout,
                    auto_evolve=False,
                    min_samples=min_evolve_samples,
                    requester=requester,
                )
            ] = "evolution"

            for future in as_completed(futures):
                name = futures[future]
                try:
                    agents[name] = future.result()
                except Exception as exc:  # noqa: BLE001
                    agents[name] = _placeholder_agent(
                        name, f"agent_exception: {type(exc).__name__}: {exc}"
                    )

    missing_agent_names = _ensure_agent_coverage(
        agents, reason="agent coverage gap detected; this watchdog cycle is incomplete."
    )
    if missing_agent_names:
        coverage_msg = (
            "ops_agents 覆盖缺口：缺少 "
            + ", ".join(missing_agent_names)
            + "，本轮巡检已按 fail 处理。"
        )
        for name in missing_agent_names:
            agents[name]["recommendations"] = [coverage_msg]

    ordered_agents = {name: agents[name] for name in OPS_AGENT_NAMES}
    statuses = [str(row.get("status") or "fail") for row in ordered_agents.values()]
    fail_count = sum(1 for s in statuses if s == "fail")
    warn_count = sum(1 for s in statuses if s == "warn")
    pass_count = sum(1 for s in statuses if s == "pass")
    overall_status = "pass"
    if fail_count > 0:
        overall_status = "fail"
    elif warn_count > 0:
        overall_status = "warn"

    recommendations: List[str] = []
    for row in ordered_agents.values():
        for text in row.get("recommendations") or []:
            msg = str(text).strip()
            if msg and msg not in recommendations:
                recommendations.append(msg)

    return {
        "generated_at": _now_iso(),
        "base_url": base_url,
        "agent_count": len(OPS_AGENT_NAMES),
        "expected_agent_names": list(OPS_AGENT_NAMES),
        "missing_agent_names": missing_agent_names,
        "settings": {
            "auto_repair": bool(auto_repair),
            "auto_evolve": bool(auto_evolve),
            "min_evolve_samples": int(min_evolve_samples),
            "timeout_seconds": float(timeout),
            "max_workers": max(1, int(max_workers)),
        },
        "overall": {
            "status": overall_status,
            "pass_count": pass_count,
            "warn_count": warn_count,
            "fail_count": fail_count,
            "duration_ms": int((time.monotonic() - cycle_started) * 1000),
        },
        "agents": ordered_agents,
        "recommendations": recommendations[:20],
    }
