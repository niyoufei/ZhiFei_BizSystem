from __future__ import annotations

import argparse
import hashlib
import json
import platform
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict

from app.performance_baseline import summarize_latencies
from app.sqlite_repository import SQLiteRepositoryBackend
from app.storage import atomic_write_text, load_json, path_transaction, save_json

CONCURRENCY_SCHEMA_VERSION = "qingtian-concurrency-probe-v1"


@dataclass(frozen=True)
class ConcurrencyWorkload:
    writer_count: int = 4
    writes_per_writer: int = 25
    reader_count: int = 4
    reads_per_reader: int = 100
    read_pause_seconds: float = 0.001
    barrier_timeout_seconds: float = 30.0

    def validate(self) -> None:
        for name in (
            "writer_count",
            "writes_per_writer",
            "reader_count",
            "reads_per_reader",
        ):
            if int(getattr(self, name)) <= 0:
                raise ValueError(f"concurrency workload {name} must be positive")
        if self.read_pause_seconds < 0:
            raise ValueError("concurrency workload read_pause_seconds must be non-negative")
        if self.barrier_timeout_seconds <= 0:
            raise ValueError("concurrency workload barrier_timeout_seconds must be positive")


@dataclass(frozen=True)
class ConcurrencyGuardrails:
    read_p95_ms: float = 1000.0
    write_p95_ms: float = 2000.0
    writer_fairness_ratio: float = 10.0


class _JsonCounterBackend:
    name = "json"

    def __init__(self, path: Path) -> None:
        self.path = path
        save_json(path, {"value": 0, "events": []})

    def increment(self, event_id: str) -> None:
        with path_transaction(self.path):
            state = load_json(self.path, {"value": 0, "events": []})
            state["value"] += 1
            state["events"].append(event_id)
            save_json(self.path, state)

    def snapshot(self) -> Dict[str, Any]:
        return load_json(self.path, {"value": 0, "events": []})

    def metadata(self) -> Dict[str, object]:
        return {"locking": "path_transaction+flock", "journal_mode": None}


class _SQLiteCounterBackend:
    name = "sqlite"

    def __init__(self, path: Path) -> None:
        self.backend = SQLiteRepositoryBackend(
            path,
            store_defaults={"counter": {"value": 0, "events": []}},
        )

        @self.backend.transaction_factory("counter")
        def seed() -> None:
            self.backend.save("counter", {"value": 0, "events": []})

        @self.backend.transaction_factory("counter")
        def increment(event_id: str) -> None:
            state = self.backend.load("counter")
            state["value"] += 1
            state["events"].append(event_id)
            self.backend.save("counter", state)

        seed()
        self._increment = increment

    def increment(self, event_id: str) -> None:
        self._increment(event_id)

    def snapshot(self) -> Dict[str, Any]:
        return self.backend.load("counter")

    def metadata(self) -> Dict[str, object]:
        return {
            "locking": "BEGIN IMMEDIATE",
            "journal_mode": self.backend.journal_mode(),
            "integrity_check": self.backend.integrity_check(),
        }


def _state_is_valid(state: Any) -> bool:
    if not isinstance(state, dict):
        return False
    value = state.get("value")
    events = state.get("events")
    return (
        isinstance(value, int)
        and value >= 0
        and isinstance(events, list)
        and len(events) == value
        and all(isinstance(event, str) for event in events)
        and len(set(events)) == len(events)
    )


