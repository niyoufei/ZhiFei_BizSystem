from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from scripts import staging_drill, staging_soak

REPO_ROOT = Path(__file__).parents[1]


def test_score_fingerprint_ignores_only_timestamp():
    first = {"total_score": 88, "meta": {"timestamp": "one", "mode": "rules"}}
    second = {"total_score": 88, "meta": {"timestamp": "two", "mode": "rules"}}
    changed = {"total_score": 87, "meta": {"timestamp": "two", "mode": "rules"}}

    assert staging_soak._score_fingerprint(first) == staging_soak._score_fingerprint(second)
    assert staging_soak._score_fingerprint(first) != staging_soak._score_fingerprint(changed)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("512MiB / 1GiB", 512 * 1024 * 1024), ("1.5GiB / 2GiB", int(1.5 * 1024**3))],
)
def test_memory_parser(raw, expected):
    assert staging_soak._parse_memory_bytes(raw) == expected


def test_one_cycle_soak_is_atomic_and_refuses_overwrite(tmp_path, monkeypatch):
    key_file = tmp_path / "key.txt"
    key_file.write_text("r8-test-key\n", encoding="utf-8")
    expected_image = "ghcr.io/example/app@sha256:" + "a" * 64
    monkeypatch.setattr(
        staging_soak,
        "_probe_health",
        lambda _url: {"health": 0.01, "ready": 0.02},
    )
    monkeypatch.setattr(staging_soak, "_probe_auth", lambda _url: 0.01)
    monkeypatch.setattr(staging_soak, "_probe_prometheus", lambda _url: 0.01)
    monkeypatch.setattr(
        staging_soak,
        "_probe_score",
        lambda _url, _key, expected: (expected or "score-fingerprint", 0.03),
    )
    monkeypatch.setattr(
        staging_soak,
        "_probe_canary",
        lambda _url, _key, _run_id, _cycle: [0.01, 0.01, 0.01, 0.01],
    )
    monkeypatch.setattr(
        staging_soak,
        "_docker_state",
        lambda _container: {
            "running": True,
            "oom_killed": False,
            "restart_count": 0,
            "repo_digests": [expected_image],
            "memory_bytes": 128 * 1024 * 1024,
            "wal_bytes": 4096,
        },
    )
    config = staging_soak.SoakConfig(
        base_url="http://service",
        prometheus_url="http://prometheus",
        api_key_file=key_file,
        expected_image=expected_image,
        container="app",
        output_dir=tmp_path / "run",
        max_cycles=1,
    )

    report = staging_soak.run_soak(config)

    assert report["status"] == "PASS"
    assert json.loads((config.output_dir / "soak-report.json").read_text())["status"] == "PASS"
    with pytest.raises(staging_soak.SoakBlocked, match="refusing to overwrite"):
        staging_soak.run_soak(config)


def test_digest_drift_blocks_soak(tmp_path, monkeypatch):
    key_file = tmp_path / "key.txt"
    key_file.write_text("r8-test-key\n", encoding="utf-8")
    monkeypatch.setattr(
        staging_soak,
        "_probe_health",
        lambda _url: {"health": 0.01, "ready": 0.01},
    )
    monkeypatch.setattr(staging_soak, "_probe_auth", lambda _url: 0.01)
    monkeypatch.setattr(
        staging_soak,
        "_docker_state",
        lambda _container: {
            "running": True,
            "oom_killed": False,
            "restart_count": 0,
            "repo_digests": ["ghcr.io/example/app@sha256:" + "b" * 64],
            "memory_bytes": 1,
            "wal_bytes": 0,
        },
    )
    config = staging_soak.SoakConfig(
        base_url="http://service",
        prometheus_url="http://prometheus",
        api_key_file=key_file,
        expected_image="ghcr.io/example/app@sha256:" + "a" * 64,
        container="app",
        output_dir=tmp_path / "run",
        max_cycles=1,
    )

    report = staging_soak.run_soak(config)

    assert report["status"] == "BLOCKED"
    assert "digest drift" in report["failure"]


