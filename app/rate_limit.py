"""Rate limiting module for API protection.

Provides configurable rate limiting using slowapi + in-memory middleware guard.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

_RATE_WINDOW_STATE: dict[str, deque[float]] = {}
_RATE_WINDOW_LOCK = threading.Lock()


def get_rate_limit_key(request: Request) -> str:
    """Get rate limit key based on API key or IP address."""
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return f"apikey:{api_key}"
    api_key = request.query_params.get("api_key")
    if api_key:
        return f"apikey:{api_key}"
    return get_remote_address(request)


def get_rate_limits() -> dict[str, str | bool]:
    """Get rate limit configuration from environment."""
    return {
        "default": os.getenv("RATE_LIMIT_DEFAULT", "100/minute"),
        "score": os.getenv("RATE_LIMIT_SCORE", "30/minute"),
        "upload": os.getenv("RATE_LIMIT_UPLOAD", "20/minute"),
        "enabled": os.getenv("RATE_LIMIT_ENABLED", "true").lower() in {"true", "1", "yes", "on"},
    }


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> Response:
    """Custom handler for rate limit exceeded errors."""
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
    """Create and configure the slowapi limiter."""
    config = get_rate_limits()
    if not config["enabled"]:
        return Limiter(key_func=lambda _: "disabled", enabled=False)
    return Limiter(key_func=get_rate_limit_key)


def _parse_rate_limit(
    limit_text: str, *, fallback_limit: int, fallback_window: int
) -> tuple[int, int]:
    text = str(limit_text or "").strip().lower()
    if "/" not in text:
        return fallback_limit, fallback_window
    raw_count, raw_window = text.split("/", 1)
    try:
        limit = max(1, int(raw_count.strip()))
    except Exception:
        return fallback_limit, fallback_window
    window_token = raw_window.strip()
    mapping = {
        "s": 1,
        "sec": 1,
        "second": 1,
        "seconds": 1,
        "m": 60,
        "min": 60,
        "minute": 60,
        "minutes": 60,
        "h": 3600,
        "hour": 3600,
        "hours": 3600,
        "d": 86400,
        "day": 86400,
        "days": 86400,
    }
    window_seconds = mapping.get(window_token)
    if window_seconds is None:
        return fallback_limit, fallback_window
    return limit, window_seconds


def _pick_limit_category(path: str, method: str) -> str:
    lower_path = str(path or "").lower()
    method_upper = str(method or "GET").upper()
    if method_upper == "OPTIONS":
        return "skip"
    if (
        lower_path.startswith("/docs")
        or lower_path.startswith("/openapi")
        or lower_path.startswith("/redoc")
        or lower_path.startswith("/favicon")
        or lower_path in {"/health", "/ready", "/metrics"}
    ):
        return "skip"
    if method_upper in {"POST", "PUT", "PATCH"} and (
        "/materials" in lower_path
        or "/shigong" in lower_path
        or "/upload_" in lower_path
        or "/ground_truth" in lower_path
    ):
        return "upload"
    if any(
        token in lower_path
        for token in (
            "/score",
            "/rescore",
            "/compare",
            "/insights",
            "/learning",
            "/adaptive",
            "/evolve",
            "/reflection",
            "/calibration",
        )
    ):
        return "score"
    return "default"


def _should_bypass_for_local_key(rate_key: str) -> bool:
    """Bypass local loopback/test clients to avoid throttling dev and test flows."""
    key = str(rate_key or "").strip().lower()
    if key in {"127.0.0.1", "::1", "localhost", "testclient"}:
        return True
    if key.startswith("127."):
        return True
    return False


def _check_in_memory_rate_limit(request: Request) -> tuple[bool, int, str]:
    cfg = get_rate_limits()
    if not bool(cfg.get("enabled")):
        return True, 0, "disabled"
    category = _pick_limit_category(request.url.path, request.method)
    if category == "skip":
        return True, 0, "skip"

    rate_key = get_rate_limit_key(request)
    if _should_bypass_for_local_key(rate_key):
        return True, 0, "local-bypass"

    limit_text = str(cfg.get(category) or cfg.get("default") or "100/minute")
    limit_count, window_seconds = _parse_rate_limit(
        limit_text, fallback_limit=100, fallback_window=60
    )
    key = f"{category}:{rate_key}"
    now = time.time()
    with _RATE_WINDOW_LOCK:
        q = _RATE_WINDOW_STATE.get(key)
        if q is None:
            q = deque()
            _RATE_WINDOW_STATE[key] = q
        threshold = now - float(window_seconds)
        while q and q[0] < threshold:
            q.popleft()
        if len(q) >= limit_count:
            retry_after = max(1, int((q[0] + float(window_seconds)) - now))
            return False, retry_after, category
        q.append(now)
    return True, 0, category


limiter = create_limiter()


def setup_rate_limiting(app: FastAPI) -> None:
    """Setup rate limiting for a FastAPI application."""
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

    @app.middleware("http")
    async def _rate_limit_guard(request: Request, call_next):
        allowed, retry_after, category = _check_in_memory_rate_limit(request)
        if not allowed:
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": str(retry_after)},
                content={
                    "detail": "请求过于频繁，请稍后再试",
                    "error": "rate_limit_exceeded",
                    "retry_after": retry_after,
                    "category": category,
                },
            )
        return await call_next(request)


def get_limiter() -> Limiter:
    """Get the global limiter instance."""
    return limiter


def get_rate_limit_status() -> dict:
    """Get current rate limiting status."""
    cfg = get_rate_limits()
    return {
        "enabled": cfg["enabled"],
        "middleware_guard": True,
        "limits": {
            "default": str(cfg["default"]),
            "score": str(cfg["score"]),
            "upload": str(cfg["upload"]),
        },
    }
