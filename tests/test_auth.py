"""Tests for app/auth.py API Key authentication module."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.auth import (
    API_KEYS_ENV,
    get_auth_status,
    get_valid_api_keys,
    is_auth_enabled,
    verify_api_key,
)

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
    """Provide a deterministic default while allowing explicit fail-closed cases."""
    with patch.dict(os.environ, {API_KEYS_ENV: TEST_API_KEY}, clear=False):
        yield


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


class TestVerifyApiKey:
    """Tests for verify_api_key function."""

    def test_unconfigured_auth_fails_closed(self):
        """Should return a stable 503 error when no valid key is configured."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop(API_KEYS_ENV, None)
            with pytest.raises(HTTPException) as exc_info:
                verify_api_key(api_key_header=None)
            assert exc_info.value.status_code == 503
            assert exc_info.value.detail["code"] == "AUTH_NOT_CONFIGURED"

    def test_header_key_accepted(self):
        """Should accept valid API key from header."""
        with patch.dict(os.environ, {API_KEYS_ENV: "valid-key"}):
            result = verify_api_key(api_key_header="valid-key")
            assert result == "valid-key"

    def test_missing_key_raises_401(self):
        """Should raise 401 when key is missing and auth enabled."""
        with patch.dict(os.environ, {API_KEYS_ENV: "valid-key"}):
            with pytest.raises(HTTPException) as exc_info:
                verify_api_key(api_key_header=None)
            assert exc_info.value.status_code == 401
            assert exc_info.value.detail["code"] == "AUTH_KEY_MISSING"

    def test_invalid_key_raises_401(self):
        """Should raise 401 when key is invalid."""
        with patch.dict(os.environ, {API_KEYS_ENV: "valid-key"}):
            with pytest.raises(HTTPException) as exc_info:
                verify_api_key(api_key_header="invalid-key")
            assert exc_info.value.status_code == 401
            assert exc_info.value.detail["code"] == "AUTH_KEY_INVALID"

    def test_any_valid_key_accepted(self):
        """Should accept any key from the configured list."""
        with patch.dict(os.environ, {API_KEYS_ENV: "key1,key2,key3"}):
            assert verify_api_key(api_key_header="key1") == "key1"
            assert verify_api_key(api_key_header="key2") == "key2"
            assert verify_api_key(api_key_header="key3") == "key3"


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
            assert result["auth_methods"] == ["X-API-Key header"]


class TestIntegrationWithFastAPI:
    """Integration tests with FastAPI TestClient."""

    def test_auth_status_endpoint(self):
        """Should be able to check auth status with a valid header key."""
        from fastapi.testclient import TestClient

        with patch.dict(os.environ, {API_KEYS_ENV: "status-key"}):
            client = TestClient(app)
            response = client.get(
                "/api/v1/auth/status",
                headers={"X-API-Key": "status-key"},
            )
            assert response.status_code == 200
            data = response.json()
            assert "auth_enabled" in data
            assert "configured_keys_count" in data

    def test_protected_endpoint_without_auth(self):
        """Protected endpoint should fail closed when auth is unconfigured."""
        from fastapi.testclient import TestClient

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop(API_KEYS_ENV, None)
            client = TestClient(app)
            response = client.post("/api/v1/score", json={"text": "测试文本"})
            assert response.status_code == 503
            assert response.json()["detail"]["code"] == "AUTH_NOT_CONFIGURED"

    def test_protected_endpoint_with_valid_header_key(self):
        """Protected endpoint should work with valid header key."""
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        with patch.dict(os.environ, {API_KEYS_ENV: TEST_API_KEY}):
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
                    headers=AUTH_HEADERS,
                )
                assert response.status_code == 200

    def test_protected_endpoint_rejects_valid_query_key(self):
        """A valid key in the query string must not authenticate."""
        from fastapi.testclient import TestClient

        with patch.dict(os.environ, {API_KEYS_ENV: TEST_API_KEY}):
            client = TestClient(app)
            response = client.post(
                f"/api/v1/score?api_key={TEST_API_KEY}",
                json={"text": "测试文本"},
            )
            assert response.status_code == 401
            assert response.json()["detail"]["code"] == "AUTH_KEY_MISSING"

    def test_protected_endpoint_rejects_missing_key(self):
        """Protected endpoint should reject request without key when auth enabled."""
        from fastapi.testclient import TestClient

        with patch.dict(os.environ, {API_KEYS_ENV: TEST_API_KEY}):
            client = TestClient(app)
            response = client.post("/api/v1/score", json={"text": "测试文本"})
            assert response.status_code == 401
            assert response.json()["detail"]["code"] == "AUTH_KEY_MISSING"

    def test_protected_endpoint_rejects_invalid_key(self):
        """Protected endpoint should reject request with invalid key."""
        from fastapi.testclient import TestClient

        with patch.dict(os.environ, {API_KEYS_ENV: "valid-key"}):
            client = TestClient(app)
            response = client.post(
                "/api/v1/score",
                json={"text": "测试文本"},
                headers={"X-API-Key": "invalid-key"},
            )
            assert response.status_code == 401
            assert response.json()["detail"]["code"] == "AUTH_KEY_INVALID"
