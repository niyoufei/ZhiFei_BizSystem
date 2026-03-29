"""
进化报告 LLM 增强：根据 EVOLUTION_LLM_BACKEND 调用不同 AI 后端，
对规则版进化报告进行增强，生成更丰富的高分逻辑与编制指导。

说明：
- 当前真实后端为 rules / openai / gemini。
- 支持 auto 多 provider 编排：优先主后端，失败时自动切到备用后端。
- 历史上的 spark 配置仅作为兼容别名保留，并在运行时映射到 openai。
"""
from __future__ import annotations

import os
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from app.engine.llm_evolution_common import parse_api_key_pool
from app.engine.llm_evolution_gemini import (
    get_gemini_evolution_pool_health,
    get_gemini_evolution_pool_quality,
)
from app.engine.llm_evolution_openai import (
    get_openai_evolution_pool_health,
    get_openai_evolution_pool_quality,
)
from app.engine.llm_runtime_state import (
    clear_provider_failure,
    clear_provider_quality_degraded,
    get_provider_failure_timestamps,
    get_provider_quality_degraded_timestamps,
    get_provider_review_stats,
    record_provider_review_outcome,
    set_provider_failure,
    set_provider_quality_degraded,
)
from app.engine.openai_compat import get_openai_model

# 支持的真实后端: rules | openai | gemini
EVOLUTION_LLM_BACKEND_ENV = "EVOLUTION_LLM_BACKEND"
LEGACY_SPARK_BACKEND_ALIAS = "spark"
AUTO_MULTI_PROVIDER_BACKEND = "auto"
LEGACY_SPARK_ENV_KEYS = (
    "SPARK_APIPASSWORD",
    "SPARK_MODEL",
    "SPARK_APP_ID",
    "SPARK_API_KEY",
    "SPARK_API_SECRET",
)
REAL_LLM_PROVIDERS: Tuple[str, ...] = ("openai", "gemini")
DEFAULT_ENHANCEMENT_REVIEW_SIMILARITY_THRESHOLD = 0.35
EVOLUTION_LLM_PROVIDER_COOLDOWN_ENV = "EVOLUTION_LLM_ACCOUNT_COOLDOWN_SECONDS"
DEFAULT_EVOLUTION_LLM_PROVIDER_COOLDOWN_SECONDS = 300.0
EVOLUTION_LLM_QUALITY_DEGRADE_SECONDS_ENV = "EVOLUTION_LLM_QUALITY_DEGRADE_SECONDS"
DEFAULT_EVOLUTION_LLM_QUALITY_DEGRADE_SECONDS = 1800.0
DEFAULT_PROVIDER_QUALITY_SCORE_PRIOR_WEIGHT = 4.0
DEFAULT_PROVIDER_QUALITY_SCORE_PRIOR_SUCCESS = 2.0
DEFAULT_PROVIDER_QUALITY_PROMOTION_MIN_HISTORY = 3
DEFAULT_PROVIDER_QUALITY_PROMOTION_MIN_GAP = 8.0
_PROVIDER_FAILURES: Dict[str, float] = {}
_PROVIDER_FAILURES_LOCK = threading.Lock()
_PROVIDER_QUALITY_DEGRADED: Dict[str, float] = {}
_PROVIDER_QUALITY_DEGRADED_LOCK = threading.Lock()
_PROVIDER_REVIEW_STATS: Dict[str, Dict[str, Any]] = {}
_PROVIDER_REVIEW_STATS_LOCK = threading.Lock()


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _sync_provider_failures_from_runtime_state() -> None:
    persisted = get_provider_failure_timestamps()
    with _PROVIDER_FAILURES_LOCK:
        _PROVIDER_FAILURES.clear()
        _PROVIDER_FAILURES.update(persisted)


