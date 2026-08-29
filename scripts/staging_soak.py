from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

SCHEMA = "qingtian-r8-staging-soak-v1"
DEFAULT_DURATION_SECONDS = 72 * 60 * 60
SCORE_TEXT = (
    "施工前完成图纸会审和技术交底，关键工序实行样板引路、旁站检查和验收留痕。"
    "现场设置临时排水、消防器材与安全通道，进度偏差触发资源纠偏并复核闭环。"
)


class SoakBlocked(RuntimeError):
    pass


@dataclass(frozen=True)
class SoakConfig:
    base_url: str
    prometheus_url: str
    api_key_file: Path
    expected_image: str
    container: str
    output_dir: Path
    duration_seconds: int = DEFAULT_DURATION_SECONDS
    health_interval_seconds: int = 30
    metrics_interval_seconds: int = 60
    score_interval_seconds: int = 15 * 60
    canary_interval_seconds: int = 60 * 60
    max_cycles: int | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    candidate = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    candidate.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(candidate, path)


def _load_single_api_key(path: Path) -> str:
    raw = path.read_text(encoding="utf-8")
    keys = [part.strip() for line in raw.splitlines() for part in line.split(",") if part.strip()]
    if len(keys) != 1:
        raise SoakBlocked("api key file must contain exactly one non-empty key")
    return keys[0]


def _request_json(
    method: str,
    url: str,
    *,
    api_key: str | None = None,
    payload: Mapping[str, Any] | None = None,
    timeout: float = 20.0,
    opener: Callable[..., Any] = urlopen,
) -> tuple[int, Any, float]:
    headers = {"Accept": "application/json", "User-Agent": "qingtian-r8-soak/1"}
    data = None
    if api_key is not None:
        headers["X-API-Key"] = api_key
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(url, data=data, headers=headers, method=method)
    started = time.perf_counter()
    try:
        response = opener(request, timeout=timeout)
        with response:
            status = int(response.status)
            body = response.read()
    except HTTPError as exc:
        status = int(exc.code)
        body = exc.read()
    except (OSError, URLError) as exc:
        raise SoakBlocked(f"request failed: {method} {url}: {exc}") from exc
    latency = max(0.0, time.perf_counter() - started)
    if not body:
        parsed: Any = None
    else:
        try:
            parsed = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SoakBlocked(f"non-JSON response: {method} {url}: status={status}") from exc
    return status, parsed, latency


def _score_fingerprint(payload: Mapping[str, Any]) -> str:
    normalized = json.loads(json.dumps(payload, ensure_ascii=False))
    meta = normalized.get("meta")
    if isinstance(meta, dict):
        meta.pop("timestamp", None)
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    rank = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[rank]


def _parse_memory_bytes(value: str) -> int:
    token = value.strip().split("/", 1)[0].strip().replace(" ", "")
    units = {
        "b": 1,
        "kb": 1000,
        "kib": 1024,
        "mb": 1000**2,
        "mib": 1024**2,
        "gb": 1000**3,
        "gib": 1024**3,
    }
    lowered = token.lower()
    for unit in sorted(units, key=len, reverse=True):
        if lowered.endswith(unit):
            return int(float(lowered[: -len(unit)]) * units[unit])
    return int(float(lowered))


