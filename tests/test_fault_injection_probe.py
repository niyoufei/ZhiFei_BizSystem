from __future__ import annotations

import json
from pathlib import Path

from app.fault_injection_probe import (
    FAULT_INJECTION_SCHEMA_VERSION,
    evaluate_fault_injection_guardrails,
    run_fault_injection_probe,
)


def test_fault_matrix_passes_all_recovery_boundaries(tmp_path):
    report = run_fault_injection_probe(tmp_path)
    repeated = run_fault_injection_probe(tmp_path)

    assert report["schema_version"] == FAULT_INJECTION_SCHEMA_VERSION
    assert report["guardrails"]["passed"] is True
    assert report["guardrails"]["scenario_count"] == 10
    assert repeated["guardrails"]["passed"] is True
    assert all(scenario["unexpected_error"] is None for scenario in report["scenarios"])
    assert all(scenario["passed"] is True for scenario in report["scenarios"])
    assert {scenario["name"] for scenario in report["scenarios"]} == {
        "json_before_publish_failure",
        "json_after_publish_ack_failure",
        "json_corrupt_source_rejected",
        "sqlite_transaction_failure_rolls_back",
        "sqlite_abandoned_transaction_recovers",
        "sqlite_corrupt_payload_detected",
        "migration_import_failure_cleans_candidate",
        "migration_publish_failure_cleans_candidate",
        "recovery_export_failure_cleans_candidate",
        "round_trip_recovery_preserves_snapshot",
    }
    json.dumps(report, ensure_ascii=False)


def test_guardrail_fails_when_any_scenario_fails():
    report = {
        "scenarios": [
            {"name": "passed", "passed": True},
            {"name": "failed", "passed": False},
        ]
    }

    result = evaluate_fault_injection_guardrails(report)

    assert result == {
        "passed": False,
        "checks": {"passed": True, "failed": False},
        "scenario_count": 2,
    }


def test_runbook_pins_schema_fault_matrix_and_ambiguous_commit_boundary():
    runbook = (Path(__file__).parents[1] / "docs" / "fault-injection-recovery.md").read_text(
        encoding="utf-8"
    )
    for required in (
        FAULT_INJECTION_SCHEMA_VERSION,
        "发布前失败",
        "发布后确认失败",
        "最后一次已提交状态",
        "语义损坏",
        "10/10",
    ):
        assert required in runbook
