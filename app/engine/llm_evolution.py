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
from typing import Any, Dict, List, Optional, Tuple

from app.engine.llm_evolution_common import parse_api_key_pool
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


def get_evolution_llm_provider_chain() -> List[str]:
    """
    计算当前进化增强的 provider 编排链。

    规则：
    - rules => 空链
    - openai/gemini => 该 provider 为主；若另一 provider 已配置，则作为 fallback
    - auto/未指定 => 按 openai -> gemini 的优先顺序启用所有已配置 provider
    - 显式请求的 provider 若未配置，不阻断；会自动回退到其他已配置 provider
    """
    requested_backend = _get_requested_evolution_llm_backend()
    normalized_backend = _normalize_evolution_llm_backend(requested_backend)
    configured = [provider for provider in REAL_LLM_PROVIDERS if _provider_configured(provider)]
    if normalized_backend == "rules":
        return []
    if normalized_backend in REAL_LLM_PROVIDERS:
        ordered = [normalized_backend] + [
            provider for provider in configured if provider != normalized_backend
        ]
        return _unique_provider_chain(
            [provider for provider in ordered if _provider_configured(provider)]
        )
    if normalized_backend == AUTO_MULTI_PROVIDER_BACKEND or normalized_backend is None:
        return configured
    return configured


def get_evolution_llm_backend() -> str:
    """返回当前实际生效的主后端；若都不可用则回退为 rules。"""
    chain = get_evolution_llm_provider_chain()
    if chain:
        return chain[0]
    return "rules"


def get_llm_backend_status() -> Dict[str, Any]:
    """返回各 LLM 后端的配置状态，便于运维与界面展示（不暴露密钥）。"""
    requested_backend = _get_requested_evolution_llm_backend()
    backend = get_evolution_llm_backend()
    provider_chain = get_evolution_llm_provider_chain()
    legacy_spark_env_keys = _list_legacy_spark_env_keys()
    openai_configured = _provider_configured("openai")
    gemini_configured = _provider_configured("gemini")
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
        "openai_model": get_openai_model() if openai_configured else None,
        "gemini_configured": gemini_configured,
        "gemini_account_count": _provider_key_count("gemini"),
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
        result = _call_provider(
            provider,
            project_id=project_id,
            report=report,
            ground_truth_records=ground_truth_records,
            project_context=project_context,
        )
        attempts += 1
        if result is not None:
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
        result["enhancement_review_status"] = "unavailable"
        result["enhancement_review_notes"] = [f"{review_provider} 复核未返回有效结果，保留主结果。"]
        return
    similarity = _compare_enhancement_similarity(result, review)
    threshold = DEFAULT_ENHANCEMENT_REVIEW_SIMILARITY_THRESHOLD
    result["enhancement_review_similarity"] = similarity
    if similarity >= threshold:
        result["enhancement_review_status"] = "confirmed"
        result["enhancement_review_notes"] = [
            f"{review_provider} 复核通过，结果相似度 {similarity:.2f}。"
        ]
        return
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
