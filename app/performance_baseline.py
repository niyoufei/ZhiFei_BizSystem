from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import statistics
import tempfile
import time
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable

from app.sqlite_repository import SQLiteRepositoryBackend
from app.storage import atomic_write_text, load_json, path_transaction, save_json

BASELINE_SCHEMA_VERSION = "qingtian-performance-baseline-v1"


@dataclass(frozen=True)
class PerformanceWorkload:
    project_count: int = 100
    submissions_per_project: int = 10
    read_iterations: int = 30
    read_warmup_iterations: int = 3
    write_iterations: int = 20
    scoring_iterations: int = 10
    scoring_warmup_iterations: int = 1
    text_repeat: int = 4

    def validate(self) -> None:
        for name, value in asdict(self).items():
            if int(value) < 0:
                raise ValueError(f"performance workload {name} must be non-negative")
        if self.project_count <= 0:
            raise ValueError("performance workload requires at least one project")
        if self.submissions_per_project <= 0:
            raise ValueError("performance workload requires submissions")
        if self.read_iterations <= 0 or self.write_iterations <= 0:
            raise ValueError("performance workload requires read and write iterations")


@dataclass(frozen=True)
class PerformanceGuardrails:
    storage_read_p95_ms: float = 500.0
    storage_write_p95_ms: float = 1500.0
    scoring_p95_ms: float = 5000.0


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def summarize_latencies(samples_ns: Iterable[int]) -> Dict[str, float | int]:
    samples = sorted(int(value) for value in samples_ns)
    if not samples:
        raise ValueError("latency summary requires samples")

    def percentile(fraction: float) -> float:
        index = max(0, math.ceil(len(samples) * fraction) - 1)
        return samples[index] / 1_000_000.0

    total_seconds = sum(samples) / 1_000_000_000.0
    return {
        "samples": len(samples),
        "mean_ms": round(statistics.fmean(samples) / 1_000_000.0, 6),
        "p50_ms": round(percentile(0.50), 6),
        "p95_ms": round(percentile(0.95), 6),
        "p99_ms": round(percentile(0.99), 6),
        "max_ms": round(samples[-1] / 1_000_000.0, 6),
        "throughput_ops_per_second": round(len(samples) / total_seconds, 3)
        if total_seconds > 0
        else 0.0,
    }


def _measure(operation: Callable[[], Any], iterations: int) -> tuple[list[int], list[Any]]:
    samples: list[int] = []
    results: list[Any] = []
    for _index in range(iterations):
        started = time.perf_counter_ns()
        result = operation()
        samples.append(time.perf_counter_ns() - started)
        results.append(result)
    return samples, results


def _build_dataset(workload: PerformanceWorkload) -> Dict[str, list[dict[str, object]]]:
    projects = [
        {
            "id": f"p{project_index:04d}",
            "name": f"benchmark-project-{project_index:04d}",
            "benchmark_revision": 0,
        }
        for project_index in range(workload.project_count)
    ]
    submissions = []
    for project_index in range(workload.project_count):
        for submission_index in range(workload.submissions_per_project):
            submissions.append(
                {
                    "id": f"s{project_index:04d}-{submission_index:04d}",
                    "project_id": f"p{project_index:04d}",
                    "filename": f"submission-{submission_index:04d}.txt",
                    "total_score": float((project_index + submission_index) % 101),
                    "benchmark_revision": 0,
                    "report": {
                        "dimension_scores": {
                            f"D{dimension:02d}": float(dimension) for dimension in range(1, 17)
                        }
                    },
                }
            )
    return {"projects": projects, "submissions": submissions}


class _JsonBenchmarkBackend:
    def __init__(self, directory: Path, dataset: Dict[str, Any]) -> None:
        self.directory = directory
        self.paths = {
            "projects": directory / "projects.json",
            "submissions": directory / "submissions.json",
        }
        directory.mkdir(parents=True, exist_ok=True)
        for name, path in self.paths.items():
            save_json(path, deepcopy(dataset[name]))

    def snapshot(self) -> Dict[str, Any]:
        with path_transaction(*self.paths.values()):
            return {name: load_json(path, []) for name, path in self.paths.items()}

    def read_fingerprint(self) -> str:
        return _fingerprint(self.snapshot())

    def write_revision(self, revision: int) -> None:
        with path_transaction(*self.paths.values()):
            projects = load_json(self.paths["projects"], [])
            submissions = load_json(self.paths["submissions"], [])
            projects[0]["benchmark_revision"] = revision
            submissions[0]["benchmark_revision"] = revision
            save_json(self.paths["projects"], projects)
            save_json(self.paths["submissions"], submissions)

    def size_bytes(self) -> int:
        return sum(path.stat().st_size for path in self.paths.values())


