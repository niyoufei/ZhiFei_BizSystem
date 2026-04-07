from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT_PATH = Path("/Users/youfeini/Desktop/ZhiFei_BizSystem/scripts/stability_soak.py")
README_PATH = Path("/Users/youfeini/Desktop/ZhiFei_BizSystem/README.md")
MAKEFILE_PATH = Path("/Users/youfeini/Desktop/ZhiFei_BizSystem/Makefile")

spec = importlib.util.spec_from_file_location("stability_soak", SCRIPT_PATH)
stability_soak = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(stability_soak)


def _cycle(*, ok: bool, pids: list[str]) -> dict[str, object]:
    return {
        "cycle": 1,
        "started_at": "2026-04-02T00:00:00Z",
        "listener_pids": pids,
        "health": {"elapsed_ms": 12.3},
        "self_check": {"elapsed_ms": 22.4},
        "home": {"elapsed_ms": 8.1},
        "issues": [] if ok else ["health_http_failed"],
        "ok": ok,
    }


def test_build_report_marks_pass_when_all_cycles_and_doctors_are_green() -> None:
    report = stability_soak.build_report(
        base_url="http://127.0.0.1:8000",
        duration_seconds=120,
        interval_seconds=30,
        timeout_seconds=10.0,
        doctor_before={"ok": True, "returncode": 0, "elapsed_ms": 10},
        doctor_after={"ok": True, "returncode": 0, "elapsed_ms": 12},
        cycles=[_cycle(ok=True, pids=["123"]), _cycle(ok=True, pids=["123"])],
    )
    assert report["status"] == "PASS"
    assert report["summary"]["failed_cycle_count"] == 0
    assert report["summary"]["listener_pid_change_count"] == 0


def test_build_report_warns_when_listener_pid_changes_without_hard_failure() -> None:
    report = stability_soak.build_report(
        base_url="http://127.0.0.1:8000",
        duration_seconds=120,
        interval_seconds=30,
        timeout_seconds=10.0,
        doctor_before={"ok": True, "returncode": 0, "elapsed_ms": 10},
        doctor_after={"ok": True, "returncode": 0, "elapsed_ms": 12},
        cycles=[_cycle(ok=True, pids=["123"]), _cycle(ok=True, pids=["456"])],
    )
    assert report["status"] == "WARN"
    assert report["summary"]["listener_pid_change_count"] == 1
    assert any("listener pid changed" in item for item in report["warnings"])


def test_build_report_fails_when_cycle_or_doctor_fails() -> None:
    report = stability_soak.build_report(
        base_url="http://127.0.0.1:8000",
        duration_seconds=120,
        interval_seconds=30,
        timeout_seconds=10.0,
        doctor_before={"ok": False, "returncode": 1, "elapsed_ms": 10},
        doctor_after={"ok": True, "returncode": 0, "elapsed_ms": 12},
        cycles=[_cycle(ok=False, pids=["123"])],
    )
    assert report["status"] == "FAIL"
    assert report["summary"]["failed_cycle_count"] == 1


def test_soak_markdown_contains_key_sections() -> None:
    report = stability_soak.build_report(
        base_url="http://127.0.0.1:8000",
        duration_seconds=120,
        interval_seconds=30,
        timeout_seconds=10.0,
        doctor_before={"ok": True, "returncode": 0, "elapsed_ms": 10},
        doctor_after={"ok": True, "returncode": 0, "elapsed_ms": 12},
        cycles=[_cycle(ok=True, pids=["123"])],
    )
    text = stability_soak.to_markdown(report)
    assert "# Stability Soak Report" in text
    assert "## Summary" in text
    assert "## Doctor" in text
    assert "## Failed Cycles" in text


def test_makefile_and_readme_expose_soak_command() -> None:
    makefile = MAKEFILE_PATH.read_text(encoding="utf-8")
    readme = README_PATH.read_text(encoding="utf-8")
    assert ".PHONY:" in makefile and " soak " in makefile
    assert "soak:" in makefile
    assert "scripts/stability_soak.py --strict" in makefile
    assert "make soak SOAK_DURATION=600 SOAK_INTERVAL=30" in readme
    assert "build/stability_soak_latest.json" in readme
    assert "build/stability_soak_latest.md" in readme
