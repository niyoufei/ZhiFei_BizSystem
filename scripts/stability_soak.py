#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def choose_python(root: Path) -> str:
    candidate = root / ".venv" / "bin" / "python"
    if candidate.exists():
        return str(candidate)
    return "python3"


def resolve_api_key(root: Path) -> str:
    resolver = root / "scripts" / "resolve_api_key.py"
    if not resolver.exists():
        return ""
    env_key = str(os.environ.get("API_KEY") or "").strip()
    if env_key:
        return env_key
    python_bin = choose_python(root)
    proc = subprocess.run(
        [python_bin, str(resolver), "--preferred-role", "ops", "--fallback-role", "admin"],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return ""
    return str(proc.stdout or "").strip()


def http_fetch(
    *,
    url: str,
    api_key: str,
    timeout_seconds: float,
    expect_json: bool,
) -> Dict[str, Any]:
    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key
    request = Request(url, headers=headers)
    started = time.perf_counter()
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            body_bytes = response.read()
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            body_text = body_bytes.decode("utf-8", errors="replace")
            payload: Any = body_text
            if expect_json:
                try:
                    payload = json.loads(body_text or "{}")
                except json.JSONDecodeError:
                    return {
                        "ok": False,
                        "status_code": response.status,
                        "elapsed_ms": elapsed_ms,
                        "error": "invalid_json",
                        "body_preview": body_text[:300],
                    }
            return {
                "ok": True,
                "status_code": response.status,
                "elapsed_ms": elapsed_ms,
                "payload": payload,
                "body_preview": body_text[:300],
            }
    except HTTPError as exc:
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        try:
            body_text = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body_text = ""
        return {
            "ok": False,
            "status_code": exc.code,
            "elapsed_ms": elapsed_ms,
            "error": f"http_{exc.code}",
            "body_preview": body_text[:300],
        }
    except URLError as exc:
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "ok": False,
            "status_code": None,
            "elapsed_ms": elapsed_ms,
            "error": f"url_error:{exc.reason}",
            "body_preview": "",
        }
    except Exception as exc:  # pragma: no cover - runtime-only branch
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "ok": False,
            "status_code": None,
            "elapsed_ms": elapsed_ms,
            "error": str(exc),
            "body_preview": "",
        }


def listener_pids(port: int) -> List[str]:
    proc = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode not in (0, 1):
        return []
    return [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]


def run_doctor(root: Path, *, port: int, api_key: str, strict: bool) -> Dict[str, Any]:
    env = os.environ.copy()
    env["PORT"] = str(port)
    if api_key:
        env["API_KEY"] = api_key
    if strict:
        env["STRICT"] = "1"
    started = time.perf_counter()
    proc = subprocess.run(
        [str(root / "scripts" / "doctor.sh")],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "elapsed_ms": elapsed_ms,
        "stdout_tail": (proc.stdout or "")[-1200:],
        "stderr_tail": (proc.stderr or "")[-1200:],
    }


def sample_cycle(
    *,
    index: int,
    base_url: str,
    port: int,
    api_key: str,
    timeout_seconds: float,
) -> Dict[str, Any]:
    health_url = f"{base_url.rstrip('/')}/health"
    self_check_url = f"{base_url.rstrip('/')}/api/v1/system/self_check"
    self_check_compat_url = f"{base_url.rstrip('/')}/api/system/self_check"
    home_url = f"{base_url.rstrip('/')}/"
    cycle_started_at = utc_now()
    pids = listener_pids(port)
    health = http_fetch(
        url=health_url,
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        expect_json=True,
    )
    self_check = http_fetch(
        url=self_check_url,
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        expect_json=True,
    )
    self_check_url_used = self_check_url
    if not self_check.get("ok"):
        compat = http_fetch(
            url=self_check_compat_url,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            expect_json=True,
        )
        if compat.get("ok"):
            self_check = compat
            self_check_url_used = self_check_compat_url
    home = http_fetch(
        url=home_url,
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        expect_json=False,
    )
    issues: List[str] = []
    health_payload = health.get("payload") if isinstance(health.get("payload"), dict) else {}
    self_payload = self_check.get("payload") if isinstance(self_check.get("payload"), dict) else {}
    if not pids:
        issues.append("no_listener_pid")
    if not health.get("ok"):
        issues.append("health_http_failed")
    elif str(health_payload.get("status") or "").strip().lower() not in {"healthy", "ok"}:
        issues.append("health_status_not_healthy")
    if not self_check.get("ok"):
        issues.append("self_check_http_failed")
    elif not bool(self_payload.get("ok")):
        issues.append("self_check_not_ok")
    if not home.get("ok"):
        issues.append("home_http_failed")
    elif "<html" not in str(home.get("body_preview") or "").lower():
        issues.append("home_not_html")
    return {
        "cycle": index,
        "started_at": cycle_started_at,
        "listener_pids": pids,
        "health": {
            "ok": bool(health.get("ok")),
            "status_code": health.get("status_code"),
            "elapsed_ms": health.get("elapsed_ms"),
            "status": health_payload.get("status"),
            "version": health_payload.get("version"),
            "error": health.get("error"),
        },
        "self_check": {
            "ok": bool(self_check.get("ok")),
            "status_code": self_check.get("status_code"),
            "elapsed_ms": self_check.get("elapsed_ms"),
            "url": self_check_url_used,
            "payload_ok": self_payload.get("ok"),
            "required_ok": self_payload.get("required_ok"),
            "degraded": self_payload.get("degraded"),
            "failed_required_count": self_payload.get("failed_required_count"),
            "failed_optional_count": self_payload.get("failed_optional_count"),
            "error": self_check.get("error"),
        },
        "home": {
            "ok": bool(home.get("ok")),
            "status_code": home.get("status_code"),
            "elapsed_ms": home.get("elapsed_ms"),
            "error": home.get("error"),
        },
        "issues": issues,
        "ok": not issues,
    }


