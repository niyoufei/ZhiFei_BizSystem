"""Tests for app/auth.py API Key authentication module."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.auth import (
    API_KEYS_ENV,
    DEFAULT_API_KEY_ROLE,
    get_auth_status,
    get_valid_api_keys,
    is_auth_enabled,
    is_trusted_local_request,
    resolve_api_key_for_role,
    verify_api_key,
    verify_explicit_api_key,
    verify_ops_api_key,
)


def _build_request(host: str = "testserver", *, path: str = "/") -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "headers": [(b"host", host.encode("utf-8"))],
        "client": (host.split(":", 1)[0], 12345),
        "server": ("127.0.0.1", 8000),
    }
    return Request(scope)


class TestGetValidApiKeys:
    """Tests for get_valid_api_keys function."""

    def test_no_env_var_returns_empty(self):
        """Should return empty list when env var is not set."""
        with patch.dict(os.environ, {}, clear=True):
            # Remove the env var if it exists
            os.environ.pop(API_KEYS_ENV, None)
            result = get_valid_api_keys()
            assert result == []

    def test_empty_env_var_returns_empty(self):
        """Should return empty list when env var is empty."""
        with patch.dict(os.environ, {API_KEYS_ENV: ""}):
            result = get_valid_api_keys()
            assert result == []

    def test_whitespace_only_returns_empty(self):
        """Should return empty list when env var contains only whitespace."""
        with patch.dict(os.environ, {API_KEYS_ENV: "   "}):
            result = get_valid_api_keys()
            assert result == []

    def test_single_key(self):
        """Should return list with single key."""
        with patch.dict(os.environ, {API_KEYS_ENV: "test-key-123"}):
            result = get_valid_api_keys()
            assert result == ["test-key-123"]

    def test_multiple_keys(self):
        """Should return list with multiple keys."""
        with patch.dict(os.environ, {API_KEYS_ENV: "key1,key2,key3"}):
            result = get_valid_api_keys()
            assert result == ["key1", "key2", "key3"]

    def test_keys_with_whitespace_trimmed(self):
        """Should trim whitespace from keys."""
        with patch.dict(os.environ, {API_KEYS_ENV: " key1 , key2 , key3 "}):
            result = get_valid_api_keys()
            assert result == ["key1", "key2", "key3"]

    def test_empty_keys_filtered(self):
        """Should filter out empty keys."""
        with patch.dict(os.environ, {API_KEYS_ENV: "key1,,key2,  ,key3"}):
            result = get_valid_api_keys()
            assert result == ["key1", "key2", "key3"]

    def test_role_prefixed_keys_are_supported(self):
        """Should accept role:key syntax and expose raw keys only."""
        with patch.dict(os.environ, {API_KEYS_ENV: "admin:key1,ops:key2,readonly:key3"}):
            result = get_valid_api_keys()
            assert result == ["key1", "key2", "key3"]


class TestIsAuthEnabled:
    """Tests for is_auth_enabled function."""

    def test_disabled_when_no_keys(self):
        """Should return False when no API keys configured."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop(API_KEYS_ENV, None)
            assert is_auth_enabled() is False

    def test_enabled_when_keys_configured(self):
        """Should return True when API keys are configured."""
        with patch.dict(os.environ, {API_KEYS_ENV: "test-key"}):
            assert is_auth_enabled() is True


class TestResolveApiKeyForRole:
    def test_returns_none_when_no_keys(self):
        assert resolve_api_key_for_role("ops", api_keys_value="") is None

    def test_prefers_explicit_role(self):
        assert (
            resolve_api_key_for_role(
                "ops",
                api_keys_value="admin:admin-key,ops:ops-key,readonly:ro-key",
                fallback_roles=("admin",),
            )
            == "ops-key"
        )

    def test_falls_back_to_admin_when_ops_missing(self):
        assert (
            resolve_api_key_for_role(
                "ops",
                api_keys_value="admin:admin-key,readonly:ro-key",
                fallback_roles=("admin",),
            )
            == "admin-key"
        )

    def test_legacy_format_defaults_to_admin(self):
        assert (
            resolve_api_key_for_role(
                DEFAULT_API_KEY_ROLE,
                api_keys_value="legacy-key-1,legacy-key-2",
            )
            == "legacy-key-1"
        )

    def test_returns_first_key_when_requested_role_missing_and_no_fallback(self):
        assert (
            resolve_api_key_for_role(
                "readonly",
                api_keys_value="admin:admin-key,ops:ops-key",
            )
            == "admin-key"
        )


