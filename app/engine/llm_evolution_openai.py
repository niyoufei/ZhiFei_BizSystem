"""
进化报告 OpenAI 增强：使用 OpenAI API 在规则版报告基础上生成高分逻辑与编制指导。
配置 OPENAI_API_KEY 后，将 EVOLUTION_LLM_BACKEND=openai 即可启用。
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.engine.llm_evolution_common import build_evolution_prompt, parse_evolution_response

OPENAI_HTTP_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_DEFAULT_MODEL = "gpt-4o"


def _get_openai_model() -> str:
    """进化用模型，默认最高端 gpt-4o；可通过 OPENAI_MODEL 覆盖。"""
    return (os.getenv("OPENAI_MODEL") or "").strip() or OPENAI_DEFAULT_MODEL


def _get_openai_api_key() -> Optional[str]:
    return (os.getenv("OPENAI_API_KEY") or "").strip() or None


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


def _call_openai_http(
    user_message: str,
    model: str | None = None,
    max_tokens: int = 4096,
    timeout: int = 90,
) -> tuple[bool, Optional[Dict[str, Any]], str]:
    """调用 OpenAI Chat Completions，返回 (成功, 解析后的 JSON 或 None, 错误信息)。"""
    key = _get_openai_api_key()
    if model is None:
        model = _get_openai_model()
    if not key:
        return False, None, "missing_credentials"
    try:
        import urllib.request

        body = json.dumps(
            {
                "model": model,
                "messages": [{"role": "user", "content": user_message}],
                "temperature": 0.3,
                "max_tokens": max_tokens,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        req = urllib.request.Request(
            OPENAI_HTTP_URL,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return False, None, str(e)
    choices = data.get("choices")
    if not choices or not isinstance(choices, list):
        return False, None, "invalid_response_no_choices"
    msg = choices[0].get("message") if choices else {}
    content = (msg.get("content") or "").strip()
    if not content:
        return False, None, "empty_content"
    parsed = _extract_json_from_content(content)
    if parsed is None:
        return False, None, "json_parse_failed"
    return True, parsed, ""


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
    if not _get_openai_api_key():
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
