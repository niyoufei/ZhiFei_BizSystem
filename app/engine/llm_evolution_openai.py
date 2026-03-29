"""
进化报告 OpenAI 增强：使用 OpenAI GPT-5.4 在规则版报告基础上生成高分逻辑与编制指导。
配置 OPENAI_API_KEY 后，将 EVOLUTION_LLM_BACKEND=openai 即可启用。
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.engine.llm_evolution_common import (
    build_evolution_prompt,
    parse_api_key_pool,
    parse_evolution_response,
)
from app.engine.openai_compat import call_openai_json, get_openai_api_key, get_openai_model

OPENAI_API_KEYS_ENV = "OPENAI_API_KEYS"
EVOLUTION_LLM_ACCOUNT_COOLDOWN_ENV = "EVOLUTION_LLM_ACCOUNT_COOLDOWN_SECONDS"
DEFAULT_EVOLUTION_LLM_ACCOUNT_COOLDOWN_SECONDS = 300.0
_OPENAI_KEY_FAILURES: Dict[str, float] = {}
_OPENAI_KEY_CURSOR = 0
_OPENAI_KEY_LOCK = threading.Lock()


def get_openai_evolution_api_keys() -> List[str]:
    return parse_api_key_pool(os.getenv("OPENAI_API_KEY"), os.getenv(OPENAI_API_KEYS_ENV))


def get_openai_evolution_account_count() -> int:
    return len(get_openai_evolution_api_keys())


def get_openai_evolution_pool_health() -> Dict[str, int]:
    keys = get_openai_evolution_api_keys()
    now = time.time()
    cooldown = _account_cooldown_seconds()
    healthy_accounts = 0
    cooling_accounts = 0
    with _OPENAI_KEY_LOCK:
        for key in keys:
            failed_at = _OPENAI_KEY_FAILURES.get(key)
            if failed_at is None or (now - failed_at) >= cooldown:
                healthy_accounts += 1
            else:
                cooling_accounts += 1
    return {
        "total_accounts": len(keys),
        "healthy_accounts": healthy_accounts,
        "cooling_accounts": cooling_accounts,
    }


def _account_cooldown_seconds() -> float:
    raw = str(os.getenv(EVOLUTION_LLM_ACCOUNT_COOLDOWN_ENV) or "").strip()
    try:
        return max(30.0, float(raw)) if raw else DEFAULT_EVOLUTION_LLM_ACCOUNT_COOLDOWN_SECONDS
    except Exception:
        return DEFAULT_EVOLUTION_LLM_ACCOUNT_COOLDOWN_SECONDS


def _build_key_attempt_order(keys: List[str]) -> List[str]:
    if not keys:
        return []
    now = time.time()
    cooldown = _account_cooldown_seconds()
    global _OPENAI_KEY_CURSOR
    with _OPENAI_KEY_LOCK:
        start = _OPENAI_KEY_CURSOR % len(keys)
        rotated = keys[start:] + keys[:start]
        ready: List[str] = []
        cooling: List[str] = []
        for key in rotated:
            failed_at = _OPENAI_KEY_FAILURES.get(key)
            if failed_at is None or (now - failed_at) >= cooldown:
                ready.append(key)
            else:
                cooling.append(key)
        if ready:
            return ready
        return cooling or rotated


def _mark_key_success(key: str) -> None:
    global _OPENAI_KEY_CURSOR
    with _OPENAI_KEY_LOCK:
        _OPENAI_KEY_FAILURES.pop(key, None)
        keys = get_openai_evolution_api_keys()
        if keys:
            try:
                current_index = keys.index(key)
            except ValueError:
                current_index = _OPENAI_KEY_CURSOR
            _OPENAI_KEY_CURSOR = (current_index + 1) % len(keys)


def _mark_key_failure(key: str) -> None:
    with _OPENAI_KEY_LOCK:
        _OPENAI_KEY_FAILURES[key] = time.time()


def _call_openai_http(
    user_message: str,
    api_key: str | None = None,
    model: str | None = None,
    max_tokens: int = 4096,
    timeout: int = 90,
) -> tuple[bool, Optional[Dict[str, Any]], str]:
    """调用 OpenAI Chat Completions，返回 (成功, 解析后的 JSON 或 None, 错误信息)。"""
    return call_openai_json(
        user_message,
        api_key=api_key,
        model=model or get_openai_model(),
        max_tokens=max_tokens,
        timeout=timeout,
        temperature=0.3,
    )


def enhance_evolution_report_openai(
    project_id: str,
    report: Dict[str, Any],
    ground_truth_records: List[Dict[str, Any]],
    project_context: str = "",
) -> Optional[Dict[str, Any]]:
    """
    使用 OpenAI 增强进化报告。成功时返回完整报告 dict（含 enhanced_by）。
    未配置 OPENAI_API_KEY 或调用失败时返回 None，调用方保留规则版报告。
    """
    keys = get_openai_evolution_api_keys()
    if not keys and not get_openai_api_key():
        return None
    prompt = build_evolution_prompt(report, ground_truth_records, project_context)
    attempts = _build_key_attempt_order(keys or [get_openai_api_key() or ""])
    for api_key in attempts:
        if not api_key:
            continue
        ok, parsed, _ = _call_openai_http(prompt, api_key=api_key, max_tokens=4096)
        if not ok or not parsed:
            _mark_key_failure(api_key)
            continue
        enhanced = parse_evolution_response(parsed)
        if not enhanced:
            _mark_key_failure(api_key)
            continue
        _mark_key_success(api_key)
        return {
            "project_id": project_id,
            "high_score_logic": enhanced["high_score_logic"],
            "writing_guidance": enhanced["writing_guidance"],
            "sample_count": report.get("sample_count", 0),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "enhanced_by": "openai",
        }
    return None