def test_drill_rejects_movable_image_tags_before_docker_access(tmp_path):
    key_file = tmp_path / "key.txt"
    key_file.write_text("r8-test-key\n", encoding="utf-8")

    with pytest.raises(staging_drill.DrillBlocked, match="immutable sha256"):
        staging_drill.run_drill(
            repo_root=REPO_ROOT,
            project="qingtian-r8",
            api_key_file=key_file,
            candidate_image="ghcr.io/example/app:1.1.0-rc.4",
            rc1_image="ghcr.io/example/app:1.1.0-rc.1",
            output_dir=tmp_path / "drill",
            restore_port=18081,
        )


def test_monitoring_profile_is_localhost_only_secret_backed_and_digest_pinned():
    compose_text = (REPO_ROOT / "docker-compose.monitoring.yml").read_text(encoding="utf-8")
    compose = yaml.safe_load(compose_text)

    for name in ("prometheus", "alertmanager", "grafana"):
        service = compose["services"][name]
        assert all(str(port).startswith("127.0.0.1:") for port in service["ports"])
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        assert "no-new-privileges:true" in service["security_opt"]
    assert "admin/admin" not in compose_text
    assert "@sha256:" in compose["services"]["prometheus"]["image"]
    assert "@sha256:" in compose["services"]["alertmanager"]["image"]
    assert compose["services"]["grafana"]["image"].startswith("${R8_GRAFANA_IMAGE:")
    assert "GF_SECURITY_ADMIN_PASSWORD__FILE" in compose_text
    assert compose["services"]["grafana"]["environment"]["GF_PLUGINS_PREINSTALL_DISABLED"] == "true"


def test_grafana_image_uses_pinned_sources_and_verified_plugin_archives():
    dockerfile = (REPO_ROOT / "Dockerfile.grafana").read_text(encoding="utf-8")

    assert dockerfile.count("FROM ") == 2
    assert dockerfile.count("@sha256:") == 2
    assert "prometheus-13.1.9.linux_${TARGETARCH}.zip" in dockerfile
    assert "33c5316c52e8745e38ea844d5357723c0ebe15ce28cdaf6000cdffe95a01af72" in dockerfile
    assert "a5f89b1c61d591ba146b8522095c7c78da5ec4aa80f5e865488914be472d8bdd" in dockerfile
    assert "sha256sum -c" in dockerfile


def test_prometheus_and_alert_rules_reference_emitted_metric_contract():
    prometheus = (REPO_ROOT / "prometheus/prometheus.yml").read_text(encoding="utf-8")
    alerts = (REPO_ROOT / "prometheus/alerts/qingtian.yml").read_text(encoding="utf-8")

    assert 'targets: ["app:8000"]' in prometheus
    assert "credentials_file: /run/secrets/qingtian_api_key" in prometheus
    for obsolete in (
        "qingtian_health_status",
        "qingtian_scoring_duration_seconds",
        "qingtian_scoring_score_value",
        "qingtian_scoring_requests_total",
        'status=~"5.."',
    ):
        assert obsolete not in alerts
    for required in (
        "qingtian_readiness_status",
        "qingtian_http_request_duration_seconds_bucket",
        "qingtian_score_distribution_sum",
        "qingtian_score_requests_total",
        "qingtian_rate_limit_exceeded_total",
        'status_code=~"5.."',
    ):
        assert required in alerts


def test_readiness_alert_has_trigger_and_recovery_rule_test():
    rule_test = yaml.safe_load(
        (REPO_ROOT / "prometheus/tests/qingtian.test.yml").read_text(encoding="utf-8")
    )

    readiness = rule_test["tests"][0]
    evaluations = readiness["alert_rule_test"]
    assert readiness["name"] == "readiness alert fires and recovers"
    assert evaluations[0]["exp_alerts"]
    assert evaluations[-1]["exp_alerts"] == []