def _sync_provider_quality_from_runtime_state() -> None:
    persisted = get_provider_quality_degraded_timestamps()
    with _PROVIDER_QUALITY_DEGRADED_LOCK:
        _PROVIDER_QUALITY_DEGRADED.clear()
        _PROVIDER_QUALITY_DEGRADED.update(persisted)


def _sync_provider_review_stats_from_runtime_state() -> None:
    persisted = get_provider_review_stats()
    with _PROVIDER_REVIEW_STATS_LOCK:
        _PROVIDER_REVIEW_STATS.clear()
        for provider, stats in persisted.items():
            if provider in REAL_LLM_PROVIDERS and isinstance(stats, dict):
                _PROVIDER_REVIEW_STATS[provider] = dict(stats)


def _get_requested_evolution_llm_backend() -> Optional[str]:
    raw = (os.environ.get(EVOLUTION_LLM_BACKEND_ENV) or "").strip().lower()
    return raw or None


def _normalize_evolution_llm_backend(raw_backend: Optional[str]) -> Optional[str]:
    raw = str(raw_backend or "").strip().lower()
    if not raw:
        return None
    if raw == LEGACY_SPARK_BACKEND_ALIAS:
        return "openai"
    return raw


def _list_legacy_spark_env_keys() -> List[str]:
    return [key for key in LEGACY_SPARK_ENV_KEYS if str(os.getenv(key) or "").strip()]


def _provider_configured(provider: str) -> bool:
    return _provider_key_count(provider) > 0


def _provider_key_count(provider: str) -> int:
    if provider == "openai":
        return len(parse_api_key_pool(os.getenv("OPENAI_API_KEY"), os.getenv("OPENAI_API_KEYS")))
    if provider == "gemini":
        return len(parse_api_key_pool(os.getenv("GEMINI_API_KEY"), os.getenv("GEMINI_API_KEYS")))
    return 0


def _unique_provider_chain(items: List[str]) -> List[str]:
    out: List[str] = []
    for item in items:
        if item in REAL_LLM_PROVIDERS and item not in out:
            out.append(item)
    return out


def _provider_cooldown_seconds() -> float:
    raw = str(os.getenv(EVOLUTION_LLM_PROVIDER_COOLDOWN_ENV) or "").strip()
    try:
        return max(30.0, float(raw)) if raw else DEFAULT_EVOLUTION_LLM_PROVIDER_COOLDOWN_SECONDS
    except Exception:
        return DEFAULT_EVOLUTION_LLM_PROVIDER_COOLDOWN_SECONDS


def _provider_quality_degrade_seconds() -> float:
    raw = str(os.getenv(EVOLUTION_LLM_QUALITY_DEGRADE_SECONDS_ENV) or "").strip()
    try:
        return max(300.0, float(raw)) if raw else DEFAULT_EVOLUTION_LLM_QUALITY_DEGRADE_SECONDS
    except Exception:
        return DEFAULT_EVOLUTION_LLM_QUALITY_DEGRADE_SECONDS


def _provider_health_state(provider: str) -> str:
    if provider not in REAL_LLM_PROVIDERS:
        return "unknown"
    failed_at = _PROVIDER_FAILURES.get(provider)
    if failed_at is None:
        return "healthy"
    if (time.time() - failed_at) >= _provider_cooldown_seconds():
        return "healthy"
    return "cooldown"


def _provider_is_healthy(provider: str) -> bool:
    return _provider_health_state(provider) == "healthy"


def _provider_quality_state(provider: str) -> str:
    if provider not in REAL_LLM_PROVIDERS:
        return "unknown"
    degraded_at = _PROVIDER_QUALITY_DEGRADED.get(provider)
    if (
        degraded_at is not None
        and (time.time() - degraded_at) < _provider_quality_degrade_seconds()
    ):
        return "degraded"
    if _provider_review_regressed(provider):
        return "degraded"
    return "stable"