def _run_command(args: Sequence[str]) -> str:
    result = subprocess.run(
        list(args),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _docker_state(container: str) -> dict[str, Any]:
    raw = _run_command(["docker", "inspect", "--format", "{{json .}}", container])
    payload = json.loads(raw)
    state = payload.get("State") or {}
    image_id = str(payload.get("Image") or "")
    repo_digests_raw = _run_command(
        ["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", image_id]
    )
    repo_digests = json.loads(repo_digests_raw)
    stats_raw = _run_command(
        ["docker", "stats", "--no-stream", "--format", "{{json .}}", container]
    )
    stats = json.loads(stats_raw)
    wal_raw = _run_command(
        [
            "docker",
            "exec",
            container,
            "python",
            "-c",
            (
                "from pathlib import Path; "
                "p=Path('/var/lib/qingtian/qingtian.sqlite3-wal'); "
                "print(p.stat().st_size if p.exists() else 0)"
            ),
        ]
    )
    return {
        "running": bool(state.get("Running")),
        "oom_killed": bool(state.get("OOMKilled")),
        "restart_count": int(payload.get("RestartCount") or 0),
        "repo_digests": sorted(str(item) for item in repo_digests),
        "memory_bytes": _parse_memory_bytes(str(stats.get("MemUsage") or "0")),
        "wal_bytes": int(wal_raw),
    }


def _require_status(status: int, expected: int, name: str, payload: Any) -> None:
    if status != expected:
        raise SoakBlocked(
            f"{name} status mismatch: expected={expected} actual={status} body={payload}"
        )


def _probe_prometheus(url: str) -> float:
    query = quote('up{job="qingtian"}')
    status, payload, latency = _request_json("GET", f"{url}/api/v1/query?query={query}")
    _require_status(status, 200, "prometheus query", payload)
    results = ((payload or {}).get("data") or {}).get("result") or []
    if not results or any(str(item.get("value", [None, "0"])[1]) != "1" for item in results):
        raise SoakBlocked(f"prometheus qingtian target is not UP: {results}")
    return latency


def _probe_health(base_url: str) -> dict[str, float]:
    status, payload, health_latency = _request_json("GET", f"{base_url}/health")
    _require_status(status, 200, "health", payload)
    if (payload or {}).get("status") != "healthy":
        raise SoakBlocked(f"health payload is not healthy: {payload}")
    status, payload, ready_latency = _request_json("GET", f"{base_url}/ready")
    _require_status(status, 200, "ready", payload)
    if (payload or {}).get("status") != "ready" or not all(
        bool(value) for value in ((payload or {}).get("checks") or {}).values()
    ):
        raise SoakBlocked(f"readiness payload is not ready: {payload}")
    return {"health": health_latency, "ready": ready_latency}


def _probe_auth(base_url: str) -> float:
    status, payload, latency = _request_json("GET", f"{base_url}/api/v1/projects")
    _require_status(status, 401, "unauthorized projects", payload)
    return latency


def _probe_score(
    base_url: str, api_key: str, expected_fingerprint: str | None
) -> tuple[str, float]:
    status, payload, latency = _request_json(
        "POST",
        f"{base_url}/api/v1/score",
        api_key=api_key,
        payload={"text": SCORE_TEXT},
    )
    _require_status(status, 200, "score", payload)
    if not isinstance(payload, dict):
        raise SoakBlocked("score response is not an object")
    fingerprint = _score_fingerprint(payload)
    if expected_fingerprint is not None and fingerprint != expected_fingerprint:
        raise SoakBlocked(
            f"score semantic drift: expected={expected_fingerprint} actual={fingerprint}"
        )
    return fingerprint, latency


def _probe_canary(base_url: str, api_key: str, run_id: str, cycle: int) -> list[float]:
    latencies: list[float] = []
    status, payload, latency = _request_json(
        "POST",
        f"{base_url}/api/v1/projects",
        api_key=api_key,
        payload={"name": f"R8_CANARY_{run_id}_{cycle}"},
    )
    latencies.append(latency)
    _require_status(status, 200, "canary create", payload)
    project_id = str((payload or {}).get("id") or "")
    if not project_id:
        raise SoakBlocked("canary create response has no project id")

    status, payload, latency = _request_json("GET", f"{base_url}/api/v1/projects", api_key=api_key)
    latencies.append(latency)
    _require_status(status, 200, "canary list after create", payload)
    if not any(str(item.get("id")) == project_id for item in payload or []):
        raise SoakBlocked(f"created canary project is absent: {project_id}")

    status, payload, latency = _request_json(
        "DELETE",
        f"{base_url}/api/v1/projects/{quote(project_id, safe='')}",
        api_key=api_key,
    )
    latencies.append(latency)
    _require_status(status, 204, "canary delete", payload)

    status, payload, latency = _request_json("GET", f"{base_url}/api/v1/projects", api_key=api_key)
    latencies.append(latency)
    _require_status(status, 200, "canary list after delete", payload)
    if any(str(item.get("id")) == project_id for item in payload or []):
        raise SoakBlocked(f"deleted canary project remains visible: {project_id}")
    return latencies


def _new_report(config: SoakConfig) -> dict[str, Any]:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return {
        "schema": SCHEMA,
        "run_id": run_id,
        "status": "RUNNING",
        "started_at": _utc_now(),
        "completed_at": None,
        "config": {
            **asdict(config),
            "api_key_file": str(config.api_key_file),
            "output_dir": str(config.output_dir),
        },
        "score_fingerprint": None,
        "counters": {
            "health_cycles": 0,
            "metrics_cycles": 0,
            "score_cycles": 0,
            "canary_cycles": 0,
            "unexpected_5xx": 0,
            "unexpected_429": 0,
        },
        "latency_seconds": {"non_score": [], "score": [], "prometheus": []},
        "runtime_samples": [],
        "failure": None,
        "acceptance": {},
    }


def _evaluate(
    report: Mapping[str, Any], elapsed_seconds: float, config: SoakConfig
) -> dict[str, Any]:
    latencies = report["latency_seconds"]
    runtime_samples = list(report["runtime_samples"])
    memory_samples = [int(item["memory_bytes"]) for item in runtime_samples]
    warm_index = min(len(memory_samples) - 1, max(0, int(3600 / config.health_interval_seconds)))
    warm_memory = memory_samples[warm_index] if memory_samples else 0
    peak_memory = max(memory_samples, default=0)
    memory_growth_ratio = (
        max(0.0, (peak_memory - warm_memory) / warm_memory) if warm_memory > 0 else 0.0
    )
    expected_duration = config.duration_seconds if config.max_cycles is None else 0
    checks = {
        "duration_complete": elapsed_seconds >= expected_duration,
        "health_success_100_percent": int(report["counters"]["health_cycles"]) > 0,
        "metrics_success_100_percent": int(report["counters"]["metrics_cycles"]) > 0,
        "score_success_100_percent": int(report["counters"]["score_cycles"]) > 0,
        "canary_success_100_percent": int(report["counters"]["canary_cycles"]) > 0,
        "unexpected_5xx_zero": int(report["counters"]["unexpected_5xx"]) == 0,
        "unexpected_429_zero": int(report["counters"]["unexpected_429"]) == 0,
        "restart_count_zero": all(int(item["restart_count"]) == 0 for item in runtime_samples),
        "oom_killed_false": all(not bool(item["oom_killed"]) for item in runtime_samples),
        "image_digest_stable": all(bool(item["image_matches"]) for item in runtime_samples),
        "non_score_p95_le_1s": _percentile(latencies["non_score"], 0.95) <= 1.0,
        "score_p95_le_5s": _percentile(latencies["score"], 0.95) <= 5.0,
        "score_p99_le_10s": _percentile(latencies["score"], 0.99) <= 10.0,
        "memory_peak_le_512mib": peak_memory <= 512 * 1024 * 1024,
        "memory_growth_le_25_percent": memory_growth_ratio <= 0.25,
    }
    return {
        "checks": checks,
        "passed": all(checks.values()),
        "elapsed_seconds": round(elapsed_seconds, 6),
        "non_score_p95_seconds": round(_percentile(latencies["non_score"], 0.95), 6),
        "score_p95_seconds": round(_percentile(latencies["score"], 0.95), 6),
        "score_p99_seconds": round(_percentile(latencies["score"], 0.99), 6),
        "warm_memory_bytes": warm_memory,
        "peak_memory_bytes": peak_memory,
        "memory_growth_ratio": round(memory_growth_ratio, 6),
        "wal_peak_bytes": max((int(item["wal_bytes"]) for item in runtime_samples), default=0),
    }


def run_soak(config: SoakConfig) -> dict[str, Any]:
    report_path = config.output_dir / "soak-report.json"
    if report_path.exists():
        raise SoakBlocked(f"refusing to overwrite existing report: {report_path}")
    api_key = _load_single_api_key(config.api_key_file)
    report = _new_report(config)
    _atomic_json(report_path, report)

    started_wall = time.time()
    started_monotonic = time.monotonic()
    previous_wall = started_wall
    previous_monotonic = started_monotonic
    next_metrics = started_monotonic
    next_score = started_monotonic
    next_canary = started_monotonic
    cycle = 0
    try:
        while True:
            now_monotonic = time.monotonic()
            elapsed = now_monotonic - started_monotonic
            if config.max_cycles is None and elapsed >= config.duration_seconds:
                break
            if config.max_cycles is not None and cycle >= config.max_cycles:
                break

            now_wall = time.time()
            wall_delta = now_wall - previous_wall
            monotonic_delta = now_monotonic - previous_monotonic
            if cycle > 0 and wall_delta - monotonic_delta > max(
                5.0, config.health_interval_seconds
            ):
                raise SoakBlocked(
                    f"host sleep or clock suspension detected: gap={wall_delta - monotonic_delta:.3f}s"
                )
            previous_wall = now_wall
            previous_monotonic = now_monotonic

            health_latencies = _probe_health(config.base_url)
            report["latency_seconds"]["non_score"].extend(health_latencies.values())
            report["latency_seconds"]["non_score"].append(_probe_auth(config.base_url))
            report["counters"]["health_cycles"] += 1

            runtime = _docker_state(config.container)
            runtime["observed_at"] = _utc_now()
            runtime["image_matches"] = config.expected_image in runtime["repo_digests"]
            if not runtime["running"]:
                raise SoakBlocked("application container is not running")
            if runtime["oom_killed"]:
                raise SoakBlocked("application container was OOM-killed")
            if runtime["restart_count"] != 0:
                raise SoakBlocked(f"application container restarted: {runtime['restart_count']}")
            if not runtime["image_matches"]:
                raise SoakBlocked(
                    f"application digest drift: expected={config.expected_image} "
                    f"actual={runtime['repo_digests']}"
                )
            report["runtime_samples"].append(runtime)

            if now_monotonic >= next_metrics:
                report["latency_seconds"]["prometheus"].append(
                    _probe_prometheus(config.prometheus_url)
                )
                report["counters"]["metrics_cycles"] += 1
                next_metrics = now_monotonic + config.metrics_interval_seconds

            if now_monotonic >= next_score:
                fingerprint, latency = _probe_score(
                    config.base_url,
                    api_key,
                    report["score_fingerprint"],
                )
                report["score_fingerprint"] = fingerprint
                report["latency_seconds"]["score"].append(latency)
                report["counters"]["score_cycles"] += 1
                next_score = now_monotonic + config.score_interval_seconds

            if now_monotonic >= next_canary:
                report["latency_seconds"]["non_score"].extend(
                    _probe_canary(config.base_url, api_key, report["run_id"], cycle)
                )
                report["counters"]["canary_cycles"] += 1
                next_canary = now_monotonic + config.canary_interval_seconds

            cycle += 1
            _atomic_json(report_path, report)
            if config.max_cycles is None:
                target = started_monotonic + cycle * config.health_interval_seconds
                time.sleep(max(0.0, target - time.monotonic()))

        elapsed = time.monotonic() - started_monotonic
        report["acceptance"] = _evaluate(report, elapsed, config)
        report["status"] = "PASS" if report["acceptance"]["passed"] else "BLOCKED"
        if report["status"] != "PASS":
            report["failure"] = "acceptance thresholds failed"
    except BaseException as exc:
        report["status"] = "BLOCKED"
        report["failure"] = f"{type(exc).__name__}: {exc}"
        report["acceptance"] = _evaluate(
            report,
            time.monotonic() - started_monotonic,
            config,
        )
    report["completed_at"] = _utc_now()
    _atomic_json(report_path, report)
    return report


def _parse_args(argv: Sequence[str] | None = None) -> SoakConfig:
    parser = argparse.ArgumentParser(description="Run the QingTian R8 staging soak gate")
    parser.add_argument("--base-url", default="http://127.0.0.1:18080")
    parser.add_argument("--prometheus-url", default="http://127.0.0.1:19090")
    parser.add_argument("--api-key-file", type=Path, required=True)
    parser.add_argument("--expected-image", required=True)
    parser.add_argument("--container", default="qingtian-r8-app-1")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--duration-seconds", type=int, default=DEFAULT_DURATION_SECONDS)
    parser.add_argument("--health-interval-seconds", type=int, default=30)
    parser.add_argument("--metrics-interval-seconds", type=int, default=60)
    parser.add_argument("--score-interval-seconds", type=int, default=15 * 60)
    parser.add_argument("--canary-interval-seconds", type=int, default=60 * 60)
    parser.add_argument("--max-cycles", type=int)
    args = parser.parse_args(argv)
    for name in (
        "duration_seconds",
        "health_interval_seconds",
        "metrics_interval_seconds",
        "score_interval_seconds",
        "canary_interval_seconds",
    ):
        if getattr(args, name) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if args.max_cycles is not None and args.max_cycles <= 0:
        parser.error("--max-cycles must be positive")
    return SoakConfig(**vars(args))


def main(argv: Sequence[str] | None = None) -> int:
    try:
        config = _parse_args(argv)
        report = run_soak(config)
    except BaseException as exc:
        print(f"BLOCKED_QINGTIAN_R8_STAGING_SOAK: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report["acceptance"], ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