class _SQLiteBenchmarkBackend:
    def __init__(self, database_path: Path, dataset: Dict[str, Any]) -> None:
        self.backend = SQLiteRepositoryBackend(
            database_path,
            store_defaults={"projects": [], "submissions": []},
        )

        @self.backend.transaction_factory("projects", "submissions")
        def seed() -> None:
            self.backend.save("projects", deepcopy(dataset["projects"]))
            self.backend.save("submissions", deepcopy(dataset["submissions"]))

        seed()

    def snapshot(self) -> Dict[str, Any]:
        @self.backend.transaction_factory("projects", "submissions")
        def read() -> Dict[str, Any]:
            return {
                "projects": self.backend.load("projects"),
                "submissions": self.backend.load("submissions"),
            }

        return read()

    def read_fingerprint(self) -> str:
        return _fingerprint(self.snapshot())

    def write_revision(self, revision: int) -> None:
        @self.backend.transaction_factory("projects", "submissions")
        def write() -> None:
            projects = self.backend.load("projects")
            submissions = self.backend.load("submissions")
            projects[0]["benchmark_revision"] = revision
            submissions[0]["benchmark_revision"] = revision
            self.backend.save("projects", projects)
            self.backend.save("submissions", submissions)

        write()

    def size_bytes(self) -> int:
        checkpoint = self.backend.checkpoint()
        if checkpoint[0] != 0:
            raise RuntimeError(f"benchmark SQLite checkpoint remained busy: {checkpoint}")
        return self.backend.database_path.stat().st_size


def _benchmark_storage_backend(
    backend: _JsonBenchmarkBackend | _SQLiteBenchmarkBackend,
    workload: PerformanceWorkload,
) -> Dict[str, object]:
    for _index in range(workload.read_warmup_iterations):
        backend.read_fingerprint()
    read_samples, read_results = _measure(
        backend.read_fingerprint,
        workload.read_iterations,
    )

    revision = 0

    def write() -> int:
        nonlocal revision
        revision += 1
        backend.write_revision(revision)
        return revision

    write_samples, write_results = _measure(write, workload.write_iterations)
    final_snapshot = backend.snapshot()
    return {
        "read": summarize_latencies(read_samples),
        "write": summarize_latencies(write_samples),
        "read_results_consistent": len(set(read_results)) == 1,
        "last_write_revision": write_results[-1],
        "final_fingerprint": _fingerprint(final_snapshot),
        "size_bytes": backend.size_bytes(),
    }


def _scoring_text(repeat: int) -> str:
    paragraph = (
        "本工程采用分区流水施工，关键线路实行日检查、周纠偏。"
        "基坑、高支模、临时用电和起重吊装设置专项验收，质量安全责任落实到人。"
        "材料进场执行见证取样，进度偏差超过阈值时立即调整资源配置。"
    )
    return "\n".join(paragraph for _index in range(max(1, repeat)))


