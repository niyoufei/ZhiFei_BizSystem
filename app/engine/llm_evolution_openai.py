"""
进化报告 OpenAI 增强：使用 OpenAI GPT-5.4 在规则版报告基础上生成高分逻辑与编制指导。
配置 OPENAI_API_KEY 后，将 EVOLUTION_LLM_BACKEND=openai 即可启用。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.engine.llm_evolution_common import build_evolution_prompt, parse_evolution_response
from app.engine.openai_compat import call_openai_json, get_openai_api_key, get_openai_model


def _call_openai_http(
    user_message: str,
    model: str | None = None,
    max_tokens: int = 4096,
    timeout: int = 90,
) -> tuple[bool, Optional[Dict[str, Any]], str]:
    """调用 OpenAI Chat Completions，返回 (成功, 解析后的 JSON 或 None, 错误信息)。"""
    return call_openai_json(
        user_message,
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
    if not get_openai_api_key():
        return None
    prompt = build_evolution_prompt(report, ground_truth_records, project_context)
    ok, parsed, _ = _call_openai_http(prompt, max_tokens=4096)
    if not ok or not parsed:
        return None
    enhanced = parse_evolution_response(parsed)
    if not enhanced:
        return None
    return {
        "project_id": project_id,
        "high_score_logic": enhanced["high_score_logic"],
        "writing_guidance": enhanced["writing_guidance"],
        "sample_count": report.get("sample_count", 0),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "enhanced_by": "openai",
    }
