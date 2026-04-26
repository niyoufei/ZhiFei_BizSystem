"""
进化报告 Ollama 增强客户端：为后续本地 Ollama 后端接入提供独立封装。

当前模块不接入 app.engine.llm_evolution 的后端选择主流程；只有显式调用本模块函数时
才会尝试访问配置的 Ollama HTTP 服务。
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.engine.llm_evolution_common import build_evolution_prompt, parse_evolution_response

OLLAMA_DEFAULT_BASE_URL = "http://localhost:11434"
OLLAMA_BASE_URL_ENV = "OLLAMA_BASE_URL"
OLLAMA_MODEL_ENV = "OLLAMA_MODEL"


def _get_ollama_base_url() -> str:
    """读取 Ollama 服务地址；未配置时使用本机默认地址。"""
    return (os.getenv(OLLAMA_BASE_URL_ENV) or "").strip() or OLLAMA_DEFAULT_BASE_URL


def _get_ollama_model() -> Optional[str]:
    """读取 Ollama 模型名。未显式配置时不调用，避免误触发本地服务。"""
    return (os.getenv(OLLAMA_MODEL_ENV) or "").strip() or None


def _build_ollama_chat_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/api/chat"


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


def _call_ollama_http(
    user_message: str,
    model: str | None = None,
    max_tokens: int = 4096,
    timeout: int = 90,
    base_url: str | None = None,
) -> tuple[bool, Optional[Dict[str, Any]], str]:
    """调用 Ollama /api/chat，返回 (成功, 解析后的 JSON 或 None, 错误信息)。"""
    if model is None:
        model = _get_ollama_model()
    if not model:
        return False, None, "missing_model"
    if base_url is None:
        base_url = _get_ollama_base_url()
    try:
        import urllib.request

        body = json.dumps(
            {
                "model": model,
                "messages": [{"role": "user", "content": user_message}],
                "stream": False,
                "think": False,
                "options": {
                    "temperature": 0.3,
                    "num_predict": max_tokens,
                },
            },
            ensure_ascii=False,
        ).encode("utf-8")
        req = urllib.request.Request(
            _build_ollama_chat_url(base_url),
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return False, None, str(e)
    message = data.get("message") if isinstance(data, dict) else {}
    content = ""
    thinking = ""
    if isinstance(message, dict):
        content = str(message.get("content") or "").strip()
        thinking = str(message.get("thinking") or "").strip()
    if not content and isinstance(data, dict):
        content = str(data.get("response") or "").strip()
    if not content:
        if thinking:
            return False, None, "empty_content_thinking_only"
        return False, None, "empty_content"
    parsed = _extract_json_from_content(content)
    if parsed is None:
        return False, None, "json_parse_failed"
    return True, parsed, ""


def enhance_evolution_report_ollama(
    project_id: str,
    report: Dict[str, Any],
    ground_truth_records: List[Dict[str, Any]],
    project_context: str = "",
) -> Optional[Dict[str, Any]]:
    """
    使用 Ollama 增强进化报告。成功时返回完整报告 dict（含 enhanced_by）。
    未配置 OLLAMA_MODEL 或调用失败时返回 None，调用方保留规则版报告。
    """
    if not _get_ollama_model():
        return None
    prompt = build_evolution_prompt(report, ground_truth_records, project_context)
    ok, parsed, _ = _call_ollama_http(prompt, max_tokens=4096)
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
        "enhanced_by": "ollama",
    }
