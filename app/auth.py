"""API Key 认证模块。

支持通过环境变量配置 API Key，实现简单的 API 访问控制。

环境变量:
    API_KEYS: 逗号分隔的 API Key 列表。
        - 兼容旧格式: "key1,key2,key3"（默认视为 admin）
        - 角色格式: "admin:key1,ops:key2,readonly:key3"
        - 如果未设置，则跳过认证（开发模式）

使用方式:
    # Header 认证
    curl -H "X-API-Key: your-api-key" http://localhost:8000/score

    # Query 参数认证
    curl http://localhost:8000/score?api_key=your-api-key
"""

from __future__ import annotations

import os
from typing import Dict, Optional

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader, APIKeyQuery

# API Key 配置
API_KEYS_ENV = "API_KEYS"
API_KEY_HEADER_NAME = "X-API-Key"
API_KEY_QUERY_NAME = "api_key"
DEFAULT_API_KEY_ROLE = "admin"
OPS_API_KEY_ROLE = "ops"
READONLY_API_KEY_ROLE = "readonly"
API_KEY_ROLES = (DEFAULT_API_KEY_ROLE, OPS_API_KEY_ROLE, READONLY_API_KEY_ROLE)

# Security schemes
api_key_header = APIKeyHeader(name=API_KEY_HEADER_NAME, auto_error=False)
api_key_query = APIKeyQuery(name=API_KEY_QUERY_NAME, auto_error=False)


def _normalize_api_key_role(role: object) -> str:
    normalized = str(role or "").strip().lower()
    return normalized if normalized in API_KEY_ROLES else ""


def _parse_api_key_bindings_from_text(keys_str: str) -> list[Dict[str, object]]:
    if not keys_str.strip():
        return []

    bindings_by_key: Dict[str, Dict[str, object]] = {}
    for raw_entry in keys_str.split(","):
        token = raw_entry.strip()
        if not token:
            continue
        role = DEFAULT_API_KEY_ROLE
        key = token
        explicit_role = False
        if ":" in token:
            prefix, maybe_key = token.split(":", 1)
            normalized_role = _normalize_api_key_role(prefix)
            if normalized_role and maybe_key.strip():
                role = normalized_role
                key = maybe_key.strip()
                explicit_role = True
        bindings_by_key[key] = {
            "key": key,
            "role": role,
            "explicit_role": explicit_role,
        }
    return list(bindings_by_key.values())


def _parse_api_key_bindings() -> list[Dict[str, object]]:
    """解析 API_KEYS，兼容旧格式与 role:key 格式。"""
    keys_str = os.environ.get(API_KEYS_ENV, "")
    return _parse_api_key_bindings_from_text(keys_str)


def resolve_api_key_for_role(
    preferred_role: str,
    *,
    api_keys_value: Optional[str] = None,
    fallback_roles: tuple[str, ...] = (),
) -> Optional[str]:
    """按角色解析可用 API key。

    兼容旧格式：
    - `key1,key2` 会被视为 admin key 集合
    - `admin:key1,ops:key2` 则按显式角色选择
    """
    normalized_role = _normalize_api_key_role(preferred_role) or DEFAULT_API_KEY_ROLE
    if api_keys_value is None:
        api_keys_value = os.environ.get(API_KEYS_ENV, "")
    bindings = _parse_api_key_bindings_from_text(str(api_keys_value or ""))
    if not bindings:
        return None

    search_roles = (normalized_role,) + tuple(
        role for role in fallback_roles if _normalize_api_key_role(role)
    )
    for role in search_roles:
        match = next((row for row in bindings if str(row.get("role")) == role), None)
        if match is not None:
            return str(match.get("key") or "")
    return str(bindings[0].get("key") or "") or None


def get_valid_api_keys() -> list[str]:
    """从环境变量获取有效的 API Key 列表。

    Returns:
        有效的 API Key 列表，如果未配置则返回空列表
    """
    return [str(binding["key"]) for binding in _parse_api_key_bindings()]


def is_auth_enabled() -> bool:
    """检查是否启用了 API Key 认证。

    Returns:
        True 如果配置了至少一个 API Key，否则 False
    """
    return len(get_valid_api_keys()) > 0


def verify_explicit_api_key(
    api_key: Optional[str],
    *,
    required_roles: tuple[str, ...] = (DEFAULT_API_KEY_ROLE,),
) -> Optional[str]:
    """验证显式传入的 API Key。

    用于 Web 表单回退等无法直接复用 FastAPI Security 依赖的场景。
    """
    bindings = _parse_api_key_bindings()

    if not bindings:
        return None

    normalized_key = str(api_key or "").strip()
    if not normalized_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少 API Key。请先填写并保存 API Key。",
        )

    binding = next((row for row in bindings if str(row.get("key")) == normalized_key), None)
    if binding is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的 API Key。",
        )

    role = str(binding.get("role") or DEFAULT_API_KEY_ROLE)
    if required_roles and role not in required_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"API Key 权限不足，需要角色：{'/'.join(required_roles)}。",
        )

    return normalized_key


def _verify_api_key_for_roles(
    *,
    required_roles: tuple[str, ...],
    api_key_header: Optional[str] = Security(api_key_header),
    api_key_query: Optional[str] = Security(api_key_query),
) -> Optional[str]:
    """验证 API Key，并校验角色权限。"""
    bindings = _parse_api_key_bindings()
    if not bindings:
        return None

    api_key = api_key_header or api_key_query
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少 API Key。请在 Header 中添加 X-API-Key 或在 URL 中添加 api_key 参数。",
        )
    return verify_explicit_api_key(api_key, required_roles=required_roles)


def verify_api_key(
    api_key_header: Optional[str] = Security(api_key_header),
    api_key_query: Optional[str] = Security(api_key_query),
) -> Optional[str]:
    """验证 API Key（默认 admin 权限）。"""
    return _verify_api_key_for_roles(
        required_roles=(DEFAULT_API_KEY_ROLE,),
        api_key_header=api_key_header,
        api_key_query=api_key_query,
    )


def verify_ops_api_key(
    api_key_header: Optional[str] = Security(api_key_header),
    api_key_query: Optional[str] = Security(api_key_query),
) -> Optional[str]:
    """验证 API Key（允许 admin / ops）。"""
    return _verify_api_key_for_roles(
        required_roles=(DEFAULT_API_KEY_ROLE, OPS_API_KEY_ROLE),
        api_key_header=api_key_header,
        api_key_query=api_key_query,
    )


def get_auth_status() -> dict:
    """获取当前认证状态信息。

    Returns:
        包含认证状态的字典
    """
    enabled = is_auth_enabled()
    bindings = _parse_api_key_bindings()
    role_key_counts: Dict[str, int] = {}
    explicit_role_entries = 0
    for binding in bindings:
        role = str(binding.get("role") or DEFAULT_API_KEY_ROLE)
        role_key_counts[role] = role_key_counts.get(role, 0) + 1
        if bool(binding.get("explicit_role")):
            explicit_role_entries += 1
    return {
        "auth_enabled": enabled,
        "configured_keys_count": len(bindings),
        "auth_methods": ["X-API-Key header", "api_key query param"] if enabled else [],
        "role_mode_enabled": explicit_role_entries > 0,
        "default_role": DEFAULT_API_KEY_ROLE,
        "configured_roles": sorted(role_key_counts.keys()),
        "role_key_counts": role_key_counts,
    }
