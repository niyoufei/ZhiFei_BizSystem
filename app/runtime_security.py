from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.auth import is_auth_enabled
from app.storage import is_secure_desktop_mode_enabled

logger = logging.getLogger(__name__)

PRODUCTION_MODE_ENV = "ZHIFEI_PRODUCTION_MODE"
ENABLE_API_DOCS_ENV = "ZHIFEI_ENABLE_API_DOCS"
ALLOWED_HOSTS_ENV = "ZHIFEI_ALLOWED_HOSTS"
MAX_UPLOAD_MB_ENV = "ZHIFEI_MAX_UPLOAD_MB"
REQUIRE_API_KEYS_ENV = "ZHIFEI_REQUIRE_API_KEYS"

SECURE_DESKTOP_NOTICE = (
    "保密模式已启用：本机资料按 Windows 当前用户加密保存，"
    "Markdown 导出、下载与复制功能已禁用，仅允许本机访问。"
)
SECURE_DESKTOP_EXPORT_DETAIL = "保密模式已启用：当前版本禁用 Markdown 导出、下载与外发接口。"


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = str(raw).strip().lower()
    return value in {"1", "true", "yes", "on"}


def is_production_mode_enabled() -> bool:
    return _env_flag(PRODUCTION_MODE_ENV, default=False)


def are_api_docs_enabled() -> bool:
    if is_production_mode_enabled():
        return _env_flag(ENABLE_API_DOCS_ENV, default=False)
    return _env_flag(ENABLE_API_DOCS_ENV, default=True)


def build_fastapi_runtime_kwargs() -> Dict[str, object]:
    if are_api_docs_enabled():
        return {}
    return {"docs_url": None, "redoc_url": None, "openapi_url": None}


def get_allowed_hosts() -> List[str]:
    raw = str(os.environ.get(ALLOWED_HOSTS_ENV) or "").strip()
    if not raw:
        return []
    hosts = [item.strip() for item in raw.split(",") if item.strip()]
    return list(dict.fromkeys(hosts))


def get_max_upload_mb() -> float:
    raw = str(os.environ.get(MAX_UPLOAD_MB_ENV) or "").strip()
    if raw:
        try:
            value = float(raw)
        except ValueError:
            logger.warning("invalid max upload size env value: %s=%s", MAX_UPLOAD_MB_ENV, raw)
            return 64.0 if is_production_mode_enabled() else 0.0
        return max(0.0, value)
    return 64.0 if is_production_mode_enabled() else 0.0


def get_max_upload_bytes() -> int:
    mb = get_max_upload_mb()
    if mb <= 0:
        return 0
    return int(mb * 1024 * 1024)


def should_require_api_keys() -> bool:
    return _env_flag(REQUIRE_API_KEYS_ENV, default=is_production_mode_enabled())


def validate_runtime_security_settings() -> None:
    if should_require_api_keys() and not is_auth_enabled():
        raise RuntimeError("production_runtime_requires_api_keys")


def get_runtime_security_status() -> Dict[str, Any]:
    allowed_hosts = get_allowed_hosts()
    max_upload_mb = get_max_upload_mb()
    return {
        "production_mode": is_production_mode_enabled(),
        "api_docs_enabled": are_api_docs_enabled(),
        "allowed_hosts": allowed_hosts,
        "allowed_hosts_configured": bool(allowed_hosts),
        "max_upload_mb": max_upload_mb,
        "upload_limit_enabled": max_upload_mb > 0,
        "require_api_keys": should_require_api_keys(),
        "auth_enabled": is_auth_enabled(),
        "secure_desktop_mode": is_secure_desktop_mode_enabled(),
    }


def assert_secure_desktop_allows_export(feature_name: str) -> None:
    if not is_secure_desktop_mode_enabled():
        return
    logger.warning("secure_desktop_export_blocked feature=%s", feature_name)
    raise HTTPException(status_code=403, detail=SECURE_DESKTOP_EXPORT_DETAIL)


def configure_runtime_security(app: FastAPI) -> None:
    allowed_hosts = get_allowed_hosts()
    if allowed_hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

    max_upload_bytes = get_max_upload_bytes()
    if max_upload_bytes <= 0:
        return

    @app.middleware("http")
    async def _enforce_request_size_limit(request: Request, call_next):
        if request.method.upper() in {"POST", "PUT", "PATCH"}:
            raw_length = str(request.headers.get("content-length") or "").strip()
            if raw_length:
                try:
                    content_length = int(raw_length)
                except ValueError:
                    content_length = 0
                if content_length > max_upload_bytes:
                    max_upload_mb = round(max_upload_bytes / (1024 * 1024), 2)
                    return JSONResponse(
                        status_code=413,
                        content={
                            "detail": (
                                f"请求体过大，当前运行时限制为 {max_upload_mb} MB。"
                                f"请减小上传文件体积或调整环境变量 {MAX_UPLOAD_MB_ENV}。"
                            )
                        },
                    )
        return await call_next(request)
