from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.performance_baseline import (
    BASELINE_SCHEMA_VERSION,
    PerformanceGuardrails,
    PerformanceWorkload,
    evaluate_guardrails,
    run_performance_baseline,
    summarize_latencies,
)


def test_latency_summary_uses_nearest_rank_percentiles():
    summary = summarize_latencies([1_000_000, 2_000_000, 3_000_000, 100_000_000])

    assert summary["samples"] == 4
    assert summary["p50_ms"] == 2.0
    assert summary["p95_ms"] == 100.0
    assert summary["p99_ms"] == 100.0
    assert summary["max_ms"] == 100.0
    assert summary["throughput_ops_per_second"] > 0


def test_performance_runbook_pins_schema_workload_and_semantic_gate():
    runbook = (Path(__file__).resolve().parents[1] / "docs/performance-baseline.md").read_text(
        encoding="utf-8"
    )
    for required in (
        BASELINE_SCHEMA_VERSION,
        "7affcd692e737bc1aab9a2b583cb7bfb85c4a7437e9fcb9290f35f39f7e4352a",
        "storage_semantic_parity=true",
        "meta.timestamp",
        "同机回归参考",
    ):
        assert required in runbook


def test_latency_summary_rejects_empty_samples():
    with pytest.raises(ValueError, match="requires samples"):
        summarize_latencies([])


def test_workload_validation_rejects_invalid_sizes():
    with pytest.raises(ValueError, match="at least one project"):
        PerformanceWorkload(project_count=0).validate()
    with pytest.raises(ValueError, match="must be non-negative"):
        PerformanceWorkload(read_warmup_iterations=-1).validate()


def test_small_baseline_proves_storage_parity_and_scoring_consistency(tmp_path):
    workload = PerformanceWorkload(
        project_count=3,
        submissions_per_project=2,
        read_iterations=3,
        read_warmup_iterations=1,
        write_iterations=2,
        scoring_iterations=3,
        scoring_warmup_iterations=1,
        text_repeat=1,
    )

    report = run_performance_baseline(
        tmp_path,
        workload=workload,
        guardrails=PerformanceGuardrails(
            storage_read_p95_ms=10_000,
            storage_write_p95_ms=10_000,
            scoring_p95_ms=10_000,
        ),
        score_callable=lambda text: {"score": len(text)},
    )

    assert report["schema_version"] == BASELINE_SCHEMA_VERSION
    assert (
        report["storage"]["json"]["final_fingerprint"]
        == report["storage"]["sqlite"]["final_fingerprint"]
    )
    assert report["storage"]["json"]["last_write_revision"] == 2
    assert report["storage"]["sqlite"]["last_write_revision"] == 2
    assert report["scoring"]["results_consistent"] is True
    assert report["guardrails"]["passed"] is True
    json.dumps(report, ensure_ascii=False)


def test_scoring_consistency_ignores_only_declared_meta_timestamp(tmp_path):
    call_count = 0

    def score(_text):
        nonlocal call_count
        call_count += 1
        return {
            "total_score": 88,
            "meta": {"timestamp": f"run-{call_count}", "engine": "v1"},
        }

    report = run_performance_baseline(
        tmp_path,
        workload=PerformanceWorkload(
            project_count=1,
            submissions_per_project=1,
            read_iterations=1,
            read_warmup_iterations=0,
            write_iterations=1,
            scoring_iterations=2,
            scoring_warmup_iterations=0,
            text_repeat=1,
        ),
        guardrails=PerformanceGuardrails(
            storage_read_p95_ms=10_000,
            storage_write_p95_ms=10_000,
            scoring_p95_ms=10_000,
        ),
        score_callable=score,
    )

    assert report["scoring"]["results_consistent"] is True
    assert report["scoring"]["excluded_volatile_fields"] == ["meta.timestamp"]


def test_guardrails_fail_on_semantic_drift_or_latency_regression():
    report = {
        "storage": {
            "json": {
                "final_fingerprint": "json",
                "read": {"p95_ms": 1.0},
                "write": {"p95_ms": 1.0},
                "read_results_consistent": True,
            },
            "sqlite": {
                "final_fingerprint": "sqlite",
                "read": {"p95_ms": 999.0},
                "write": {"p95_ms": 1.0},
                "read_results_consistent": True,
            },
        }
    }

    result = evaluate_guardrails(
        report,
        PerformanceGuardrails(
            storage_read_p95_ms=100.0,
            storage_write_p95_ms=100.0,
        ),
    )

    assert result["passed"] is False
    assert result["checks"]["storage_semantic_parity"] is False
    assert result["checks"]["sqlite_read_p95"] is False
