from __future__ import annotations

from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib

from app.version import RELEASE_TAG, RELEASE_VERSION, __version__
from scripts.verify_release import verify_release_tag

REPO_ROOT = Path(__file__).parents[1]


def test_release_version_is_derived_from_single_runtime_source():
    assert __version__ == "1.1.0rc1"
    assert RELEASE_VERSION == "1.1.0-rc.1"
    assert RELEASE_TAG == "v1.1.0-rc.1"

    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["dynamic"] == ["version"]
    assert pyproject["tool"]["setuptools"]["dynamic"]["version"] == {
        "attr": "app.version.__version__"
    }
    assert pyproject["tool"]["setuptools"]["packages"]["find"]["include"] == ["app*"]


def test_release_tag_verifier_accepts_only_the_certified_tag():
    assert verify_release_tag(RELEASE_TAG) == {
        "package_version": "1.1.0rc1",
        "release_version": "1.1.0-rc.1",
        "release_tag": "v1.1.0-rc.1",
    }
    with pytest.raises(ValueError, match="release tag mismatch"):
        verify_release_tag("v1.1.0")


def test_openapi_security_upgrade_preserves_certified_schema_surface():
    from app.main import app

    schema = app.openapi()
    component_schemas = schema["components"]["schemas"]
    encoded = str(component_schemas)

    assert "contentMediaType" not in encoded
    assert component_schemas["ValidationError"]["properties"].keys() == {
        "loc",
        "msg",
        "type",
    }


def test_release_workflow_enforces_certified_delivery_gates():
    workflow = (REPO_ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    for required in (
        '"v*.*.*-rc.*"',
        'python-version: ["3.10", "3.11", "3.12"]',
        'python -m scripts.verify_release --tag "${GITHUB_REF_NAME}"',
        "pip-audit --requirement requirements-runtime.txt",
        "zricethezav/gitleaks:v8.30.0",
        "severity: HIGH,CRITICAL",
        "aquasecurity/trivy-action@v0.36.0",
        "--read-only",
        "--cap-drop ALL",
        "tesseract --list-langs",
        "PRAGMA integrity_check",
        "docker restart qingtian-release",
        "platforms: linux/amd64,linux/arm64",
        "sbom: true",
        "provenance: mode=max",
        "actions/attest-build-provenance@v3",
        "actions/attest-sbom@v3",
        "--prerelease",
    ):
        assert required in workflow