def _normalize_scoring_result(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        normalized = value.model_dump()
    elif isinstance(value, dict):
        normalized = deepcopy(value)
    else:
        return str(value)
    meta = normalized.get("meta")
    if isinstance(meta, dict):
        meta.pop("timestamp", None)
    return normalized


def _benchmark_scoring(
    score_callable: Callable[[str], Any],
    workload: PerformanceWorkload,
) -> Dict[str, object]:
    text = _scoring_text(workload.text_repeat)
    for _index in range(workload.scoring_warmup_iterations):
        score_callable(text)
    samples, results = _measure(
        lambda: _fingerprint(_normalize_scoring_result(score_callable(text))),
        workload.scoring_iterations,
    )
    return {
        "latency": summarize_latencies(samples),
        "result_fingerprint": results[0],
        "results_consistent": len(set(results)) == 1,
        "input_chars": len(text),
        "excluded_volatile_fields": ["meta.timestamp"],
    }


def evaluate_guardrails(
    report: Dict[str, Any],
    guardrails: PerformanceGuardrails,
) -> Dict[str, object]:
    storage = report["storage"]
    checks = {
        "storage_semantic_parity": (
            storage["json"]["final_fingerprint"] == storage["sqlite"]["final_fingerprint"]
        ),
        "json_read_p95": (storage["json"]["read"]["p95_ms"] <= guardrails.storage_read_p95_ms),
        "sqlite_read_p95": (storage["sqlite"]["read"]["p95_ms"] <= guardrails.storage_read_p95_ms),
        "json_write_p95": (storage["json"]["write"]["p95_ms"] <= guardrails.storage_write_p95_ms),
        "sqlite_write_p95": (
            storage["sqlite"]["write"]["p95_ms"] <= guardrails.storage_write_p95_ms
        ),
        "json_reads_consistent": storage["json"]["read_results_consistent"],
        "sqlite_reads_consistent": storage["sqlite"]["read_results_consistent"],
    }
    scoring = report.get("scoring")
    if scoring is not None:
        checks["scoring_p95"] = scoring["latency"]["p95_ms"] <= guardrails.scoring_p95_ms
        checks["scoring_results_consistent"] = scoring["results_consistent"]
    return {
        "passed": all(bool(value) for value in checks.values()),
        "checks": checks,
        "thresholds": asdict(guardrails),
    }


def run_performance_baseline(
    work_directory: Path,
    *,
    workload: PerformanceWorkload | None = None,
    guardrails: PerformanceGuardrails | None = None,
    score_callable: Callable[[str], Any] | None = None,
) -> Dict[str, object]:
    workload = workload or PerformanceWorkload()
    workload.validate()
    guardrails = guardrails or PerformanceGuardrails()
    work_directory = Path(work_directory).expanduser().resolve()
    work_directory.mkdir(parents=True, exist_ok=True)
    dataset = _build_dataset(workload)
    report: Dict[str, Any] = {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "workload": asdict(workload),
        "workload_fingerprint": _fingerprint(dataset),
        "storage": {},
    }
    report["storage"]["json"] = _benchmark_storage_backend(
        _JsonBenchmarkBackend(work_directory / "json", dataset),
        workload,
    )
    report["storage"]["sqlite"] = _benchmark_storage_backend(
        _SQLiteBenchmarkBackend(work_directory / "qingtian.sqlite3", dataset),
        workload,
    )
    if score_callable is not None and workload.scoring_iterations > 0:
        report["scoring"] = _benchmark_scoring(score_callable, workload)
    report["guardrails"] = evaluate_guardrails(report, guardrails)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="QingTian deterministic performance baseline")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--work-directory", type=Path)
    parser.add_argument("--projects", type=int, default=100)
    parser.add_argument("--submissions-per-project", type=int, default=10)
    parser.add_argument("--read-iterations", type=int, default=30)
    parser.add_argument("--write-iterations", type=int, default=20)
    parser.add_argument("--scoring-iterations", type=int, default=10)
    args = parser.parse_args(argv)
    workload = PerformanceWorkload(
        project_count=args.projects,
        submissions_per_project=args.submissions_per_project,
        read_iterations=args.read_iterations,
        write_iterations=args.write_iterations,
        scoring_iterations=args.scoring_iterations,
    )

    from app.config import load_config
    from app.engine.scorer import score_text

    config = load_config()

    def score_callable(text: str) -> Any:
        return score_text(text, config.rubric, config.lexicon)

    if args.work_directory is None:
        with tempfile.TemporaryDirectory(prefix="qingtian-performance-") as temporary:
            report = run_performance_baseline(
                Path(temporary),
                workload=workload,
                score_callable=score_callable,
            )
    else:
        report = run_performance_baseline(
            args.work_directory,
            workload=workload,
            score_callable=score_callable,
        )
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output is not None:
        atomic_write_text(args.output.expanduser().resolve(), payload)
    print(payload)
    return 0 if report["guardrails"]["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
