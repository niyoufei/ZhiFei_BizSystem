"""Tests for rate limiting module."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.rate_limit import (
    create_limiter,
    get_rate_limit_key,
    get_rate_limit_status,
    get_rate_limits,
    rate_limit_exceeded_handler,
    setup_rate_limiting,
)


class TestGetRateLimitKey:
    """Tests for get_rate_limit_key function."""

    def test_key_from_header(self):
        """Should return API key from header."""
        request = MagicMock()
        request.headers = {"X-API-Key": "test-key-123"}
        request.query_params = {}

        result = get_rate_limit_key(request)
        assert result == "apikey:test-key-123"

    def test_key_from_query(self):
        """Should return API key from query parameter."""
        request = MagicMock()
        request.headers = {}
        request.query_params = {"api_key": "query-key-456"}

        result = get_rate_limit_key(request)
        assert result == "apikey:query-key-456"

    def test_key_header_priority_over_query(self):
        """Header API key should take priority over query parameter."""
        request = MagicMock()
        request.headers = {"X-API-Key": "header-key"}
        request.query_params = {"api_key": "query-key"}

        result = get_rate_limit_key(request)
        assert result == "apikey:header-key"

    def test_key_falls_back_to_ip(self):
        """Should fall back to IP when no API key provided."""
        request = MagicMock()
        request.headers = {}
        request.query_params = {}
        request.client = MagicMock()
        request.client.host = "192.168.1.100"

        with patch("app.rate_limit.get_remote_address", return_value="192.168.1.100"):
            result = get_rate_limit_key(request)
            assert result == "192.168.1.100"


class TestGetRateLimits:
    """Tests for get_rate_limits function."""

    def test_default_values(self):
        """Should return default values when no env vars set."""
        with patch.dict(os.environ, {}, clear=True):
            result = get_rate_limits()
            assert result["default"] == "100/minute"
            assert result["score"] == "30/minute"
            assert result["upload"] == "20/minute"
            assert result["enabled"] is True

    def test_custom_values(self):
        """Should use custom values from environment."""
        env_vars = {
            "RATE_LIMIT_DEFAULT": "50/minute",
            "RATE_LIMIT_SCORE": "10/minute",
            "RATE_LIMIT_UPLOAD": "5/minute",
            "RATE_LIMIT_ENABLED": "false",
        }
        with patch.dict(os.environ, env_vars, clear=True):
            result = get_rate_limits()
            assert result["default"] == "50/minute"
            assert result["score"] == "10/minute"
            assert result["upload"] == "5/minute"
            assert result["enabled"] is False

    def test_enabled_parsing(self):
        """Should parse enabled flag correctly."""
        test_cases = [
            ("true", True),
            ("True", True),
            ("TRUE", True),
            ("false", False),
            ("False", False),
            ("0", False),
            ("no", False),
        ]
        for env_value, expected in test_cases:
            with patch.dict(os.environ, {"RATE_LIMIT_ENABLED": env_value}, clear=True):
                result = get_rate_limits()
                assert result["enabled"] == expected, f"Failed for {env_value}"


class TestRateLimitExceededHandler:
    """Tests for rate_limit_exceeded_handler function."""

    def test_handler_returns_429(self):
        """Should return 429 status code."""
        request = MagicMock()

        # Create a mock exception with detail attribute
        exc = MagicMock()
        exc.detail = "1 per minute"

        response = rate_limit_exceeded_handler(request, exc)

        assert response.status_code == 429
        assert b"rate_limit_exceeded" in response.body

    def test_handler_without_detail(self):
        """Should handle exception without detail attribute."""
        request = MagicMock()

        # Create a mock exception without detail attribute
        exc = MagicMock(spec=[])

        response = rate_limit_exceeded_handler(request, exc)

        assert response.status_code == 429
        assert b"rate_limit_exceeded" in response.body


class TestCreateLimiter:
    """Tests for create_limiter function."""

    def test_creates_limiter_when_enabled(self):
        """Should create an enabled limiter."""
        with patch.dict(os.environ, {"RATE_LIMIT_ENABLED": "true"}, clear=True):
            limiter = create_limiter()
            assert limiter is not None
            assert limiter.enabled is True

    def test_creates_disabled_limiter(self):
        """Should create disabled limiter when disabled."""
        with patch.dict(os.environ, {"RATE_LIMIT_ENABLED": "false"}, clear=True):
            limiter = create_limiter()
            assert limiter is not None
            assert limiter.enabled is False


class TestSetupRateLimiting:
    """Tests for setup_rate_limiting function."""

    def test_setup_adds_limiter_to_app(self):
        """Should add limiter to app state."""
        test_app = FastAPI()
        setup_rate_limiting(test_app)

        assert hasattr(test_app.state, "limiter")
        assert test_app.state.limiter is not None


class TestGetRateLimitStatus:
    """Tests for get_rate_limit_status function."""

    def test_status_returns_config(self):
        """Should return current rate limit configuration."""
        result = get_rate_limit_status()

        assert "enabled" in result
        assert "limits" in result
        assert "default" in result["limits"]
        assert "score" in result["limits"]
        assert "upload" in result["limits"]


class TestRateLimitEndpoint:
    """Tests for rate limit status endpoint."""

    def test_rate_limit_status_endpoint(self):
        """Should return rate limit status via API."""
        from app.main import app

        client = TestClient(app)
        response = client.get("/api/v1/rate_limit/status")

        assert response.status_code == 200
        data = response.json()
        assert "enabled" in data
        assert "limits" in data


class TestRateLimitedEndpoints:
    """Integration tests for rate limiting infrastructure."""

    def test_rate_limit_infrastructure_ready(self):
        """Rate limiting infrastructure should be set up on the app."""
        from app.main import app

        # Check that limiter is attached to app state
        assert hasattr(app.state, "limiter")
        assert app.state.limiter is not None

    def test_endpoints_work_normally(self):
        """Endpoints should work normally with rate limiting infrastructure."""
        from app.main import app

        client = TestClient(app)

        # Test score endpoint
        response = client.post("/api/v1/score", json={"text": "测试文本"})
        assert response.status_code == 200

        # Test rate limit status endpoint
        response = client.get("/api/v1/rate_limit/status")
        assert response.status_code == 200
        data = response.json()
        assert "enabled" in data
        assert "limits" in data


class TestRateLimitDisabled:
    """Tests for disabled rate limiting."""

    def test_endpoints_work_when_disabled(self):
        """Endpoints should work normally when rate limiting is disabled."""
        # Save original env
        original = os.environ.get("RATE_LIMIT_ENABLED")

        try:
            os.environ["RATE_LIMIT_ENABLED"] = "false"

            # Re-import to get fresh config
            from importlib import reload

            import app.rate_limit

            reload(app.rate_limit)

            status = app.rate_limit.get_rate_limit_status()
            assert status["enabled"] is False

        finally:
            # Restore original env
            if original is not None:
                os.environ["RATE_LIMIT_ENABLED"] = original
            elif "RATE_LIMIT_ENABLED" in os.environ:
                del os.environ["RATE_LIMIT_ENABLED"]