def build_report(
    *,
    base_url: str,
    duration_seconds: int,
    interval_seconds: int,
    timeout_seconds: float,
    doctor_before: Dict[str, Any] | None,
    doctor_after: Dict[str, Any] | None,
    cycles: List[Dict[str, Any]],
) -> Dict[str, Any]:
    all_pids = [tuple(cycle.get("listener_pids") or []) for cycle in cycles]
    pid_changes = 0
    for prev, current in zip(all_pids, all_pids[1:]):
        if prev != current:
            pid_changes += 1
    health_latencies = [
        float(cycle.get("health", {}).get("elapsed_ms") or 0)
        for cycle in cycles
        if cycle.get("health", {}).get("elapsed_ms") is not None
    ]
    self_latencies = [
        float(cycle.get("self_check", {}).get("elapsed_ms") or 0)
        for cycle in cycles
        if cycle.get("self_check", {}).get("elapsed_ms") is not None
    ]
    failed_cycles = [cycle for cycle in cycles if not cycle.get("ok")]
    warnings: List[str] = []
    if pid_changes > 0:
        warnings.append(f"listener pid changed {pid_changes} time(s)")
    if doctor_before and not doctor_before.get("ok"):
        warnings.append("doctor before soak failed")
    if doctor_after and not doctor_after.get("ok"):
        warnings.append("doctor after soak failed")
    status = "PASS"
    if (
        failed_cycles
        or (doctor_before and not doctor_before.get("ok"))
        or (doctor_after and not doctor_after.get("ok"))
    ):
        status = "FAIL"
    elif warnings:
        status = "WARN"
    return {
        "status": status,
        "generated_at": utc_now(),
        "base_url": base_url,
        "settings": {
            "duration_seconds": duration_seconds,
            "interval_seconds": interval_seconds,
            "timeout_seconds": timeout_seconds,
        },
        "doctor_before": doctor_before,
        "doctor_after": doctor_after,
        "summary": {
            "cycle_count": len(cycles),
            "ok_cycle_count": len(cycles) - len(failed_cycles),
            "failed_cycle_count": len(failed_cycles),
            "listener_pid_change_count": pid_changes,
            "first_listener_pids": list(all_pids[0]) if all_pids else [],
            "last_listener_pids": list(all_pids[-1]) if all_pids else [],
            "health_latency_ms_avg": round(statistics.mean(health_latencies), 2)
            if health_latencies
            else None,
            "health_latency_ms_max": round(max(health_latencies), 2) if health_latencies else None,
            "self_check_latency_ms_avg": round(statistics.mean(self_latencies), 2)
            if self_latencies
            else None,
            "self_check_latency_ms_max": round(max(self_latencies), 2) if self_latencies else None,
        },
        "warnings": warnings,
        "failed_cycles": failed_cycles,
        "cycles": cycles,
    }


