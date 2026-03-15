"""
历史 spark 兼容模块：保留旧入口名，但当前实际调用 OpenAI 兼容层。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.engine.llm_evolution_common import build_evolution_prompt, parse_evolution_response
from app.engine.llm_judge_spark import (
    _call_spark_http,
    _get_spark_bearer_token,
    _get_spark_model,
)


def enhance_evolution_report_spark(
    project_id: str,
    report: Dict[str, Any],
    ground_truth_records: List[Dict[str, Any]],
    project_context: str = "",
) -> Optional[Dict[str, Any]]:
    """
    历史 spark 兼容入口。成功时返回完整报告 dict；审计字段 enhanced_by 会记录真实 provider。
    """
    if not _get_spark_bearer_token():
        return None
    prompt = build_evolution_prompt(report, ground_truth_records, project_context)
    ok, parsed, _ = _call_spark_http(prompt, model=_get_spark_model(), max_tokens=4096)
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
