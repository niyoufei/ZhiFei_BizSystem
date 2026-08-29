from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

from scripts.container_entrypoint import (
    build_uvicorn_command,
    configure_api_keys,
    load_deployment_config,
    public_config,
)
from scripts.container_healthcheck import probe

REPO_ROOT = Path(__file__).parents[1]


def _production_environment(**overrides):
    environment = {
        "QINGTIAN_ENV": "production",
        "API_KEYS": "real-key-one,real-key-two",
        "QINGTIAN_DATA_DIR": "/var/lib/qingtian",
        "QINGTIAN_STORAGE_BACKEND": "sqlite",
        "QINGTIAN_SQLITE_PATH": "/var/lib/qingtian/qingtian.sqlite3",
        "PORT": "8000",
        "WEB_CONCURRENCY": "1",
    }
    environment.update(overrides)
    return environment


def test_production_entrypoint_is_fail_closed_and_builds_single_worker_command():
    environment = _production_environment()
    config = load_deployment_config(environment)
    command = build_uvicorn_command(config)

    assert config.environment == "production"
    assert config.storage_backend == "sqlite"
    assert config.workers == 1
    assert command[:4] == [sys.executable, "-m", "uvicorn", "app.main:app"]
    assert command[command.index("--workers") + 1] == "1"
    assert command[command.index("--port") + 1] == "8000"
    assert command[command.index("--forwarded-allow-ips") + 1] == "127.0.0.1"
    assert "API_KEYS" not in json.dumps(public_config(config))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"API_KEYS": ""}, "production requires API_KEYS"),
        ({"API_KEYS": "change-me"}, "placeholder value"),
        ({"WEB_CONCURRENCY": "2"}, "must remain 1"),
        ({"PORT": "70000"}, "between 1 and 65535"),
        ({"QINGTIAN_DATA_DIR": "relative/data"}, "must be an absolute path"),
        ({"QINGTIAN_STORAGE_BACKEND": "memory"}, "must be json or sqlite"),
    ],
)
def test_production_entrypoint_rejects_unsafe_configuration(overrides, message):
    with pytest.raises(RuntimeError, match=message):
        load_deployment_config(_production_environment(**overrides))


def test_secret_file_support_normalizes_lines_without_exposing_path_contents():
    environment = {"QINGTIAN_API_KEYS_FILE": "/run/secrets/qingtian_api_keys"}

    keys = configure_api_keys(
        environment,
        read_text=lambda path: "first-key\nsecond-key, third-key\n",
    )

    assert keys == ["first-key", "second-key", "third-key"]
    assert environment["API_KEYS"] == "first-key,second-key,third-key"


class _Response:
    def __init__(self, payload):
        self.status = 200
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


def test_container_healthcheck_requires_liveness_and_all_readiness_checks():
    payloads = {
        "http://service/health": {"status": "healthy"},
        "http://service/ready": {
            "status": "ready",
            "checks": {"config": True, "data_dirs": True},
        },
    }

    report = probe("http://service", opener=lambda url, timeout: _Response(payloads[url]))

    assert report["passed"] is True
    payloads["http://service/ready"]["checks"]["data_dirs"] = False
    assert (
        probe("http://service", opener=lambda url, timeout: _Response(payloads[url]))["passed"]
        is False
    )


def test_container_and_compose_pin_security_persistence_and_health_contracts():
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = yaml.safe_load((REPO_ROOT / "compose.yaml").read_text(encoding="utf-8"))
    service = compose["services"]["app"]

    for required in (
        "FROM python:${PYTHON_VERSION}-slim-bookworm",
        "USER 10001:10001",
        'ENTRYPOINT ["python", "-m", "scripts.container_entrypoint"]',
        'CMD ["python", "scripts/container_healthcheck.py"]',
        "tesseract-ocr-chi-sim",
        "org.opencontainers.image.version",
        "APP_VERSION=1.1.0-rc.4",
    ):
        assert required in dockerfile
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in service["security_opt"]
    assert service["environment"]["WEB_CONCURRENCY"] == "1"
    assert service["environment"]["QINGTIAN_STORAGE_BACKEND"] == "sqlite"
    assert "qingtian_data:/var/lib/qingtian" in service["volumes"]
    assert "qingtian_config:/srv/qingtian/app/resources" in service["volumes"]
    assert service["secrets"] == ["qingtian_api_keys"]


def test_runtime_requirements_exclude_test_and_alternate_ui_dependencies():
    requirements = (REPO_ROOT / "requirements-runtime.txt").read_text(encoding="utf-8")

    for forbidden in ("pytest", "ruff", "black", "pre-commit", "streamlit", "httpx"):
        assert forbidden not in requirements.lower()
    for required in ("fastapi==", "uvicorn==", "pydantic==", "slowapi=="):
        assert required in requirements


def test_ci_container_smoke_uses_the_production_security_boundary():
    workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert 'branches: [main, master, "codex/qingtian-*"]' in workflow
    for required in (
        "--read-only",
        "--cap-drop ALL",
        "--security-opt no-new-privileges",
        "qingtian-ci-data:/var/lib/qingtian",
        "qingtian-ci-config:/srv/qingtian/app/resources",
        "python scripts/container_healthcheck.py",
        'test "$(docker exec qingtian-ci id -u)" = "10001"',
    ):
        assert required in workflow
    assert "placeholder" not in workflow
