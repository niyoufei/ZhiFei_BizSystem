"""Characterization tests for the frozen FastAPI runtime contract."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.datastructures import DefaultPlaceholder
from fastapi.routing import APIRoute
from starlette.routing import Route


ROUTE_MANIFEST_SHA256 = "852a6fa50cc45cfeed121802b9027251947d59d7b3008ed0a4cd0da7fe89cd8c"
OPENAPI_CANONICAL_SHA256 = "7ba83f46c53faee76d2ffc94f772143a46cf5be24fb8e03ac20040dc0e67d105"
AUTH_MATRIX_SHA256 = "c65f5c1c39dc89a57144b92be5c25ce51cf08c744464e4c718ba44a4482dfc67"
FRONTEND_ADAPTER_SHA256 = "4afc2aa2d276f5e1c6b50ca3a62381a5a163b641233585f2e9cff642780569fb"

HTTP_METHOD_KEYS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


@pytest.fixture(scope="module")
def runtime_modules(tmp_path_factory: pytest.TempPathFactory):
    """Import the runtime with dotenv disabled and business data redirected."""
    data_dir = tmp_path_factory.mktemp("runtime-contract-data")
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("QINGTIAN_DATA_DIR", str(data_dir))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    monkeypatch.setattr(sys, "dont_write_bytecode", True)

    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda *args, **kwargs: False  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "dotenv", fake_dotenv)

    try:
        main_module = importlib.import_module("app.main")
        auth_module = importlib.import_module("app.auth")
        yield SimpleNamespace(main=main_module, auth=auth_module, data_dir=data_dir)
    finally:
        monkeypatch.undo()


def _requires_api_key(route: APIRoute, verify_api_key: Any) -> bool:
    pending = list(route.dependant.dependencies)
    while pending:
        dependency = pending.pop()
        if dependency.call is verify_api_key:
            return True
        pending.extend(dependency.dependencies)
    return False


def _category(path: str) -> str:
    if path.startswith("/api/v1/"):
        return "api_v1"
    if path.startswith("/api/"):
        return "api_compat"
    if path.startswith("/web/"):
        return "web"
    if path in {"/", "/health", "/ready", "/metrics"}:
        return path
    return "app_other"


def _owner(path: str) -> str:
    if path.startswith("/api/v1/"):
        return "router"
    if path.startswith("/api/"):
        return "compat_router"
    return "app"


def _stable_type(value: Any) -> str:
    if isinstance(value, DefaultPlaceholder):
        value = value.value
    if value is None:
        return "-"
    return getattr(value, "__name__", None) or re.sub(
        r"\s+", "", str(value).replace("typing.", "")
    )


def _route_records(app: FastAPI, verify_api_key: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for route in app.routes:
        if not isinstance(route, Route):
            continue
        is_api_route = isinstance(route, APIRoute)
        path = route.path
        records.append(
            {
                "methods": ",".join(sorted(route.methods or [])),
                "path": path,
                "endpoint": route.name,
                "route_class": type(route).__name__,
                "owner": _owner(path),
                "category": _category(path),
                "auth": _requires_api_key(route, verify_api_key) if is_api_route else False,
                "include_in_schema": bool(getattr(route, "include_in_schema", False)),
                "declared_status": getattr(route, "status_code", None) or 200,
                "response_model": (
                    _stable_type(getattr(route, "response_model", None))
                    if is_api_route
                    else "-"
                ),
                "response_class": (
                    _stable_type(getattr(route, "response_class", None))
                    if is_api_route
                    else "-"
                ),
                "declared_responses": (
                    ",".join(sorted(map(str, (getattr(route, "responses", {}) or {}).keys())))
                    if is_api_route
                    else "-"
                ),
            }
        )
    records.sort(key=lambda item: (item["path"], item["methods"], item["endpoint"]))
    return records


def _single_api_route(app: FastAPI, path: str, method: str) -> APIRoute:
    matches = [
        route
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.path == path
        and method in (route.methods or set())
    ]
    assert len(matches) == 1, (
        f"route count difference for {method} {path}: expected=1 actual={len(matches)}"
    )
    return matches[0]


def _operation(openapi: dict[str, Any], path: str, method: str) -> dict[str, Any]:
    paths = openapi.get("paths", {})
    assert path in paths, f"OpenAPI path missing: expected={path!r} actual_paths={len(paths)}"
    operation = paths[path].get(method.lower())
    assert operation is not None, f"OpenAPI operation missing: expected={method} {path}"
    return operation


def _multipart_object_schema(
    openapi: dict[str, Any], path: str, method: str
) -> dict[str, Any]:
    operation = _operation(openapi, path, method)
    content = operation.get("requestBody", {}).get("content", {})
    assert "multipart/form-data" in content, (
        f"media type difference for {method} {path}: "
        f"expected=multipart/form-data actual={sorted(content)}"
    )
    schema = content["multipart/form-data"].get("schema", {})
    if "$ref" in schema:
        schema_name = schema["$ref"].rsplit("/", 1)[-1]
        schema = openapi.get("components", {}).get("schemas", {}).get(schema_name, {})
    return schema


def _assert_count(label: str, actual: int, expected: int) -> None:
    assert actual == expected, (
        f"{label} count difference: expected={expected} actual={actual} "
        f"difference={actual - expected:+d}"
    )


def _assert_hash(label: str, payload: str, expected: str) -> None:
    actual = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    assert actual == expected, f"{label}: expected hash={expected} actual hash={actual}"


def test_formal_app_factory_and_route_counts(runtime_modules) -> None:
    main = runtime_modules.main
    app = main.app
    assert isinstance(app, FastAPI), f"app.main:app type difference: actual={type(app)!r}"
    assert main.create_app() is app, "create_app() must return the app.main:app singleton"

    records = _route_records(app, main.verify_api_key)
    _assert_count("HTTP Route/APIRoute object", len(records), 146)
    _assert_count("method-path binding", sum(len(route.methods or []) for route in app.routes), 165)
    _assert_count("FastAPI APIRoute", sum(isinstance(route, APIRoute) for route in app.routes), 142)
    _assert_count(
        "built-in Starlette Route",
        sum(isinstance(route, Route) and not isinstance(route, APIRoute) for route in app.routes),
        4,
    )

    owners = {
        owner: sum(record["owner"] == owner for record in records)
        for owner in {"router", "compat_router", "app"}
    }
    assert owners == {"router": 95, "compat_router": 28, "app": 23}, (
        "router classification count difference: "
        f"expected={{'router': 95, 'compat_router': 28, 'app': 23}} actual={owners}"
    )


def test_openapi_versions_and_counts(runtime_modules) -> None:
    openapi = runtime_modules.main.app.openapi()
    assert openapi.get("openapi") == "3.1.0"
    assert openapi.get("info", {}).get("version") == "1.0.0"

    paths = openapi.get("paths", {})
    operations = sum(
        method.lower() in HTTP_METHOD_KEYS
        for path_item in paths.values()
        for method in path_item
    )
    schemas = openapi.get("components", {}).get("schemas", {})
    _assert_count("OpenAPI path", len(paths), 120)
    _assert_count("OpenAPI operation", operations, 127)
    _assert_count("OpenAPI component schema", len(schemas), 97)


def test_auth_contract(runtime_modules) -> None:
    main = runtime_modules.main
    auth = runtime_modules.auth
    app = main.app
    assert auth.API_KEY_HEADER_NAME == "X-API-Key"

    openapi = app.openapi()
    api_key_scheme = openapi.get("components", {}).get("securitySchemes", {}).get(
        "APIKeyHeader", {}
    )
    assert api_key_scheme == {"type": "apiKey", "in": "header", "name": "X-API-Key"}

    api_v1_routes = [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith("/api/v1/")
    ]
    api_compat_routes = [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith("/api/")
        and not route.path.startswith("/api/v1/")
    ]
    _assert_count("protected /api/v1 route", len(api_v1_routes), 95)
    _assert_count("protected /api compatibility route", len(api_compat_routes), 28)
    assert all(_requires_api_key(route, main.verify_api_key) for route in api_v1_routes)
    assert all(_requires_api_key(route, main.verify_api_key) for route in api_compat_routes)

    for path in ("/health", "/ready"):
        route = _single_api_route(app, path, "GET")
        assert not _requires_api_key(route, main.verify_api_key), (
            f"auth difference for GET {path}: expected=no_auth actual=auth"
        )


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/projects/{project_id}/materials",
        "/api/v1/projects/{project_id}/shigong",
    ],
)
def test_required_binary_file_multipart_contract(runtime_modules, path: str) -> None:
    schema = _multipart_object_schema(runtime_modules.main.app.openapi(), path, "POST")
    required = set(schema.get("required", []))
    properties = schema.get("properties", {})
    assert "file" in required, f"multipart required field difference for POST {path}: {required}"
    assert properties.get("file", {}).get("format") == "binary", (
        f"multipart file format difference for POST {path}: "
        f"expected=binary actual={properties.get('file', {}).get('format')!r}"
    )


def test_root_and_frontend_adapter_contract(runtime_modules) -> None:
    main = runtime_modules.main
    app = main.app
    for method in ("GET", "HEAD"):
        route = _single_api_route(app, "/", method)
        assert route.include_in_schema is False, (
            f"schema visibility difference for {method} /: expected=hidden actual=schema"
        )

    records = _route_records(app, main.verify_api_key)
    web_records = [record for record in records if record["path"].startswith("/web/")]
    _assert_count("/web/* adapter route object", len(web_records), 8)
    _assert_count(
        "/web/* adapter method-path binding",
        sum(len(record["methods"].split(",")) for record in web_records),
        23,
    )
    actual = [
        (
            record["methods"],
            record["path"],
            record["endpoint"],
            "auth" if record["auth"] else "no_auth",
            "schema" if record["include_in_schema"] else "hidden",
        )
        for record in web_records
    ]
    expected = [
        ("POST", "/web/create_project", "web_create_project", "auth", "hidden"),
        ("POST", "/web/delete_project", "web_delete_project", "auth", "hidden"),
        (
            "DELETE,GET,HEAD,OPTIONS,PATCH,PUT",
            "/web/score_shigong",
            "web_score_shigong_get_fallback",
            "no_auth",
            "hidden",
        ),
        ("POST", "/web/score_shigong", "web_score_shigong", "auth", "hidden"),
        (
            "DELETE,GET,HEAD,OPTIONS,PATCH,PUT",
            "/web/upload_materials",
            "web_upload_materials_get_fallback",
            "no_auth",
            "hidden",
        ),
        ("POST", "/web/upload_materials", "web_upload_materials", "auth", "hidden"),
        (
            "DELETE,GET,HEAD,OPTIONS,PATCH,PUT",
            "/web/upload_shigong",
            "web_upload_shigong_get_fallback",
            "no_auth",
            "hidden",
        ),
        ("POST", "/web/upload_shigong", "web_upload_shigong", "auth", "hidden"),
    ]
    assert actual == expected, f"/web/* adapter state difference:\nexpected={expected}\nactual={actual}"


def test_frozen_runtime_contract_hashes(runtime_modules) -> None:
    main = runtime_modules.main
    records = _route_records(main.app, main.verify_api_key)

    route_manifest = "\n".join(
        "\t".join(
            map(
                str,
                [
                    record["methods"],
                    record["path"],
                    record["endpoint"],
                    record["route_class"],
                    record["owner"],
                    record["category"],
                    "auth" if record["auth"] else "no_auth",
                    "schema" if record["include_in_schema"] else "hidden",
                    record["declared_status"],
                    record["response_model"],
                    record["response_class"],
                    record["declared_responses"],
                ],
            )
        )
        for record in records
    ) + "\n"
    auth_matrix = "\n".join(
        "\t".join(
            [
                record["methods"],
                record["path"],
                "auth" if record["auth"] else "no_auth",
            ]
        )
        for record in records
    ) + "\n"
    frontend_records = [
        record for record in records if record["category"] in {"/", "web"}
    ]
    frontend_adapter = "\n".join(
        "\t".join(
            [
                record["methods"],
                record["path"],
                record["endpoint"],
                "auth" if record["auth"] else "no_auth",
                "schema" if record["include_in_schema"] else "hidden",
            ]
        )
        for record in frontend_records
    ) + "\n"
    openapi_canonical = json.dumps(
        main.app.openapi(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )

    _assert_hash("route_manifest_sha256", route_manifest, ROUTE_MANIFEST_SHA256)
    _assert_hash("openapi_canonical_sha256", openapi_canonical, OPENAPI_CANONICAL_SHA256)
    _assert_hash("auth_matrix_sha256", auth_matrix, AUTH_MATRIX_SHA256)
    _assert_hash("frontend_adapter_sha256", frontend_adapter, FRONTEND_ADAPTER_SHA256)
