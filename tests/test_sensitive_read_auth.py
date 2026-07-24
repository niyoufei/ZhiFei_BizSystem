"""Fail-closed authentication gates for sensitive GET/HEAD routes."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.auth import API_KEYS_ENV, verify_api_key

TEST_API_KEY = "test-auth-key-do-not-use"
AUTH_HEADERS = {"X-API-Key": TEST_API_KEY}
_ORIGINAL_API_KEYS = os.environ.get(API_KEYS_ENV)
os.environ[API_KEYS_ENV] = TEST_API_KEY
try:
    from app.main import app
finally:
    if _ORIGINAL_API_KEYS is None:
        os.environ.pop(API_KEYS_ENV, None)
    else:
        os.environ[API_KEYS_ENV] = _ORIGINAL_API_KEYS


@pytest.fixture(autouse=True)
def isolate_api_keys():
    """Keep route-gate tests independent from developer environment state."""
    with patch.dict(os.environ, {API_KEYS_ENV: TEST_API_KEY}, clear=False):
        yield


PUBLIC_GET_HEAD_PATHS = frozenset(
    {
        "/",
        "/health",
        "/ready",
        "/favicon.ico",
        "/apple-touch-icon-precomposed.png",
        "/apple-touch-icon.png",
        "/web/upload_materials",
        "/web/upload_shigong",
        "/web/score_shigong",
    }
)


def _dependency_calls(route: APIRoute) -> set[object]:
    calls: set[object] = set()
    pending = list(route.dependant.dependencies)
    while pending:
        dependency = pending.pop()
        calls.add(dependency.call)
        pending.extend(dependency.dependencies)
    return calls


def _read_routes() -> list[APIRoute]:
    return [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and {"GET", "HEAD"}.intersection(route.methods or set())
    ]


def test_every_non_public_get_head_route_requires_api_key():
    routes = _read_routes()
    protected_paths = {route.path for route in routes if route.path not in PUBLIC_GET_HEAD_PATHS}
    assert {
        "/api/v1/projects",
        "/api/v1/projects/{project_id}/materials",
        "/api/v1/projects/{project_id}/submissions",
        "/api/v1/submissions/{submission_id}/reports/latest",
        "/api/v1/projects/{project_id}/submissions/{submission_id}/evidence_trace",
        "/api/v1/projects/{project_id}/submissions/{submission_id}/scoring_basis",
        "/api/v1/projects/{project_id}/ground_truth",
        "/api/v1/calibration/models",
        "/api/v1/projects/{project_id}/patches",
    }.issubset(protected_paths)

    missing_auth = sorted(
        f"{','.join(sorted((route.methods or set()) & {'GET', 'HEAD'}))} {route.path}"
        for route in routes
        if route.path not in PUBLIC_GET_HEAD_PATHS
        and verify_api_key not in _dependency_calls(route)
    )
    assert missing_auth == []


def test_public_get_head_allowlist_has_no_api_key_dependency():
    route_by_path = {route.path: route for route in _read_routes()}
    unexpected_auth = sorted(
        path
        for path in PUBLIC_GET_HEAD_PATHS
        if path in route_by_path and verify_api_key in _dependency_calls(route_by_path[path])
    )
    assert unexpected_auth == []


def test_health_and_ready_remain_public_when_auth_is_unconfigured():
    client = TestClient(app)
    with (
        patch.dict(os.environ, {}, clear=True),
        patch("app.main.load_config"),
        patch("app.main.ensure_data_dirs"),
    ):
        os.environ.pop(API_KEYS_ENV, None)
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 200


def test_sensitive_read_fails_closed_without_configured_keys():
    client = TestClient(app)
    with (
        patch.dict(os.environ, {}, clear=True),
        patch("app.main.load_projects") as load_projects,
    ):
        os.environ.pop(API_KEYS_ENV, None)
        response = client.get("/api/v1/projects")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "AUTH_NOT_CONFIGURED"
    load_projects.assert_not_called()


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/projects",
        "/api/v1/projects/project-1/materials",
        "/api/v1/projects/project-1/submissions",
        "/api/v1/submissions/submission-1/reports/latest",
        "/api/v1/projects/project-1/submissions/submission-1/evidence_trace",
        "/api/v1/projects/project-1/submissions/submission-1/scoring_basis",
    ],
)
def test_affected_sensitive_read_interfaces_fail_closed_before_business_logic(path):
    client = TestClient(app)
    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop(API_KEYS_ENV, None)
        response = client.get(path)

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "AUTH_NOT_CONFIGURED"


def test_sensitive_read_rejects_missing_wrong_and_query_keys_without_leaking_them():
    client = TestClient(app)
    configured_key = "configured-secret-key"
    wrong_key = "wrong-secret-key"
    with patch.dict(os.environ, {API_KEYS_ENV: configured_key}):
        missing = client.get("/api/v1/projects")
        wrong = client.get(
            "/api/v1/projects",
            headers={"X-API-Key": wrong_key},
        )
        query_only = client.get(f"/api/v1/projects?api_key={configured_key}")

    assert missing.status_code == 401
    assert missing.json()["detail"]["code"] == "AUTH_KEY_MISSING"
    assert wrong.status_code == 401
    assert wrong.json()["detail"]["code"] == "AUTH_KEY_INVALID"
    assert query_only.status_code == 401
    assert query_only.json()["detail"]["code"] == "AUTH_KEY_MISSING"
    combined_errors = missing.text + wrong.text + query_only.text
    assert configured_key not in combined_errors
    assert wrong_key not in combined_errors


def test_correct_header_enters_original_sensitive_read_logic():
    client = TestClient(app)
    with (
        patch.dict(os.environ, {API_KEYS_ENV: TEST_API_KEY}),
        patch("app.main.load_projects", return_value=[]) as load_projects,
    ):
        response = client.get(
            "/api/v1/projects",
            headers=AUTH_HEADERS,
        )

    assert response.status_code == 200
    assert response.json() == []
    load_projects.assert_called_once_with()


def test_public_root_does_not_load_or_embed_business_data():
    client = TestClient(app)
    with (
        patch.dict(os.environ, {}, clear=True),
        patch("app.main.ensure_data_dirs") as ensure_data_dirs,
        patch("app.main.load_projects") as load_projects,
        patch("app.main.load_materials") as load_materials,
        patch("app.main.load_submissions") as load_submissions,
        patch("app.main.load_expert_profiles") as load_expert_profiles,
    ):
        response = client.get(
            "/?created=1&create_ok=secret-project-name&project_id=secret-project-id"
        )

    assert response.status_code == 200
    assert "__qingtianApiKeyFetchInstalled" in response.text
    assert "X-API-Key" in response.text
    assert "?api_key" not in response.text
    assert "项目已创建，请使用 API key 刷新项目列表。" in response.text
    assert "secret-project-name" not in response.text
    assert "secret-project-id" not in response.text
    ensure_data_dirs.assert_not_called()
    load_projects.assert_not_called()
    load_materials.assert_not_called()
    load_submissions.assert_not_called()
    load_expert_profiles.assert_not_called()