def _provider_review_regressed(provider: str) -> bool:
    with _PROVIDER_REVIEW_STATS_LOCK:
        stats = dict(_PROVIDER_REVIEW_STATS.get(provider) or {})
    confirmed_count = max(0, _to_int(stats.get("confirmed_count"), 0))
    diverged_count = max(0, _to_int(stats.get("diverged_count"), 0))
    return diverged_count >= 2 and diverged_count > confirmed_count


def _provider_review_stats(provider: str) -> Dict[str, Any]:
    with _PROVIDER_REVIEW_STATS_LOCK:
        return dict(_PROVIDER_REVIEW_STATS.get(provider) or {})


def _provider_quality_score(provider: str) -> float:
    stats = _provider_review_stats(provider)
    confirmed_count = max(0, _to_int(stats.get("confirmed_count"), 0))
    diverged_count = max(0, _to_int(stats.get("diverged_count"), 0))
    unavailable_count = max(0, _to_int(stats.get("unavailable_count"), 0))
    fallback_only_count = max(0, _to_int(stats.get("fallback_only_count"), 0))
    weighted_success = (
        float(confirmed_count)
        + (0.4 * float(fallback_only_count))
        + (0.2 * float(unavailable_count))
    )
    total = confirmed_count + diverged_count + unavailable_count + fallback_only_count
    score = 100.0 * (
        (weighted_success + DEFAULT_PROVIDER_QUALITY_SCORE_PRIOR_SUCCESS)
        / (float(total) + DEFAULT_PROVIDER_QUALITY_SCORE_PRIOR_WEIGHT)
    )
    return round(max(0.0, min(100.0, score)), 1)


def _provider_has_quality_signal(provider: str) -> bool:
    stats = _provider_review_stats(provider)
    total = (
        max(0, _to_int(stats.get("confirmed_count"), 0))
        + max(0, _to_int(stats.get("diverged_count"), 0))
        + max(0, _to_int(stats.get("unavailable_count"), 0))
        + max(0, _to_int(stats.get("fallback_only_count"), 0))
    )
    return total >= DEFAULT_PROVIDER_QUALITY_PROMOTION_MIN_HISTORY


def _provider_pool_health(provider: str) -> Dict[str, int]:
    if provider == "openai":
        return get_openai_evolution_pool_health()
    if provider == "gemini":
        return get_gemini_evolution_pool_health()
    return {}


def _provider_pool_quality(provider: str) -> Dict[str, float]:
    if provider == "openai":
        return get_openai_evolution_pool_quality()
    if provider == "gemini":
        return get_gemini_evolution_pool_quality()
    return {}


def _provider_priority_key(provider: str) -> tuple[int, float, int, int]:
    pool = _provider_pool_health(provider)
    quality = _provider_pool_quality(provider)
    healthy_accounts = max(0, _to_int(pool.get("healthy_accounts"), 0))
    total_accounts = max(0, _to_int(pool.get("total_accounts"), 0))
    cooling_accounts = max(0, _to_int(pool.get("cooling_accounts"), 0))
    average_quality_score = float(quality.get("average_quality_score") or 0.0)
    return (
        healthy_accounts,
        average_quality_score,
        total_accounts - cooling_accounts,
        total_accounts,
    )


def _should_promote_provider_by_quality(primary: str, alternate: str) -> bool:
    if not (_provider_has_quality_signal(primary) and _provider_has_quality_signal(alternate)):
        return False
    primary_score = _provider_quality_score(primary)
    alternate_score = _provider_quality_score(alternate)
    return (alternate_score - primary_score) >= DEFAULT_PROVIDER_QUALITY_PROMOTION_MIN_GAP


def _mark_provider_quality_stable(provider: str) -> None:
    with _PROVIDER_QUALITY_DEGRADED_LOCK:
        _PROVIDER_QUALITY_DEGRADED.pop(provider, None)
    clear_provider_quality_degraded(provider)


