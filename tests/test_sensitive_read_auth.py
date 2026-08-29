"""Fail-closed authentication gates for all business HTTP methods."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.auth import API_KEYS_ENV, verify_api_key, verify_metrics_api_key

TEST_API_KEY = "test-auth-key-do-not-use"
AUTH_HEADERS = {"X-API-Key": TEST_API_KEY}
_ORIGINAL_API_KEYS = os.environ.get(API_KEYS_ENV)
os.environ[API_KEYS_ENV] = TEST_API_KEY
try:
    from app.main import AuthenticatedAPIRouter, app
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


BUSINESS_HTTP_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"})

PUBLIC_ROUTE_METHODS = frozenset(
    {
        ("GET", "/"),
        ("HEAD", "/"),
        ("GET", "/health"),
        ("GET", "/ready"),
        ("GET", "/favicon.ico"),
        ("GET", "/apple-touch-icon-precomposed.png"),
        ("GET", "/apple-touch-icon.png"),
        *{
            (method, path)
            for path in (
                "/web/upload_materials",
                "/web/upload_shigong",
                "/web/score_shigong",
            )
            for method in ("GET", "HEAD", "PUT", "PATCH", "DELETE")
        },
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


def _business_routes() -> list[APIRoute]:
    return [
        route
        for route in app.routes
        if isinstance(route, APIRoute)
        and BUSINESS_HTTP_METHODS.intersection(route.methods or set())
    ]


def _has_required_auth(route: APIRoute) -> bool:
    calls = _dependency_calls(route)
    if route.path == "/metrics":
        return verify_metrics_api_key in calls
    return verify_api_key in calls


def test_authenticated_router_covers_every_business_http_method():
    test_router = AuthenticatedAPIRouter()

    def endpoint():
        return None

    for method in BUSINESS_HTTP_METHODS:
        getattr(test_router, method.lower())(f"/{method.lower()}")(endpoint)

    route_methods = {
        method
        for route in test_router.routes
        if isinstance(route, APIRoute)
        for method in (route.methods or set()) & BUSINESS_HTTP_METHODS
        if verify_api_key in _dependency_calls(route)
    }
    assert route_methods == BUSINESS_HTTP_METHODS


def test_every_non_public_business_route_requires_api_key():
    routes = _business_routes()
    observed_methods = {
        method for route in routes for method in (route.methods or set()) & BUSINESS_HTTP_METHODS
    }
    assert observed_methods == BUSINESS_HTTP_METHODS

    protected_route_methods = {
        (method, route.path)
        for route in routes
        for method in (route.methods or set()) & BUSINESS_HTTP_METHODS
        if (method, route.path) not in PUBLIC_ROUTE_METHODS
    }
    assert {
        ("GET", "/api/v1/projects"),
        ("POST", "/api/v1/per-tender/analyze"),
        ("POST", "/api/v1/tools/parse_text"),
        ("POST", "/local-llm/preview-mock"),
        ("POST", "/local-llm/zdoc-preview-only/receive"),
        ("PUT", "/api/v1/projects/{project_id}/expert-profile"),
        ("DELETE", "/api/v1/projects/{project_id}"),
    }.issubset(protected_route_methods)

    missing_auth = sorted(
        f"{method} {route.path}"
        for route in routes
        for method in (route.methods or set()) & BUSINESS_HTTP_METHODS
        if (method, route.path) not in PUBLIC_ROUTE_METHODS
        if not _has_required_auth(route)
    )
    assert missing_auth == []


def test_public_route_method_allowlist_has_no_api_key_dependency():
    unexpected_auth = sorted(
        f"{method} {route.path}"
        for route in _business_routes()
        for method in (route.methods or set()) & BUSINESS_HTTP_METHODS
        if (method, route.path) in PUBLIC_ROUTE_METHODS
        if verify_api_key in _dependency_calls(route)
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


@pytest.mark.parametrize(
    ("path", "request_kwargs", "business_parser"),
    [
        (
            "/api/v1/per-tender/analyze",
            {"json": {"profile": {"tender_id": "secret-project-id"}}},
            "app.main._parse_inline_tender_profile",
        ),
        (
            "/api/v1/tools/parse_text",
            {
                "files": [
                    (
                        "file",
                        ("secret-project-name.txt", b"secret-project-id", "text/plain"),
                    )
                ]
            },
            "app.main._read_uploaded_file_content",
        ),
    ],
)
@pytest.mark.parametrize(
    ("configured_key", "headers", "query_suffix", "status_code", "error_code"),
    [
        (None, {}, "", 503, "AUTH_NOT_CONFIGURED"),
        ("configured-secret-key", {}, "", 401, "AUTH_KEY_MISSING"),
        (
            "configured-secret-key",
            {"X-API-Key": "wrong-secret-key"},
            "",
            401,
            "AUTH_KEY_INVALID",
        ),
        (
            "configured-secret-key",
            {},
            "?api_key=configured-secret-key",
            401,
            "AUTH_KEY_MISSING",
        ),
    ],
)
def test_sensitive_posts_reject_before_body_validation_or_business_parsing(
    path,
    request_kwargs,
    business_parser,
    configured_key,
    headers,
    query_suffix,
    status_code,
    error_code,
):
    client = TestClient(app)
    environment = {} if configured_key is None else {API_KEYS_ENV: configured_key}
    with (
        patch.dict(os.environ, environment, clear=True),
        patch(business_parser) as parser,
    ):
        response = client.post(
            path + query_suffix,
            headers=headers,
            **request_kwargs,
        )

    assert response.status_code == status_code
    assert response.json()["detail"]["code"] == error_code
    assert "secret-project-id" not in response.text
    assert "secret-project-name" not in response.text
    parser.assert_not_called()


@pytest.mark.parametrize(
    ("path", "content_type", "body"),
    [
        ("/api/v1/per-tender/analyze", "application/json", b"{secret-project-id"),
        (
            "/api/v1/tools/parse_text",
            "multipart/form-data; boundary=broken",
            b"--broken\r\nsecret-project-name",
        ),
    ],
)
def test_sensitive_posts_reject_before_malformed_body_parsing(path, content_type, body):
    client = TestClient(app)
    with patch.dict(
        os.environ,
        {API_KEYS_ENV: "configured-secret-key"},
        clear=True,
    ):
        response = client.post(
            path,
            content=body,
            headers={"Content-Type": content_type},
        )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "AUTH_KEY_MISSING"
    assert "secret-project-id" not in response.text
    assert "secret-project-name" not in response.text


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
