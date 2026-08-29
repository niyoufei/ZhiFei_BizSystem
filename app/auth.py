"""Header-only, fail-closed API Key authentication."""

from __future__ import annotations

import hmac
import os
from typing import Optional

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

# API Key 配置
API_KEYS_ENV = "API_KEYS"
API_KEY_HEADER_NAME = "X-API-Key"

# Security schemes
api_key_header = APIKeyHeader(name=API_KEY_HEADER_NAME, auto_error=False)


def get_valid_api_keys() -> list[str]:
    """从环境变量获取有效的 API Key 列表。

    Returns:
        有效的 API Key 列表，如果未配置则返回空列表
    """
    keys_str = os.environ.get(API_KEYS_ENV, "")
    if not keys_str.strip():
        return []
    return [k.strip() for k in keys_str.split(",") if k.strip()]


def is_auth_enabled() -> bool:
    """检查是否启用了 API Key 认证。

    Returns:
        True 如果配置了至少一个 API Key，否则 False
    """
    return len(get_valid_api_keys()) > 0


def verify_api_key(
    api_key_header: Optional[str] = Security(api_key_header),
) -> str:
    """Validate an API key supplied only through ``X-API-Key``."""
    valid_keys = get_valid_api_keys()

    if not valid_keys:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "AUTH_NOT_CONFIGURED",
                "message": "API authentication is not configured.",
            },
        )

    if not api_key_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "AUTH_KEY_MISSING",
                "message": "X-API-Key header is required.",
            },
        )

    if not any(hmac.compare_digest(api_key_header, valid_key) for valid_key in valid_keys):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "AUTH_KEY_INVALID",
                "message": "Invalid API key.",
            },
        )

    return api_key_header


def get_auth_status() -> dict:
    """获取当前认证状态信息。

    Returns:
        包含认证状态的字典
    """
    enabled = is_auth_enabled()
    keys = get_valid_api_keys()
    return {
        "auth_enabled": enabled,
        "configured_keys_count": len(keys),
        "auth_methods": ["X-API-Key header"] if enabled else [],
    }