def _mark_provider_quality_degraded(provider: str) -> None:
    degraded_at = time.time()
    with _PROVIDER_QUALITY_DEGRADED_LOCK:
        _PROVIDER_QUALITY_DEGRADED[provider] = degraded_at
    set_provider_quality_degraded(provider, degraded_at)


def _mark_provider_success(provider: str) -> None:
    with _PROVIDER_FAILURES_LOCK:
        _PROVIDER_FAILURES.pop(provider, None)
    clear_provider_failure(provider)


def _mark_provider_failure(provider: str) -> None:
    failed_at = time.time()
    with _PROVIDER_FAILURES_LOCK:
        _PROVIDER_FAILURES[provider] = failed_at
    set_provider_failure(provider, failed_at)


def _order_provider_chain(
    providers: List[str],
    *,
    requested_provider: Optional[str] = None,
) -> List[str]:
    base = _unique_provider_chain(providers)
    if len(base) <= 1:
        return base
    healthy = [provider for provider in base if _provider_is_healthy(provider)]
    cooling = [provider for provider in base if provider not in healthy]
    if not healthy:
        return base
    if requested_provider and requested_provider in healthy:
        return (
            [requested_provider]
            + [provider for provider in healthy if provider != requested_provider]
            + cooling
        )
    ranked_healthy = sorted(
        healthy,
        key=lambda provider: (
            _provider_quality_state(provider) == "stable",
            _provider_quality_score(provider),
            _provider_priority_key(provider),
            -base.index(provider),
        ),
        reverse=True,
    )
    return ranked_healthy + cooling


def _provider_selection_reason(
    requested_backend: Optional[str],
    provider_chain: List[str],
) -> str:
    if not provider_chain:
        return "rules_only"
    primary = provider_chain[0]
    normalized_backend = _normalize_evolution_llm_backend(requested_backend)
    if normalized_backend in REAL_LLM_PROVIDERS:
        if primary == normalized_backend:
            return f"requested_{primary}_healthy"
        if _provider_health_state(normalized_backend) != "healthy":
            return f"requested_{normalized_backend}_cooldown"
        return f"requested_{normalized_backend}_fallback_to_{primary}"
    if normalized_backend in (None, AUTO_MULTI_PROVIDER_BACKEND):
        if primary == "openai":
            return "default_openai_primary"
        if _provider_health_state("openai") != "healthy":
            return "openai_cooldown_promoted_gemini"
        if _provider_quality_state("openai") != "stable":
            return "openai_quality_degraded_promoted_gemini"
        if _should_promote_provider_by_quality("openai", "gemini"):
            return "openai_low_quality_score_promoted_gemini"
        if _provider_priority_key("gemini") > _provider_priority_key("openai"):
            return "openai_thin_pool_promoted_gemini"
        return f"auto_selected_{primary}"
    return f"auto_selected_{primary}"


def get_evolution_llm_provider_chain() -> List[str]:
    """
    计算当前进化增强的 provider 编排链。

    规则：
    - rules => 空链
    - openai/gemini => 该 provider 为主；若另一 provider 已配置，则作为 fallback
    - auto/未指定 => 按 openai -> gemini 的优先顺序启用所有已配置 provider
    - 显式请求的 provider 若未配置，不阻断；会自动回退到其他已配置 provider
    """
    _sync_provider_failures_from_runtime_state()
    _sync_provider_quality_from_runtime_state()
    _sync_provider_review_stats_from_runtime_state()
    requested_backend = _get_requested_evolution_llm_backend()
    normalized_backend = _normalize_evolution_llm_backend(requested_backend)
    configured = [provider for provider in REAL_LLM_PROVIDERS if _provider_configured(provider)]
    if normalized_backend == "rules":
        return []
    if normalized_backend in REAL_LLM_PROVIDERS:
        ordered = [normalized_backend] + [
            provider for provider in configured if provider != normalized_backend
        ]
        return _order_provider_chain(
            [provider for provider in ordered if _provider_configured(provider)],
            requested_provider=normalized_backend,
        )
    if normalized_backend == AUTO_MULTI_PROVIDER_BACKEND or normalized_backend is None:
        return _order_provider_chain(configured)
    return _order_provider_chain(configured)


