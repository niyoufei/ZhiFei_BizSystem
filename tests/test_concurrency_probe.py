from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.concurrency_probe import (
    CONCURRENCY_SCHEMA_VERSION,
    ConcurrencyGuardrails,
    ConcurrencyWorkload,
    evaluate_concurrency_guardrails,
    run_concurrency_probe,
)


def test_small_probe_has_exact_writes_valid_snapshots_and_backend_parity(tmp_path):
    report = run_concurrency_probe(
        tmp_path,
        workload=ConcurrencyWorkload(
            writer_count=2,
            writes_per_writer=4,
            reader_count=2,
            reads_per_reader=10,
            read_pause_seconds=0,
        ),
        guardrails=ConcurrencyGuardrails(
            read_p95_ms=10_000,
            write_p95_ms=10_000,
            writer_fairness_ratio=100,
        ),
    )

    assert report["schema_version"] == CONCURRENCY_SCHEMA_VERSION
    assert report["guardrails"]["passed"] is True
    assert report["guardrails"]["checks"]["backend_event_set_parity"] is True
    for result in report["backends"].values():
        assert result["completed_writes"] == 8
        assert result["final_value"] == 8
        assert result["final_events_unique"] is True
        assert result["reader_monotonic"] is True
        assert result["reader_snapshots_valid"] is True
        assert result["errors"] == []
    assert report["backends"]["sqlite"]["metadata"]["journal_mode"] == "wal"
    assert report["backends"]["sqlite"]["metadata"]["integrity_check"] == "ok"
    json.dumps(report, ensure_ascii=False)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("writer_count", 0, "writer_count must be positive"),
        ("writes_per_writer", 0, "writes_per_writer must be positive"),
        ("reader_count", 0, "reader_count must be positive"),
        ("reads_per_reader", 0, "reads_per_reader must be positive"),
        ("read_pause_seconds", -1, "read_pause_seconds must be non-negative"),
    ],
)
def test_workload_validation_rejects_invalid_values(field, value, message):
    values = {field: value}
    with pytest.raises(ValueError, match=message):
        ConcurrencyWorkload(**values).validate()


def test_guardrails_detect_lost_write_reader_error_latency_and_unfairness():
    result = {
        "expected_writes": 2,
        "completed_writes": 1,
        "writer_completions": {"0": 1},
        "writer_fairness_ratio": 99.0,
        "read": {"p95_ms": 999.0},
        "write": {"p95_ms": 999.0},
        "reader_monotonic": False,
        "reader_snapshots_valid": False,
        "final_value": 1,
        "final_events_unique": False,
        "final_event_fingerprint": "different",
        "errors": ["RuntimeError: injected"],
    }
    report = {
        "workload": {"writer_count": 1, "writes_per_writer": 2},
        "backends": {"json": result, "sqlite": dict(result)},
    }

    evaluation = evaluate_concurrency_guardrails(
        report,
        ConcurrencyGuardrails(
            read_p95_ms=100,
            write_p95_ms=100,
            writer_fairness_ratio=10,
        ),
    )

    assert evaluation["passed"] is False
    assert evaluation["checks"]["json_no_errors"] is False
    assert evaluation["checks"]["json_all_writes_completed"] is False
    assert evaluation["checks"]["json_final_value_exact"] is False
    assert evaluation["checks"]["json_reader_monotonic"] is False
    assert evaluation["checks"]["json_snapshots_valid"] is False
    assert evaluation["checks"]["json_read_p95"] is False
    assert evaluation["checks"]["json_write_p95"] is False
    assert evaluation["checks"]["json_writer_fairness"] is False


def test_runbook_pins_schema_and_concurrency_acceptance():
    runbook = (Path(__file__).parents[1] / "docs" / "concurrency-reliability.md").read_text(
        encoding="utf-8"
    )
    for required in (
        CONCURRENCY_SCHEMA_VERSION,
        "无丢写",
        "读快照一致",
        "writer_fairness_ratio",
        "同机回归参考",
    ):
        assert required in runbook