class TestVerifyApiKey:
    """Tests for verify_api_key function."""

    def test_no_auth_required_when_disabled(self):
        """Should return None (pass) when auth is disabled."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop(API_KEYS_ENV, None)
            result = verify_api_key(
                request=_build_request(),
                api_key_header=None,
                api_key_query=None,
            )
            assert result is None

    def test_header_key_accepted(self):
        """Should accept valid API key from header."""
        with patch.dict(os.environ, {API_KEYS_ENV: "valid-key"}):
            result = verify_api_key(
                request=_build_request(),
                api_key_header="valid-key",
                api_key_query=None,
            )
            assert result == "valid-key"

    def test_query_key_accepted(self):
        """Should accept valid API key from query param."""
        with patch.dict(os.environ, {API_KEYS_ENV: "valid-key"}):
            result = verify_api_key(
                request=_build_request(),
                api_key_header=None,
                api_key_query="valid-key",
            )
            assert result == "valid-key"

    def test_header_takes_precedence(self):
        """Should use header key when both header and query provided."""
        with patch.dict(os.environ, {API_KEYS_ENV: "header-key,query-key"}):
            result = verify_api_key(
                request=_build_request(),
                api_key_header="header-key",
                api_key_query="query-key",
            )
            assert result == "header-key"

    def test_missing_key_raises_401(self):
        """Should raise 401 when key is missing and auth enabled."""
        with patch.dict(os.environ, {API_KEYS_ENV: "valid-key"}):
            with pytest.raises(HTTPException) as exc_info:
                verify_api_key(request=_build_request(), api_key_header=None, api_key_query=None)
            assert exc_info.value.status_code == 401
            assert "缺少 API Key" in exc_info.value.detail

    def test_invalid_key_raises_401(self):
        """Should raise 401 when key is invalid."""
        with patch.dict(os.environ, {API_KEYS_ENV: "valid-key"}):
            with pytest.raises(HTTPException) as exc_info:
                verify_api_key(
                    request=_build_request(),
                    api_key_header="invalid-key",
                    api_key_query=None,
                )
            assert exc_info.value.status_code == 401
            assert "无效的 API Key" in exc_info.value.detail

    def test_any_valid_key_accepted(self):
        """Should accept any key from the configured list."""
        with patch.dict(os.environ, {API_KEYS_ENV: "key1,key2,key3"}):
            assert (
                verify_api_key(request=_build_request(), api_key_header="key1", api_key_query=None)
                == "key1"
            )
            assert (
                verify_api_key(request=_build_request(), api_key_header="key2", api_key_query=None)
                == "key2"
            )
            assert (
                verify_api_key(request=_build_request(), api_key_header="key3", api_key_query=None)
                == "key3"
            )

    def test_admin_dependency_rejects_ops_role(self):
        """默认受保护接口只允许 admin key。"""
        with patch.dict(os.environ, {API_KEYS_ENV: "admin:admin-key,ops:ops-key"}):
            with pytest.raises(HTTPException) as exc_info:
                verify_api_key(
                    request=_build_request(),
                    api_key_header="ops-key",
                    api_key_query=None,
                )
            assert exc_info.value.status_code == 403
            assert "admin" in exc_info.value.detail

    def test_localhost_request_bypasses_auth_when_non_production(self):
        with patch.dict(os.environ, {API_KEYS_ENV: "admin:admin-key"}, clear=False):
            result = verify_api_key(
                request=_build_request("127.0.0.1:8000"),
                api_key_header=None,
                api_key_query=None,
            )
            assert result is None

    def test_localhost_bypass_disabled_in_production_mode(self):
        with patch.dict(
            os.environ,
            {API_KEYS_ENV: "admin:admin-key", "ZHIFEI_PRODUCTION_MODE": "1"},
            clear=False,
        ):
            with pytest.raises(HTTPException) as exc_info:
                verify_api_key(
                    request=_build_request("127.0.0.1:8000"),
                    api_key_header=None,
                    api_key_query=None,
                )
            assert exc_info.value.status_code == 401

    def test_ops_dependency_accepts_ops_role(self):
        """运维接口允许 ops key。"""
        with patch.dict(os.environ, {API_KEYS_ENV: "admin:admin-key,ops:ops-key"}):
            assert (
                verify_ops_api_key(
                    request=_build_request(),
                    api_key_header="ops-key",
                    api_key_query=None,
                )
                == "ops-key"
            )
            assert (
                verify_ops_api_key(
                    request=_build_request(),
                    api_key_header="admin-key",
                    api_key_query=None,
                )
                == "admin-key"
            )

    def test_verify_explicit_api_key_accepts_valid_admin_key(self):
        with patch.dict(os.environ, {API_KEYS_ENV: "admin:admin-key,ops:ops-key"}):
            assert verify_explicit_api_key("admin-key") == "admin-key"

    def test_verify_explicit_api_key_rejects_missing_key(self):
        with patch.dict(os.environ, {API_KEYS_ENV: "admin:admin-key"}):
            with pytest.raises(HTTPException) as exc_info:
                verify_explicit_api_key("")
            assert exc_info.value.status_code == 401
            assert "请先填写并保存 API Key" in exc_info.value.detail

    def test_verify_explicit_api_key_rejects_insufficient_role(self):
        with patch.dict(os.environ, {API_KEYS_ENV: "admin:admin-key,ops:ops-key"}):
            with pytest.raises(HTTPException) as exc_info:
                verify_explicit_api_key("ops-key", required_roles=(DEFAULT_API_KEY_ROLE,))
            assert exc_info.value.status_code == 403
            assert "admin" in exc_info.value.detail


class TestGetAuthStatus:
    """Tests for get_auth_status function."""

    def test_status_when_disabled(self):
        """Should return correct status when auth disabled."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop(API_KEYS_ENV, None)
            result = get_auth_status()
            assert result["auth_enabled"] is False
            assert result["configured_keys_count"] == 0
            assert result["auth_methods"] == []

    def test_status_when_enabled(self):
        """Should return correct status when auth enabled."""
        with patch.dict(os.environ, {API_KEYS_ENV: "key1,key2"}):
            result = get_auth_status()
            assert result["auth_enabled"] is True
            assert result["configured_keys_count"] == 2
            assert len(result["auth_methods"]) == 2
            assert "X-API-Key header" in result["auth_methods"]
            assert "api_key query param" in result["auth_methods"]

    def test_status_reports_roles_when_enabled(self):
        with patch.dict(os.environ, {API_KEYS_ENV: "admin:key1,ops:key2,readonly:key3"}):
            result = get_auth_status()
            assert result["role_mode_enabled"] is True
            assert result["default_role"] == "admin"
            assert result["configured_roles"] == ["admin", "ops", "readonly"]
            assert result["role_key_counts"] == {"admin": 1, "ops": 1, "readonly": 1}

    def test_status_reports_localhost_bypass_for_local_request(self):
        with patch.dict(os.environ, {API_KEYS_ENV: "admin:key1"}, clear=False):
            result = get_auth_status(request=_build_request("127.0.0.1:8000"))
            assert result["auth_enabled"] is True
            assert result["trusted_local_bypass_active"] is True
            assert result["ui_auth_required"] is False
            assert result["write_auth_required"] is False