def get_evolution_llm_backend() -> str:
    """返回当前实际生效的主后端；若都不可用则回退为 rules。"""
    chain = get_evolution_llm_provider_chain()
    if chain:
        return chain[0]
    return "rules"


def get_llm_backend_status() -> Dict[str, Any]:
    """返回各 LLM 后端的配置状态，便于运维与界面展示（不暴露密钥）。"""
    _sync_provider_failures_from_runtime_state()
    _sync_provider_quality_from_runtime_state()
    _sync_provider_review_stats_from_runtime_state()
    requested_backend = _get_requested_evolution_llm_backend()
    backend = get_evolution_llm_backend()
    provider_chain = get_evolution_llm_provider_chain()
    legacy_spark_env_keys = _list_legacy_spark_env_keys()
    openai_configured = _provider_configured("openai")
    gemini_configured = _provider_configured("gemini")
    openai_pool_health = get_openai_evolution_pool_health() if openai_configured else {}
    openai_pool_quality = get_openai_evolution_pool_quality() if openai_configured else {}
    gemini_pool_health = get_gemini_evolution_pool_health() if gemini_configured else {}
    gemini_pool_quality = get_gemini_evolution_pool_quality() if gemini_configured else {}
    provider_review_stats = get_provider_review_stats()
    return {
        "evolution_backend": backend,
        "requested_backend": requested_backend,
        "backend_alias_applied": bool(requested_backend == LEGACY_SPARK_BACKEND_ALIAS),
        "auto_mode": _normalize_evolution_llm_backend(requested_backend)
        in (None, AUTO_MULTI_PROVIDER_BACKEND),
        "spark_configured": bool(legacy_spark_env_keys),
        "legacy_spark_env_keys": legacy_spark_env_keys,
        "openai_configured": openai_configured,
        "openai_account_count": _provider_key_count("openai"),
        "openai_pool_health": openai_pool_health,
        "openai_pool_quality": openai_pool_quality,
        "openai_model": get_openai_model() if openai_configured else None,
        "gemini_configured": gemini_configured,
        "gemini_account_count": _provider_key_count("gemini"),
        "gemini_pool_health": gemini_pool_health,
        "gemini_pool_quality": gemini_pool_quality,
        "provider_health": {
            provider: _provider_health_state(provider)
            for provider in REAL_LLM_PROVIDERS
            if _provider_configured(provider)
        },
        "provider_quality": {
            provider: _provider_quality_state(provider)
            for provider in REAL_LLM_PROVIDERS
            if _provider_configured(provider)
        },
        "provider_review_stats": {
            provider: dict(provider_review_stats.get(provider) or {})
            for provider in REAL_LLM_PROVIDERS
            if _provider_configured(provider)
        },
        "provider_quality_score": {
            provider: _provider_quality_score(provider)
            for provider in REAL_LLM_PROVIDERS
            if _provider_configured(provider)
        },
        "primary_provider_reason": _provider_selection_reason(requested_backend, provider_chain),
        "provider_chain": provider_chain,
        "fallback_providers": provider_chain[1:],
    }


