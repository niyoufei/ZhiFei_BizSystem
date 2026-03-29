"""
进化报告 Gemini 增强：使用 Google Gemini API 在规则版报告基础上生成高分逻辑与编制指导。
配置 GEMINI_API_KEY 后，将 EVOLUTION_LLM_BACKEND=gemini 即可启用。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.engine.llm_evolution_common import (
    build_evolution_prompt,
    parse_api_key_pool,
    parse_evolution_response,
)

GEMINI_HTTP_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_DEFAULT_MODEL = "gemini-1.5-pro"
GEMINI_API_KEYS_ENV = "GEMINI_API_KEYS"
EVOLUTION_LLM_ACCOUNT_COOLDOWN_ENV = "EVOLUTION_LLM_ACCOUNT_COOLDOWN_SECONDS"
DEFAULT_EVOLUTION_LLM_ACCOUNT_COOLDOWN_SECONDS = 300.0
_GEMINI_KEY_FAILURES: Dict[str, float] = {}
_GEMINI_KEY_CURSOR = 0
_GEMINI_KEY_LOCK = threading.Lock()


def _get_gemini_model() -> str:
    """进化用模型，默认最高端 gemini-1.5-pro；可通过 GEMINI_MODEL 覆盖。"""
    return (os.getenv("GEMINI_MODEL") or "").strip() or GEMINI_DEFAULT_MODEL


def _get_gemini_api_key() -> Optional[str]:
    return (os.getenv("GEMINI_API_KEY") or "").strip() or None


def get_gemini_evolution_api_keys() -> List[str]:
    return parse_api_key_pool(_get_gemini_api_key(), os.getenv(GEMINI_API_KEYS_ENV))


def get_gemini_evolution_account_count() -> int:
    return len(get_gemini_evolution_api_keys())


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
    global _GEMINI_KEY_CURSOR
    with _GEMINI_KEY_LOCK:
        start = _GEMINI_KEY_CURSOR % len(keys)
        rotated = keys[start:] + keys[:start]
        ready: List[str] = []
        cooling: List[str] = []
        for key in rotated:
            failed_at = _GEMINI_KEY_FAILURES.get(key)
            if failed_at is None or (now - failed_at) >= cooldown:
                ready.append(key)
            else:
                cooling.append(key)
        if ready:
            return ready
        return cooling or rotated


def _mark_key_success(key: str) -> None:
    global _GEMINI_KEY_CURSOR
    with _GEMINI_KEY_LOCK:
        _GEMINI_KEY_FAILURES.pop(key, None)
        keys = get_gemini_evolution_api_keys()
        if keys:
            try:
                current_index = keys.index(key)
            except ValueError:
                current_index = _GEMINI_KEY_CURSOR
            _GEMINI_KEY_CURSOR = (current_index + 1) % len(keys)


def _mark_key_failure(key: str) -> None:
    with _GEMINI_KEY_LOCK:
        _GEMINI_KEY_FAILURES[key] = time.time()


def _extract_json_from_content(content: str) -> Optional[Dict[str, Any]]:
    content = content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    for pattern in (r"```(?:json)?\s*([\s\S]*?)\s*```", r"(\{[\s\S]*\})"):
        match = re.search(pattern, content)
        if match:
            raw = match.group(1).strip() if match.lastindex else match.group(0)
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                continue
    return None


def _call_gemini_http(
    user_message: str,
    api_key: str | None = None,
    model: str | None = None,
    max_tokens: int = 4096,
    timeout: int = 90,
) -> tuple[bool, Optional[Dict[str, Any]], str]:
    """调用 Gemini generateContent，返回 (成功, 解析后的 JSON 或 None, 错误信息)。"""
    key = api_key or _get_gemini_api_key()
    if not key:
        return False, None, "missing_credentials"
    if model is None:
        model = _get_gemini_model()
    url = f"{GEMINI_HTTP_BASE}/{model}:generateContent?key={key}"
    try:
        import urllib.request

        body = json.dumps(
            {
                "contents": [{"parts": [{"text": user_message}]}],
                "generationConfig": {
                    "temperature": 0.3,
                    "maxOutputTokens": max_tokens,
                },
            },
            ensure_ascii=False,
        ).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return False, None, str(e)
    candidates = data.get("candidates")
    if not candidates or not isinstance(candidates, list):
        return False, None, "invalid_response_no_candidates"
    content_parts = (candidates[0].get("content") or {}).get("parts") or []
    if not content_parts:
        return False, None, "empty_content"
    text = (content_parts[0].get("text") or "").strip()
    if not text:
        return False, None, "empty_content"
    parsed = _extract_json_from_content(text)
    if parsed is None:
        return False, None, "json_parse_failed"
    return True, parsed, ""


def enhance_evolution_report_gemini(
    project_id: str,
    report: Dict[str, Any],
    ground_truth_records: List[Dict[str, Any]],
    project_context: str = "",
) -> Optional[Dict[str, Any]]:
    """
    使用 Gemini 增强进化报告。成功时返回完整报告 dict（含 enhanced_by）。
    未配置 GEMINI_API_KEY 或调用失败时返回 None，调用方保留规则版报告。
    """
    keys = get_gemini_evolution_api_keys()
    if not keys and not _get_gemini_api_key():
        return None
    prompt = build_evolution_prompt(report, ground_truth_records, project_context)
    attempts = _build_key_attempt_order(keys or [_get_gemini_api_key() or ""])
    for api_key in attempts:
        if not api_key:
            continue
        ok, parsed, _ = _call_gemini_http(prompt, api_key=api_key, max_tokens=4096)
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
            "enhanced_by": "gemini",
        }
    return None
