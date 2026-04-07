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
from app.engine.llm_runtime_state import (
    clear_account_failure,
    get_account_failure_timestamps,
    get_account_request_stats,
    record_account_request_outcome,
    set_account_failure,
)

GEMINI_HTTP_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_DEFAULT_MODEL = "gemini-1.5-pro"
GEMINI_API_KEYS_ENV = "GEMINI_API_KEYS"
EVOLUTION_LLM_ACCOUNT_COOLDOWN_ENV = "EVOLUTION_LLM_ACCOUNT_COOLDOWN_SECONDS"
DEFAULT_EVOLUTION_LLM_ACCOUNT_COOLDOWN_SECONDS = 300.0
DEFAULT_EVOLUTION_LLM_ACCOUNT_QUALITY_SCORE_PRIOR_WEIGHT = 3.0
DEFAULT_EVOLUTION_LLM_ACCOUNT_QUALITY_SCORE_PRIOR_SUCCESS = 1.5
DEFAULT_EVOLUTION_LLM_ACCOUNT_LOW_QUALITY_THRESHOLD = 35.0
DEFAULT_EVOLUTION_LLM_ACCOUNT_LOW_QUALITY_MIN_HISTORY = 3
DEFAULT_EVOLUTION_LLM_ACCOUNT_LOW_QUALITY_PROMOTION_GAP = 12.0
_GEMINI_KEY_FAILURES: Dict[str, float] = {}
_GEMINI_KEY_CURSOR = 0
_GEMINI_KEY_LOCK = threading.Lock()


def _sync_key_failures_from_runtime_state(keys: List[str] | None = None) -> None:
    active_keys = list(keys if keys is not None else get_gemini_evolution_api_keys())
    persisted = get_account_failure_timestamps("gemini", active_keys)
    with _GEMINI_KEY_LOCK:
        _GEMINI_KEY_FAILURES.clear()
        _GEMINI_KEY_FAILURES.update(persisted)


def _get_gemini_model() -> str:
    """进化用模型，默认最高端 gemini-1.5-pro；可通过 GEMINI_MODEL 覆盖。"""
    return (os.getenv("GEMINI_MODEL") or "").strip() or GEMINI_DEFAULT_MODEL


def _get_gemini_api_key() -> Optional[str]:
    return (os.getenv("GEMINI_API_KEY") or "").strip() or None


def get_gemini_evolution_api_keys() -> List[str]:
    return parse_api_key_pool(_get_gemini_api_key(), os.getenv(GEMINI_API_KEYS_ENV))


def get_gemini_evolution_account_count() -> int:
    return len(get_gemini_evolution_api_keys())


def get_gemini_evolution_pool_health() -> Dict[str, int]:
    keys = get_gemini_evolution_api_keys()
    _sync_key_failures_from_runtime_state(keys)
    now = time.time()
    cooldown = _account_cooldown_seconds()
    healthy_accounts = 0
    cooling_accounts = 0
    with _GEMINI_KEY_LOCK:
        for key in keys:
            failed_at = _GEMINI_KEY_FAILURES.get(key)
            if failed_at is None or (now - failed_at) >= cooldown:
                healthy_accounts += 1
            else:
                cooling_accounts += 1
    return {
        "total_accounts": len(keys),
        "healthy_accounts": healthy_accounts,
        "cooling_accounts": cooling_accounts,
    }