def enhance_evolution_report_with_llm(
    project_id: str,
    report: Dict[str, Any],
    ground_truth_records: List[Dict[str, Any]],
    project_context: str = "",
) -> Optional[Dict[str, Any]]:
    """
    使用配置的 LLM 后端增强进化报告（高分逻辑、编制指导）。
    若后端为 rules 或调用失败，返回 None，调用方保留规则版报告。
    对 openai/gemini 失败时自动重试一次；若配置了多 provider，则自动切换备用后端。

    Returns:
        增强后的报告 dict（含 high_score_logic, writing_guidance, enhanced_by 等），或 None
    """
    provider_chain = get_evolution_llm_provider_chain()
    if not provider_chain:
        return None

    attempts = 0
    primary_provider = provider_chain[0]
    for provider in provider_chain:
        result = _call_provider(
            provider,
            project_id=project_id,
            report=report,
            ground_truth_records=ground_truth_records,
            project_context=project_context,
        )
        attempts += 1
        if result is not None:
            _mark_provider_success(provider)
            result["enhanced_by"] = provider
            result["enhancement_provider_chain"] = list(provider_chain)
            result["enhancement_fallback_used"] = provider != primary_provider
            result["enhancement_attempts"] = attempts
            _attach_enhancement_review(
                result=result,
                provider_chain=provider_chain,
                primary_provider=primary_provider,
                actual_provider=provider,
                project_id=project_id,
                report=report,
                ground_truth_records=ground_truth_records,
                project_context=project_context,
            )
            return result
        _mark_provider_failure(provider)
        result = _call_provider(
            provider,
            project_id=project_id,
            report=report,
            ground_truth_records=ground_truth_records,
            project_context=project_context,
        )
        attempts += 1
        if result is not None:
            _mark_provider_success(provider)
            result["enhanced_by"] = provider
            result["enhancement_provider_chain"] = list(provider_chain)
            result["enhancement_fallback_used"] = provider != primary_provider
            result["enhancement_attempts"] = attempts
            _attach_enhancement_review(
                result=result,
                provider_chain=provider_chain,
                primary_provider=primary_provider,
                actual_provider=provider,
                project_id=project_id,
                report=report,
                ground_truth_records=ground_truth_records,
                project_context=project_context,
            )
            return result
        _mark_provider_failure(provider)
    return None


def _attach_enhancement_review(
    *,
    result: Dict[str, Any],
    provider_chain: List[str],
    primary_provider: str,
    actual_provider: str,
    project_id: str,
    report: Dict[str, Any],
    ground_truth_records: List[Dict[str, Any]],
    project_context: str,
) -> None:
    original_logic = list(report.get("high_score_logic") or [])
    original_guidance = list(report.get("writing_guidance") or [])
    result["enhancement_applied"] = True
    result["enhancement_governed"] = False
    result["enhancement_governance_notes"] = []
    result["enhancement_review_provider"] = None
    result["enhancement_review_status"] = "not_run"
    result["enhancement_review_similarity"] = None
    result["enhancement_review_notes"] = []
    if len(provider_chain) < 2:
        return
    if actual_provider != primary_provider:
        record_provider_review_outcome(actual_provider, "fallback_only", time.time())
        result["enhancement_review_status"] = "fallback_only"
        result["enhancement_review_notes"] = [
            "主 provider 已失败并切换到备用 provider，本次跳过备用复核。"
        ]
        return
    review_provider = next((item for item in provider_chain if item != actual_provider), None)
    if not review_provider:
        return
    result["enhancement_review_provider"] = review_provider
    review = _call_provider(
        review_provider,
        project_id=project_id,
        report=report,
        ground_truth_records=ground_truth_records,
        project_context=project_context,
    )
    if review is None:
        _mark_provider_failure(review_provider)
        record_provider_review_outcome(actual_provider, "unavailable", time.time())
        result["enhancement_review_status"] = "unavailable"
        result["enhancement_review_notes"] = [f"{review_provider} 复核未返回有效结果，保留主结果。"]
        return
    _mark_provider_success(review_provider)
    similarity = _compare_enhancement_similarity(result, review)
    threshold = DEFAULT_ENHANCEMENT_REVIEW_SIMILARITY_THRESHOLD
    result["enhancement_review_similarity"] = similarity
    if similarity >= threshold:
        _mark_provider_quality_stable(actual_provider)
        record_provider_review_outcome(actual_provider, "confirmed", time.time())
        result["enhancement_review_status"] = "confirmed"
        result["enhancement_review_notes"] = [
            f"{review_provider} 复核通过，结果相似度 {similarity:.2f}。"
        ]
        return
    _mark_provider_quality_degraded(actual_provider)
    record_provider_review_outcome(actual_provider, "diverged", time.time())
    result["enhancement_review_status"] = "diverged"
    result["enhancement_review_notes"] = [
        f"{review_provider} 复核与主结果差异较大，相似度 {similarity:.2f}；建议人工复核高分逻辑与编制指导。"
    ]
    result["high_score_logic"] = original_logic
    result["writing_guidance"] = original_guidance
    result["enhancement_applied"] = False
    result["enhancement_governed"] = True
    result["enhancement_governance_notes"] = [
        "主 provider 增强结果与备用 provider 复核分歧过大，已自动回退到规则版高分逻辑与编制指导。"
    ]