def _event_fingerprint(events: list[str]) -> str:
    payload = json.dumps(sorted(events), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _run_backend(
    backend: _JsonCounterBackend | _SQLiteCounterBackend,
    workload: ConcurrencyWorkload,
) -> Dict[str, object]:
    worker_count = workload.writer_count + workload.reader_count
    barrier = threading.Barrier(worker_count)
    expected_writes = workload.writer_count * workload.writes_per_writer
    started = time.perf_counter_ns()

    def writer(writer_index: int) -> Dict[str, object]:
        samples: list[int] = []
        completed = 0
        barrier.wait(timeout=workload.barrier_timeout_seconds)
        worker_started = time.perf_counter_ns()
        for operation_index in range(workload.writes_per_writer):
            event_id = f"writer-{writer_index:04d}-operation-{operation_index:06d}"
            operation_started = time.perf_counter_ns()
            backend.increment(event_id)
            samples.append(time.perf_counter_ns() - operation_started)
            completed += 1
        return {
            "kind": "writer",
            "worker": writer_index,
            "completed": completed,
            "samples": samples,
            "duration_ns": time.perf_counter_ns() - worker_started,
        }

    def reader(reader_index: int) -> Dict[str, object]:
        samples: list[int] = []
        previous_value = -1
        monotonic = True
        snapshots_valid = True
        minimum_value: int | None = None
        maximum_value: int | None = None
        barrier.wait(timeout=workload.barrier_timeout_seconds)
        for _operation_index in range(workload.reads_per_reader):
            operation_started = time.perf_counter_ns()
            state = backend.snapshot()
            samples.append(time.perf_counter_ns() - operation_started)
            snapshots_valid = snapshots_valid and _state_is_valid(state)
            value = state.get("value") if isinstance(state, dict) else None
            if not isinstance(value, int):
                monotonic = False
            else:
                monotonic = monotonic and value >= previous_value
                previous_value = value
                minimum_value = value if minimum_value is None else min(minimum_value, value)
                maximum_value = value if maximum_value is None else max(maximum_value, value)
            if workload.read_pause_seconds:
                time.sleep(workload.read_pause_seconds)
        return {
            "kind": "reader",
            "worker": reader_index,
            "monotonic": monotonic,
            "snapshots_valid": snapshots_valid,
            "minimum_value": minimum_value,
            "maximum_value": maximum_value,
            "samples": samples,
        }

    worker_results: list[Dict[str, object]] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(writer, index) for index in range(workload.writer_count)]
        futures.extend(executor.submit(reader, index) for index in range(workload.reader_count))
        for future in as_completed(futures):
            try:
                worker_results.append(future.result())
            except BaseException as exc:
                errors.append(f"{type(exc).__name__}: {exc}")

    final_state = backend.snapshot()
    writer_results = [result for result in worker_results if result["kind"] == "writer"]
    reader_results = [result for result in worker_results if result["kind"] == "reader"]
    write_samples = [sample for result in writer_results for sample in result.get("samples", [])]
    read_samples = [sample for result in reader_results for sample in result.get("samples", [])]
    writer_durations = [int(result["duration_ns"]) for result in writer_results]
    shortest_writer_duration = min(writer_durations, default=0)
    fairness_ratio = (
        max(writer_durations) / shortest_writer_duration if shortest_writer_duration > 0 else None
    )
    final_events = final_state.get("events", []) if isinstance(final_state, dict) else []
    return {
        "duration_ms": round((time.perf_counter_ns() - started) / 1_000_000.0, 6),
        "expected_writes": expected_writes,
        "completed_writes": sum(int(result["completed"]) for result in writer_results),
        "writer_completions": {
            str(result["worker"]): int(result["completed"]) for result in writer_results
        },
        "writer_fairness_ratio": round(fairness_ratio, 6) if fairness_ratio is not None else None,
        "read": summarize_latencies(read_samples) if read_samples else None,
        "write": summarize_latencies(write_samples) if write_samples else None,
        "reader_monotonic": (
            len(reader_results) == workload.reader_count
            and all(bool(result["monotonic"]) for result in reader_results)
        ),
        "reader_snapshots_valid": (
            len(reader_results) == workload.reader_count
            and all(bool(result["snapshots_valid"]) for result in reader_results)
        ),
        "reader_observed_ranges": {
            str(result["worker"]): [result["minimum_value"], result["maximum_value"]]
            for result in reader_results
        },
        "final_value": final_state.get("value") if isinstance(final_state, dict) else None,
        "final_events_unique": (
            isinstance(final_events, list)
            and all(isinstance(value, str) for value in final_events)
            and len(set(final_events)) == len(final_events)
        ),
        "final_event_fingerprint": _event_fingerprint(final_events)
        if isinstance(final_events, list) and all(isinstance(value, str) for value in final_events)
        else None,
        "errors": errors,
        "metadata": backend.metadata(),
    }