def get_gemini_evolution_pool_quality() -> Dict[str, float]:
    keys = get_gemini_evolution_api_keys()
    stats = get_account_request_stats("gemini", keys)
    scores = [_key_quality_score(key, stats) for key in keys if key]
    rated_scores = [
        _key_quality_score(key, stats) for key in keys if (_key_total_attempts(key, stats) > 0)
    ]
    sufficiently_rated_scores = [
        _key_quality_score(key, stats)
        for key in keys
        if _key_total_attempts(key, stats) >= DEFAULT_EVOLUTION_LLM_ACCOUNT_LOW_QUALITY_MIN_HISTORY
    ]
    low_quality_accounts = sum(1 for key in keys if _key_is_low_quality(key, stats))
    if not scores:
        return {}
    display_scores = sufficiently_rated_scores or [50.0 for _ in keys if _]
    return {
        "total_accounts": float(len(keys)),
        "rated_accounts": float(len(rated_scores)),
        "sufficiently_rated_accounts": float(len(sufficiently_rated_scores)),
        "average_quality_score": round(sum(display_scores) / float(len(display_scores)), 1),
        "best_quality_score": round(max(display_scores), 1),
        "worst_quality_score": round(min(display_scores), 1),
        "low_quality_accounts": float(low_quality_accounts),
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
    _sync_key_failures_from_runtime_state(keys)
    now = time.time()
    cooldown = _account_cooldown_seconds()
    key_stats = get_account_request_stats("gemini", keys)
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
        ready = sorted(
            ready,
            key=lambda key: (
                _key_quality_score(key, key_stats),
                _key_total_attempts(key, key_stats),
            ),
            reverse=True,
        )
        best_ready_score = max((_key_quality_score(key, key_stats) for key in ready), default=0.0)
        ready_strong = [
            key
            for key in ready
            if not _key_should_be_deprioritized(
                key,
                key_stats,
                best_available_score=best_ready_score,
            )
        ]
        ready_weak = [key for key in ready if key not in ready_strong]
        cooling = sorted(
            cooling,
            key=lambda key: (
                _key_quality_score(key, key_stats),
                _key_total_attempts(key, key_stats),
            ),
            reverse=True,
        )
        if ready:
            return ready_strong + ready_weak
        return cooling or rotated


def _key_total_attempts(key: str, stats: Dict[str, Dict[str, Any]]) -> int:
    row = stats.get(str(key or "").strip()) or {}
    return max(0, int(row.get("success_count") or 0)) + max(0, int(row.get("failure_count") or 0))


def _key_quality_score(key: str, stats: Dict[str, Dict[str, Any]]) -> float:
    row = stats.get(str(key or "").strip()) or {}
    success_count = max(0, int(row.get("success_count") or 0))
    failure_count = max(0, int(row.get("failure_count") or 0))
    total = success_count + failure_count
    score = 100.0 * (
        (float(success_count) + DEFAULT_EVOLUTION_LLM_ACCOUNT_QUALITY_SCORE_PRIOR_SUCCESS)
        / (float(total) + DEFAULT_EVOLUTION_LLM_ACCOUNT_QUALITY_SCORE_PRIOR_WEIGHT)
    )
    return round(max(0.0, min(100.0, score)), 1)


def _key_is_low_quality(key: str, stats: Dict[str, Dict[str, Any]]) -> bool:
    return _key_total_attempts(
        key, stats
    ) >= DEFAULT_EVOLUTION_LLM_ACCOUNT_LOW_QUALITY_MIN_HISTORY and (
        _key_quality_score(key, stats) < DEFAULT_EVOLUTION_LLM_ACCOUNT_LOW_QUALITY_THRESHOLD
    )


def _key_should_be_deprioritized(
    key: str,
    stats: Dict[str, Dict[str, Any]],
    *,
    best_available_score: float,
) -> bool:
    quality_score = _key_quality_score(key, stats)
    if not _key_is_low_quality(key, stats):
        return False
    return (
        best_available_score - quality_score
    ) >= DEFAULT_EVOLUTION_LLM_ACCOUNT_LOW_QUALITY_PROMOTION_GAP


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
    clear_account_failure("gemini", key)
    record_account_request_outcome("gemini", key, "success", time.time())


def _mark_key_failure(key: str) -> None:
    failed_at = time.time()
    with _GEMINI_KEY_LOCK:
        _GEMINI_KEY_FAILURES[key] = failed_at
    set_account_failure("gemini", key, failed_at)
    record_account_request_outcome("gemini", key, "failure", failed_at)


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