def _compare_enhancement_similarity(primary: Dict[str, Any], review: Dict[str, Any]) -> float:
    logic_similarity = _list_similarity(
        primary.get("high_score_logic") or [],
        review.get("high_score_logic") or [],
    )
    guidance_similarity = _list_similarity(
        primary.get("writing_guidance") or [],
        review.get("writing_guidance") or [],
    )
    return round((logic_similarity + guidance_similarity) / 2.0, 4)


def _list_similarity(left: List[str], right: List[str]) -> float:
    left_norm = [_normalize_review_text(item) for item in left if _normalize_review_text(item)]
    right_norm = [_normalize_review_text(item) for item in right if _normalize_review_text(item)]
    if not left_norm and not right_norm:
        return 1.0
    if not left_norm or not right_norm:
        return 0.0
    left_tokens = set().union(*[_tokenize_review_text(item) for item in left_norm])
    right_tokens = set().union(*[_tokenize_review_text(item) for item in right_norm])
    if not left_tokens and not right_tokens:
        return 1.0
    if not left_tokens or not right_tokens:
        return 0.0
    intersection = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens)
    return round(intersection / union, 4) if union else 0.0


def _normalize_review_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _tokenize_review_text(text: str) -> set[str]:
    normalized = _normalize_review_text(text)
    compact = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", normalized)
    return {token for token in compact.split() if token}


def _call_provider(
    provider: str,
    *,
    project_id: str,
    report: Dict[str, Any],
    ground_truth_records: List[Dict[str, Any]],
    project_context: str,
) -> Optional[Dict[str, Any]]:
    if provider == "openai":
        return _enhance_with_openai(project_id, report, ground_truth_records, project_context)
    if provider == "gemini":
        return _enhance_with_gemini(project_id, report, ground_truth_records, project_context)
    return None


def _enhance_with_spark(
    project_id: str,
    report: Dict[str, Any],
    ground_truth_records: List[Dict[str, Any]],
    project_context: str,
) -> Optional[Dict[str, Any]]:
    """历史 spark 兼容入口：当前统一委托 OpenAI 后端。"""
    return _enhance_with_openai(project_id, report, ground_truth_records, project_context)


def _enhance_with_openai(
    project_id: str,
    report: Dict[str, Any],
    ground_truth_records: List[Dict[str, Any]],
    project_context: str,
) -> Optional[Dict[str, Any]]:
    """OpenAI 后端增强。未实现时返回 None。"""
    try:
        from app.engine.llm_evolution_openai import enhance_evolution_report_openai

        return enhance_evolution_report_openai(
            project_id, report, ground_truth_records, project_context
        )
    except Exception:
        return None


def _enhance_with_gemini(
    project_id: str,
    report: Dict[str, Any],
    ground_truth_records: List[Dict[str, Any]],
    project_context: str,
) -> Optional[Dict[str, Any]]:
    """Gemini 后端增强。未实现时返回 None。"""
    try:
        from app.engine.llm_evolution_gemini import enhance_evolution_report_gemini

        return enhance_evolution_report_gemini(
            project_id, report, ground_truth_records, project_context
        )
    except Exception:
        return None
