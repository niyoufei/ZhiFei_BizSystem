"""
进化报告 LLM 增强：根据 EVOLUTION_LLM_BACKEND 调用不同 AI 后端，
对规则版进化报告进行增强，生成更丰富的高分逻辑与编制指导。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

# 支持的后端: rules | spark | openai | gemini | ollama
EVOLUTION_LLM_BACKEND_ENV = "EVOLUTION_LLM_BACKEND"


def get_evolution_llm_backend() -> str:
    """从环境变量读取进化 LLM 后端，默认 rules（仅规则，不调 API）。"""
    return (os.environ.get(EVOLUTION_LLM_BACKEND_ENV) or "rules").strip().lower()


def get_llm_backend_status() -> Dict[str, Any]:
    """返回各 LLM 后端的配置状态，便于运维与界面展示（不暴露密钥）。"""
    backend = get_evolution_llm_backend()
    spark_configured = bool((os.getenv("SPARK_APIPASSWORD") or "").strip())
    openai_configured = bool((os.getenv("OPENAI_API_KEY") or "").strip())
    gemini_configured = bool((os.getenv("GEMINI_API_KEY") or "").strip())
    ollama_configured = bool((os.getenv("OLLAMA_MODEL") or "").strip())
    return {
        "evolution_backend": backend,
        "spark_configured": spark_configured,
        "openai_configured": openai_configured,
        "gemini_configured": gemini_configured,
        "ollama_configured": ollama_configured,
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
    对 spark/openai/gemini/ollama 失败时自动重试一次，提高鲁棒性。

    Returns:
        增强后的报告 dict（含 high_score_logic, writing_guidance, enhanced_by 等），或 None
    """
    backend = get_evolution_llm_backend()
    if backend == "rules":
        return None

    def _call() -> Optional[Dict[str, Any]]:
        if backend == "spark":
            return _enhance_with_spark(project_id, report, ground_truth_records, project_context)
        if backend == "openai":
            return _enhance_with_openai(project_id, report, ground_truth_records, project_context)
        if backend == "gemini":
            return _enhance_with_gemini(project_id, report, ground_truth_records, project_context)
        if backend == "ollama":
            return _enhance_with_ollama(project_id, report, ground_truth_records, project_context)
        return None

    result = _call()
    if result is None and backend in ("spark", "openai", "gemini", "ollama"):
        result = _call()  # 一次重试
    return result


def _enhance_with_spark(
    project_id: str,
    report: Dict[str, Any],
    ground_truth_records: List[Dict[str, Any]],
    project_context: str,
) -> Optional[Dict[str, Any]]:
    """星火后端增强。未实现时返回 None。"""
    try:
        from app.engine.llm_evolution_spark import enhance_evolution_report_spark

        return enhance_evolution_report_spark(
            project_id, report, ground_truth_records, project_context
        )
    except Exception:
        return None


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


def _enhance_with_ollama(
    project_id: str,
    report: Dict[str, Any],
    ground_truth_records: List[Dict[str, Any]],
    project_context: str,
) -> Optional[Dict[str, Any]]:
    """Ollama 后端增强。未配置、调用失败或模块不可用时返回 None。"""
    try:
        from app.engine.llm_evolution_ollama import enhance_evolution_report_ollama

        return enhance_evolution_report_ollama(
            project_id, report, ground_truth_records, project_context
        )
    except Exception:
        return None
