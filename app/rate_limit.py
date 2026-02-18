"""Rate limiting module for API protection.

Provides configurable rate limiting using slowapi.
Rate limiting is applied via decorators on specific endpoints.
File upload endpoints are exempt due to annotation compatibility issues.
"""

from __future__ import annotations

import os

from fastapi import FastAPI, Request, Response
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address


def get_rate_limit_key(request: Request) -> str:
    """Get rate limit key based on API key or IP address.

    Priority:
    1. X-API-Key header
    2. api_key query parameter
    3. Remote IP address
    """
    # Check for API key in header
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return f"apikey:{api_key}"

    # Check for API key in query
    api_key = request.query_params.get("api_key")
    if api_key:
        return f"apikey:{api_key}"

    # Fall back to IP address
    return get_remote_address(request)


def get_rate_limits() -> dict[str, str | bool]:
    """Get rate limit configuration from environment.

    Environment variables:
    - RATE_LIMIT_DEFAULT: Default rate limit (default: "100/minute")
    - RATE_LIMIT_SCORE: Rate limit for /score endpoint (default: "30/minute")
    - RATE_LIMIT_UPLOAD: Rate limit for upload endpoints (default: "20/minute")
    - RATE_LIMIT_ENABLED: Enable/disable rate limiting (default: "true")

    Returns:
        Dictionary with rate limit settings.
    """
    return {
        "default": os.getenv("RATE_LIMIT_DEFAULT", "100/minute"),
        "score": os.getenv("RATE_LIMIT_SCORE", "30/minute"),
        "upload": os.getenv("RATE_LIMIT_UPLOAD", "20/minute"),
        "enabled": os.getenv("RATE_LIMIT_ENABLED", "true").lower() == "true",
    }


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> Response:
    """Custom handler for rate limit exceeded errors."""
    from fastapi.responses import JSONResponse

    # Extract retry-after info from the exception
    retry_info = str(exc.detail) if hasattr(exc, "detail") else "unknown"

    return JSONResponse(
        status_code=429,
        content={
            "detail": "请求过于频繁，请稍后再试",
            "error": "rate_limit_exceeded",
            "retry_after": retry_info,
        },
    )


def create_limiter() -> Limiter:
    """Create and configure the rate limiter."""
    config = get_rate_limits()
    if not config["enabled"]:
        # Return a limiter with no-op key function when disabled
        return Limiter(key_func=lambda _: "disabled", enabled=False)
    return Limiter(key_func=get_rate_limit_key)


# Global limiter instance
limiter = create_limiter()


def setup_rate_limiting(app: FastAPI) -> None:
    """Setup rate limiting for a FastAPI application.

    Args:
        app: FastAPI application instance.
    """
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)


def get_limiter() -> Limiter:
    """Get the global limiter instance."""
    return limiter


def get_rate_limit_status() -> dict:
    """Get current rate limiting status.

    Returns:
        Dictionary with rate limiting configuration and status.
    """
    config = get_rate_limits()
    return {
        "enabled": config["enabled"],
        "limits": {
            "default": str(config["default"]),
            "score": str(config["score"]),
            "upload": str(config["upload"]),
        },
    }