def to_markdown(report: Dict[str, Any]) -> str:
    settings = report.get("settings") or {}
    summary = report.get("summary") or {}
    lines = [
        "# Stability Soak Report",
        "",
        f"- generated_at: `{report.get('generated_at')}`",
        f"- base_url: `{report.get('base_url')}`",
        f"- status: `{report.get('status')}`",
        f"- duration_seconds: `{settings.get('duration_seconds')}`",
        f"- interval_seconds: `{settings.get('interval_seconds')}`",
        f"- timeout_seconds: `{settings.get('timeout_seconds')}`",
        "",
        "## Summary",
        f"- cycle_count: `{summary.get('cycle_count')}`",
        f"- ok_cycle_count: `{summary.get('ok_cycle_count')}`",
        f"- failed_cycle_count: `{summary.get('failed_cycle_count')}`",
        f"- listener_pid_change_count: `{summary.get('listener_pid_change_count')}`",
        f"- first_listener_pids: `{summary.get('first_listener_pids')}`",
        f"- last_listener_pids: `{summary.get('last_listener_pids')}`",
        f"- health_latency_ms_avg: `{summary.get('health_latency_ms_avg')}`",
        f"- health_latency_ms_max: `{summary.get('health_latency_ms_max')}`",
        f"- self_check_latency_ms_avg: `{summary.get('self_check_latency_ms_avg')}`",
        f"- self_check_latency_ms_max: `{summary.get('self_check_latency_ms_max')}`",
        "",
        "## Doctor",
    ]
    for label in ("doctor_before", "doctor_after"):
        row = report.get(label) or {}
        lines.append(
            f"- {label}: ok=`{row.get('ok')}`, returncode=`{row.get('returncode')}`, elapsed_ms=`{row.get('elapsed_ms')}`"
        )
    lines.append("")
    lines.append("## Warnings")
    warnings = report.get("warnings") or []
    if warnings:
        for item in warnings:
            lines.append(f"- {item}")
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Failed Cycles")
    failed_cycles = report.get("failed_cycles") or []
    if failed_cycles:
        for cycle in failed_cycles:
            lines.append(
                f"- cycle={cycle.get('cycle')} started_at={cycle.get('started_at')} issues={cycle.get('issues')}"
            )
    else:
        lines.append("- none")
    lines.append("")
    lines.append("## Cycle Snapshot")
    for cycle in (report.get("cycles") or [])[:5]:
        lines.append(
            f"- cycle={cycle.get('cycle')} ok={cycle.get('ok')} pids={cycle.get('listener_pids')} "
            f"health={cycle.get('health', {}).get('elapsed_ms')}ms "
            f"self_check={cycle.get('self_check', {}).get('elapsed_ms')}ms "
            f"issues={cycle.get('issues')}"
        )
    lines.append("")
    return "\n".join(lines)


def write_outputs(report: Dict[str, Any], *, output_json: Path, output_md: Path) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    output_md.write_text(to_markdown(report), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Long-running read-only stability soak.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--duration-seconds", type=int, default=600)
    parser.add_argument("--interval-seconds", type=int, default=30)
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument(
        "--output-json",
        default=str(ROOT / "build" / "stability_soak_latest.json"),
    )
    parser.add_argument(
        "--output-md",
        default=str(ROOT / "build" / "stability_soak_latest.md"),
    )
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--skip-doctor-before", action="store_true")
    parser.add_argument("--skip-doctor-after", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    api_key = resolve_api_key(ROOT)
    doctor_before = None
    doctor_after = None
    if not args.skip_doctor_before:
        doctor_before = run_doctor(ROOT, port=args.port, api_key=api_key, strict=True)
        if args.strict and not doctor_before.get("ok"):
            report = build_report(
                base_url=args.base_url,
                duration_seconds=args.duration_seconds,
                interval_seconds=args.interval_seconds,
                timeout_seconds=args.timeout_seconds,
                doctor_before=doctor_before,
                doctor_after=None,
                cycles=[],
            )
            write_outputs(
                report,
                output_json=Path(args.output_json),
                output_md=Path(args.output_md),
            )
            return 1

    deadline = time.time() + max(1, int(args.duration_seconds))
    cycles: List[Dict[str, Any]] = []
    cycle_index = 1
    while True:
        cycles.append(
            sample_cycle(
                index=cycle_index,
                base_url=args.base_url,
                port=args.port,
                api_key=api_key,
                timeout_seconds=args.timeout_seconds,
            )
        )
        cycle_index += 1
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        time.sleep(min(float(args.interval_seconds), max(0.0, remaining)))

    if not args.skip_doctor_after:
        doctor_after = run_doctor(ROOT, port=args.port, api_key=api_key, strict=True)

    report = build_report(
        base_url=args.base_url,
        duration_seconds=args.duration_seconds,
        interval_seconds=args.interval_seconds,
        timeout_seconds=args.timeout_seconds,
        doctor_before=doctor_before,
        doctor_after=doctor_after,
        cycles=cycles,
    )
    write_outputs(
        report,
        output_json=Path(args.output_json),
        output_md=Path(args.output_md),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.strict and report.get("status") != "PASS":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