class TestTrustedLocalRequest:
    def test_localhost_is_trusted_when_not_in_production(self):
        with patch.dict(os.environ, {}, clear=True):
            assert is_trusted_local_request(_build_request("localhost:8000")) is True
            assert is_trusted_local_request(_build_request("127.0.0.1:8000")) is True

    def test_testclient_host_is_not_treated_as_trusted_local(self):
        with patch.dict(os.environ, {}, clear=True):
            assert is_trusted_local_request(_build_request("testserver")) is False


class TestIntegrationWithFastAPI:
    """Integration tests with FastAPI TestClient."""

    def test_auth_status_endpoint(self):
        """Should be able to check auth status via endpoint."""
        from fastapi.testclient import TestClient

        from app.main import app

        client = TestClient(app)
        response = client.get("/api/v1/auth/status")
        assert response.status_code == 200
        data = response.json()
        assert "auth_enabled" in data
        assert "configured_keys_count" in data

    def test_auth_status_endpoint_reports_localhost_bypass(self):
        from fastapi.testclient import TestClient

        from app.main import app

        with patch.dict(os.environ, {API_KEYS_ENV: "admin:admin-key"}, clear=False):
            client = TestClient(app)
            response = client.get("/api/v1/auth/status", headers={"host": "127.0.0.1:8000"})
        assert response.status_code == 200
        data = response.json()
        assert data["auth_enabled"] is True
        assert data["trusted_local_bypass_active"] is True
        assert data["ui_auth_required"] is False

    def test_auth_verify_endpoint_accepts_admin_key(self):
        from fastapi.testclient import TestClient

        from app.main import app

        with patch.dict(os.environ, {API_KEYS_ENV: "admin:admin-key,ops:ops-key"}):
            client = TestClient(app)
            response = client.get("/api/v1/auth/verify", headers={"X-API-Key": "admin-key"})
        assert response.status_code == 200
        assert response.json()["ok"] is True

    def test_auth_verify_endpoint_rejects_ops_key(self):
        from fastapi.testclient import TestClient

        from app.main import app

        with patch.dict(os.environ, {API_KEYS_ENV: "admin:admin-key,ops:ops-key"}):
            client = TestClient(app)
            response = client.get("/api/v1/auth/verify", headers={"X-API-Key": "ops-key"})
        assert response.status_code == 403

    def test_protected_endpoint_without_auth(self):
        """Protected endpoint should work without key when auth disabled."""
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        from app.main import app

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop(API_KEYS_ENV, None)
            client = TestClient(app)

            # Mock the score_text to avoid needing real config
            with patch("app.main.load_config") as mock_config, patch(
                "app.main.score_text"
            ) as mock_score:
                from app.schemas import LogicLockResult, ScoreReport

                mock_config.return_value = MagicMock(rubric={}, lexicon={})
                mock_score.return_value = ScoreReport(
                    total_score=85.0,
                    dimension_scores={},
                    logic_lock=LogicLockResult(
                        definition_score=1.0,
                        analysis_score=1.0,
                        solution_score=1.0,
                        breaks=[],
                        evidence=[],
                    ),
                    penalties=[],
                    suggestions=[],
                    meta={},
                    judge_mode="local",
                    judge_source="scorer",
                    fallback_reason="",
                )
                response = client.post("/api/v1/score", json={"text": "测试文本"})
                assert response.status_code == 200

    def test_protected_endpoint_with_valid_header_key(self):
        """Protected endpoint should work with valid header key."""
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        from app.main import app

        with patch.dict(os.environ, {API_KEYS_ENV: "test-api-key"}):
            client = TestClient(app)

            with patch("app.main.load_config") as mock_config, patch(
                "app.main.score_text"
            ) as mock_score:
                from app.schemas import LogicLockResult, ScoreReport

                mock_config.return_value = MagicMock(rubric={}, lexicon={})
                mock_score.return_value = ScoreReport(
                    total_score=85.0,
                    dimension_scores={},
                    logic_lock=LogicLockResult(
                        definition_score=1.0,
                        analysis_score=1.0,
                        solution_score=1.0,
                        breaks=[],
                        evidence=[],
                    ),
                    penalties=[],
                    suggestions=[],
                    meta={},
                    judge_mode="local",
                    judge_source="scorer",
                    fallback_reason="",
                )
                response = client.post(
                    "/api/v1/score",
                    json={"text": "测试文本"},
                    headers={"X-API-Key": "test-api-key"},
                )
                assert response.status_code == 200

    def test_protected_endpoint_with_valid_query_key(self):
        """Protected endpoint should work with valid query key."""
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        from app.main import app

        with patch.dict(os.environ, {API_KEYS_ENV: "test-api-key"}):
            client = TestClient(app)

            with patch("app.main.load_config") as mock_config, patch(
                "app.main.score_text"
            ) as mock_score:
                from app.schemas import LogicLockResult, ScoreReport

                mock_config.return_value = MagicMock(rubric={}, lexicon={})
                mock_score.return_value = ScoreReport(
                    total_score=85.0,
                    dimension_scores={},
                    logic_lock=LogicLockResult(
                        definition_score=1.0,
                        analysis_score=1.0,
                        solution_score=1.0,
                        breaks=[],
                        evidence=[],
                    ),
                    penalties=[],
                    suggestions=[],
                    meta={},
                    judge_mode="local",
                    judge_source="scorer",
                    fallback_reason="",
                )
                response = client.post(
                    "/api/v1/score?api_key=test-api-key",
                    json={"text": "测试文本"},
                )
                assert response.status_code == 200

    def test_protected_endpoint_rejects_missing_key(self):
        """Protected endpoint should reject request without key when auth enabled."""
        from fastapi.testclient import TestClient

        from app.main import app

        with patch.dict(os.environ, {API_KEYS_ENV: "test-api-key"}):
            client = TestClient(app)
            response = client.post("/api/v1/score", json={"text": "测试文本"})
            assert response.status_code == 401
            assert "缺少 API Key" in response.json()["detail"]

    def test_protected_endpoint_rejects_invalid_key(self):
        """Protected endpoint should reject request with invalid key."""
        from fastapi.testclient import TestClient

        from app.main import app

        with patch.dict(os.environ, {API_KEYS_ENV: "valid-key"}):
            client = TestClient(app)
            response = client.post(
                "/api/v1/score",
                json={"text": "测试文本"},
                headers={"X-API-Key": "invalid-key"},
            )
            assert response.status_code == 401
            assert "无效的 API Key" in response.json()["detail"]

    def test_ops_key_cannot_call_admin_only_score_endpoint(self):
        from fastapi.testclient import TestClient

        from app.main import app

        with patch.dict(os.environ, {API_KEYS_ENV: "admin:admin-key,ops:ops-key"}):
            client = TestClient(app)
            response = client.post(
                "/api/v1/score",
                json={"text": "测试文本"},
                headers={"X-API-Key": "ops-key"},
            )
            assert response.status_code == 403
            assert "admin" in response.json()["detail"]

    def test_ops_key_can_call_ops_write_endpoint(self):
        from fastapi.testclient import TestClient

        from app.main import app

        with patch.dict(os.environ, {API_KEYS_ENV: "admin:admin-key,ops:ops-key"}):
            client = TestClient(app)
            with patch("app.main._build_data_hygiene_report") as mock_report:
                mock_report.return_value = {
                    "generated_at": "2026-03-16T00:00:00Z",
                    "apply_mode": True,
                    "valid_project_count": 1,
                    "orphan_records_total": 0,
                    "cleaned_records_total": 0,
                    "datasets": [],
                    "recommendations": [],
                }
                response = client.post(
                    "/api/v1/system/data_hygiene/repair",
                    headers={"X-API-Key": "ops-key"},
                )
            assert response.status_code == 200
            assert response.json()["apply_mode"] is True
