from __future__ import annotations

import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from urllib import error, request


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _normalize_projects(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("projects", "items", "data"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return [x for x in rows if isinstance(x, dict)]
    return []


def _request_json(
    *,
    method: str,
    url: str,
    api_key: Optional[str] = None,
    timeout: float = 8.0,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    headers: Dict[str, str] = {"Accept": "application/json"}
    body: Optional[bytes] = None
    if api_key:
        headers["X-API-Key"] = api_key
    if payload is not None:
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
        restart_result["attempted"] = True
        try:
            proc = subprocess.run(
                restart_cmd,
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
            restart_result["returncode"] = int(proc.returncode)
            restart_result["ok"] = proc.returncode == 0
            if proc.returncode != 0:
                restart_result["error"] = (proc.stderr or proc.stdout or "").strip()[:600]
        except Exception as exc:  # noqa: BLE001
            restart_result["error"] = f"{type(exc).__name__}: {exc}"
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
    if not projects:
        return {
            "name": "scoring_quality",
            "status": "warn",
            "duration_ms": int((time.monotonic() - started) * 1000),
            "checks": {"projects": projects_resp},
            "metrics": {"project_count": 0},
            "recommendations": ["当前无项目可审计。"],
        }

    audits: List[Dict[str, Any]] = []
    critical = 0
    watch = 0
    good = 0
    for project in projects:
        pid = str(project.get("id") or "").strip()
        if not pid:
            continue
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
        level = str(((resp.get("json") or {}).get("overall") or {}).get("level") or "").lower()
        if level == "critical":
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
        recommendations.append(f"存在 {watch} 个项目处于 watch，建议补充样本与进化训练。")
    else:
        recommendations.append("所有项目评分质量状态良好。")

    return {
        "name": "scoring_quality",
        "status": _status(pass_flag, warn_flag=warn_flag),
        "duration_ms": int((time.monotonic() - started) * 1000),
        "checks": {"projects": projects_resp, "audits": audits[:20]},
        "metrics": {
            "project_count": len(projects),
            "good_count": good,
            "watch_count": watch,
            "critical_count": critical,
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
    checks: List[Dict[str, Any]] = []
    evolve_actions: List[Dict[str, Any]] = []
    mature_projects = 0
    insufficient_projects = 0
    pending_evolve: List[str] = []
    failed_count = 0
    for project in projects:
        pid = str(project.get("id") or "").strip()
        if not pid:
            continue
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
        has_mult = bool(summary.get("has_evolved_multipliers"))
        if gt_count >= int(min_samples):
            mature_projects += 1
            if not has_mult:
                pending_evolve.append(pid)
        else:
            insufficient_projects += 1

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

    pass_flag = failed_count == 0 and remaining_pending == 0
    warn_flag = pass_flag and mature_projects == 0 and insufficient_projects > 0
    recommendations: List[str] = []
    if failed_count > 0:
        recommendations.append(
            f"进化链路存在 {failed_count} 处失败，建议检查真实评分样本与API日志。"
        )
    elif remaining_pending > 0:
        recommendations.append(f"仍有 {remaining_pending} 个项目未产出进化权重，请人工复核。")
    elif mature_projects == 0 and insufficient_projects > 0:
        recommendations.append(
            "当前项目真实评分样本不足，建议每项目至少录入 3 条后再观察进化效果。"
        )
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
            "mature_projects": mature_projects,
            "insufficient_projects": insufficient_projects,
            "pending_evolve_before": len(pending_evolve),
            "pending_evolve_after": remaining_pending,
            "failed_count": failed_count,
        },
        "recommendations": recommendations,
    }


def run_ops_agents_cycle(
    *,
    base_url: str = "http://127.0.0.1:8000",
    api_key: Optional[str] = None,
    auto_repair: bool = True,
    auto_evolve: bool = True,
    min_evolve_samples: int = 3,
    restart_cmd: Optional[List[str]] = None,
    timeout: float = 8.0,
    max_workers: int = 3,
) -> Dict[str, Any]:
    """
    运行一轮“多智能体运维闭环”。
    - sre_watchdog
    - data_hygiene
    - scoring_quality
    - evolution
    """
    cycle_started = time.monotonic()
    restart_cmd = restart_cmd or ["./scripts/restart_server.sh"]
    requester = _request_json

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
        agents["data_hygiene"] = {
            "name": "data_hygiene",
            "status": "fail",
            "duration_ms": 0,
            "checks": {},
            "actions": {},
            "metrics": {},
            "recommendations": ["SRE未恢复服务，跳过本轮执行。"],
        }
        agents["scoring_quality"] = {
            "name": "scoring_quality",
            "status": "fail",
            "duration_ms": 0,
            "checks": {},
            "actions": {},
            "metrics": {},
            "recommendations": ["SRE未恢复服务，跳过本轮执行。"],
        }
        agents["evolution"] = {
            "name": "evolution",
            "status": "fail",
            "duration_ms": 0,
            "checks": {},
            "actions": {},
            "metrics": {},
            "recommendations": ["SRE未恢复服务，跳过本轮执行。"],
        }
    else:
        futures = {}
        with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as executor:
            futures[
                executor.submit(
                    _run_data_hygiene_agent,
                    base_url=base_url,
                    api_key=api_key,
                    timeout=timeout,
                    auto_repair=auto_repair,
                    requester=requester,
                )
            ] = "data_hygiene"
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
                    auto_evolve=auto_evolve,
                    min_samples=min_evolve_samples,
                    requester=requester,
                )
            ] = "evolution"

            for future in as_completed(futures):
                name = futures[future]
                try:
                    agents[name] = future.result()
                except Exception as exc:  # noqa: BLE001
                    agents[name] = {
                        "name": name,
                        "status": "fail",
                        "duration_ms": 0,
                        "checks": {},
                        "actions": {},
                        "metrics": {},
                        "recommendations": [f"agent_exception: {type(exc).__name__}: {exc}"],
                    }

    statuses = [str(row.get("status") or "fail") for row in agents.values()]
    fail_count = sum(1 for s in statuses if s == "fail")
    warn_count = sum(1 for s in statuses if s == "warn")
    pass_count = sum(1 for s in statuses if s == "pass")
    overall_status = "pass"
    if fail_count > 0:
        overall_status = "fail"
    elif warn_count > 0:
        overall_status = "warn"

    recommendations: List[str] = []
    for row in agents.values():
        for text in row.get("recommendations") or []:
            msg = str(text).strip()
            if msg and msg not in recommendations:
                recommendations.append(msg)

    return {
        "generated_at": _now_iso(),
        "base_url": base_url,
        "agent_count": 4,
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
        "agents": agents,
        "recommendations": recommendations[:20],
    }
