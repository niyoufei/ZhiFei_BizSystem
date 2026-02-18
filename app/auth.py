"""API Key 认证模块。

支持通过环境变量配置 API Key，实现简单的 API 访问控制。

环境变量:
    API_KEYS: 逗号分隔的 API Key 列表，如 "key1,key2,key3"
             如果未设置，则跳过认证（开发模式）

使用方式:
    # Header 认证
    curl -H "X-API-Key: your-api-key" http://localhost:8000/score

    # Query 参数认证
    curl http://localhost:8000/score?api_key=your-api-key
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader, APIKeyQuery

# API Key 配置
API_KEYS_ENV = "API_KEYS"
API_KEY_HEADER_NAME = "X-API-Key"
API_KEY_QUERY_NAME = "api_key"

# Security schemes
api_key_header = APIKeyHeader(name=API_KEY_HEADER_NAME, auto_error=False)
api_key_query = APIKeyQuery(name=API_KEY_QUERY_NAME, auto_error=False)


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
    api_key_query: Optional[str] = Security(api_key_query),
) -> Optional[str]:
    """验证 API Key（FastAPI 依赖）。

    优先检查 Header，然后检查 Query 参数。
    如果未配置任何 API Key，则跳过认证（开发模式）。

    Args:
        api_key_header: 从请求 Header 中提取的 API Key
        api_key_query: 从 Query 参数中提取的 API Key

    Returns:
        验证通过的 API Key，或 None（开发模式）

    Raises:
        HTTPException: 401 如果认证失败
    """
    valid_keys = get_valid_api_keys()

    # 如果未配置 API Key，跳过认证（开发模式）
    if not valid_keys:
        return None

    # 优先使用 Header 中的 Key
    api_key = api_key_header or api_key_query

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少 API Key。请在 Header 中添加 X-API-Key 或在 URL 中添加 api_key 参数。",
        )

    if api_key not in valid_keys:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的 API Key。",
        )

    return api_key


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
        "auth_methods": ["X-API-Key header", "api_key query param"] if enabled else [],
    }
