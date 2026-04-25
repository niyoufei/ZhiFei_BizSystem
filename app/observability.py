from __future__ import annotations

import logging
import os
import time
from uuid import uuid4

from fastapi import FastAPI, Request
from starlette.routing import BaseRoute

from app.application.task_runtime import log_structured, runtime_context
from app.metrics import record_request

REQUEST_ID_HEADER = "X-Request-ID"
SLOW_REQUEST_WARN_MS_ENV = "ZHIFEI_SLOW_REQUEST_WARN_MS"
DEFAULT_SLOW_REQUEST_WARN_MS = 1500.0


def _get_slow_request_warn_ms() -> float:
    raw_value = str(os.getenv(SLOW_REQUEST_WARN_MS_ENV, str(DEFAULT_SLOW_REQUEST_WARN_MS))).strip()
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return DEFAULT_SLOW_REQUEST_WARN_MS
    return value if value >= 0 else DEFAULT_SLOW_REQUEST_WARN_MS


def _get_endpoint_label(request: Request) -> str:
    route = request.scope.get("route")
    if isinstance(route, BaseRoute):
        path = getattr(route, "path_format", None) or getattr(route, "path", None)
        if isinstance(path, str) and path.strip():
            return path
    return str(request.url.path or "/")


def configure_observability(app: FastAPI, logger: logging.Logger) -> None:
    if getattr(app.state, "_zhifei_observability_configured", False):
        return

    app.state._zhifei_observability_configured = True
    slow_request_warn_ms = _get_slow_request_warn_ms()

    @app.middleware("http")
    async def request_id_and_latency_middleware(request: Request, call_next):
        request_id = str(request.headers.get(REQUEST_ID_HEADER) or uuid4().hex)
        request.state.request_id = request_id
        started = time.perf_counter()
        with runtime_context(correlation_id=request_id, run_id=request_id):
            try:
                response = await call_next(request)
            except Exception:
                duration_ms = (time.perf_counter() - started) * 1000.0
                record_request(
                    request.method,
                    _get_endpoint_label(request),
                    500,
                    duration_ms / 1000.0,
                )
                if duration_ms >= slow_request_warn_ms:
                    log_structured(
                        logger,
                        level=logging.WARNING,
                        event="http_request_slow_failed",
                        path=request.url.path,
                        method=request.method,
                        duration_ms=round(duration_ms, 1),
                        status_code=500,
                    )
                raise

            duration_ms = (time.perf_counter() - started) * 1000.0
            record_request(
                request.method,
                _get_endpoint_label(request),
                int(response.status_code),
                duration_ms / 1000.0,
            )
            if REQUEST_ID_HEADER not in response.headers:
                response.headers[REQUEST_ID_HEADER] = request_id
            if duration_ms >= slow_request_warn_ms:
                log_structured(
                    logger,
                    level=logging.WARNING,
                    event="http_request_slow",
                    path=request.url.path,
                    method=request.method,
                    status_code=int(response.status_code),
                    duration_ms=round(duration_ms, 1),
                )
            return response