def evaluate_concurrency_guardrails(
    report: Dict[str, Any],
    guardrails: ConcurrencyGuardrails,
) -> Dict[str, object]:
    checks: Dict[str, bool] = {}
    expected_event_fingerprint: str | None = None
    for backend_name, result in report["backends"].items():
        expected_writes = result["expected_writes"]
        expected_completions = report["workload"]["writes_per_writer"]
        checks[f"{backend_name}_no_errors"] = not result["errors"]
        checks[f"{backend_name}_all_writes_completed"] = (
            result["completed_writes"] == expected_writes
            and len(result["writer_completions"]) == report["workload"]["writer_count"]
            and all(
                completed == expected_completions
                for completed in result["writer_completions"].values()
            )
        )
        checks[f"{backend_name}_final_value_exact"] = result["final_value"] == expected_writes
        checks[f"{backend_name}_events_unique"] = bool(result["final_events_unique"])
        checks[f"{backend_name}_reader_monotonic"] = bool(result["reader_monotonic"])
        checks[f"{backend_name}_snapshots_valid"] = bool(result["reader_snapshots_valid"])
        checks[f"{backend_name}_read_p95"] = (
            result["read"] is not None and result["read"]["p95_ms"] <= guardrails.read_p95_ms
        )
        checks[f"{backend_name}_write_p95"] = (
            result["write"] is not None and result["write"]["p95_ms"] <= guardrails.write_p95_ms
        )
        fairness_ratio = result["writer_fairness_ratio"]
        checks[f"{backend_name}_writer_fairness"] = (
            fairness_ratio is not None and fairness_ratio <= guardrails.writer_fairness_ratio
        )
        if expected_event_fingerprint is None:
            expected_event_fingerprint = result["final_event_fingerprint"]
        else:
            checks["backend_event_set_parity"] = (
                result["final_event_fingerprint"] == expected_event_fingerprint
            )
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "thresholds": asdict(guardrails),
    }


def run_concurrency_probe(
    work_directory: Path,
    *,
    workload: ConcurrencyWorkload | None = None,
    guardrails: ConcurrencyGuardrails | None = None,
    backend_factories: Dict[str, Callable[[Path], Any]] | None = None,
) -> Dict[str, object]:
    workload = workload or ConcurrencyWorkload()
    workload.validate()
    guardrails = guardrails or ConcurrencyGuardrails()
    work_directory = Path(work_directory).expanduser().resolve()
    work_directory.mkdir(parents=True, exist_ok=True)
    factories = backend_factories or {
        "json": lambda directory: _JsonCounterBackend(directory / "counter.json"),
        "sqlite": lambda directory: _SQLiteCounterBackend(directory / "qingtian.sqlite3"),
    }
    report: Dict[str, Any] = {
        "schema_version": CONCURRENCY_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "workload": asdict(workload),
        "backends": {},
    }
    for backend_name, factory in factories.items():
        report["backends"][backend_name] = _run_backend(
            factory(work_directory / backend_name),
            workload,
        )
    report["guardrails"] = evaluate_concurrency_guardrails(report, guardrails)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="QingTian storage concurrency probe")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--work-directory", type=Path)
    parser.add_argument("--writers", type=int, default=4)
    parser.add_argument("--writes-per-writer", type=int, default=25)
    parser.add_argument("--readers", type=int, default=4)
    parser.add_argument("--reads-per-reader", type=int, default=100)
    args = parser.parse_args(argv)
    workload = ConcurrencyWorkload(
        writer_count=args.writers,
        writes_per_writer=args.writes_per_writer,
        reader_count=args.readers,
        reads_per_reader=args.reads_per_reader,
    )
    if args.work_directory is None:
        with tempfile.TemporaryDirectory(prefix="qingtian-concurrency-") as temporary:
            report = run_concurrency_probe(Path(temporary), workload=workload)
    else:
        report = run_concurrency_probe(args.work_directory, workload=workload)
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output is not None:
        atomic_write_text(args.output.expanduser().resolve(), payload)
    print(payload)
    return 0 if report["guardrails"]["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
