from __future__ import annotations

import json
import os
import re
from base64 import b64encode
from typing import Any, Dict, Optional, Tuple

OPENAI_HTTP_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_DEFAULT_MODEL = "gpt-5.4"

_OPENAI_MODEL_ALIASES = {
    "chatgpt5.4": "gpt-5.4",
    "chatgpt-5.4": "gpt-5.4",
    "chatgpt54": "gpt-5.4",
    "chatgpt5": "gpt-5.4",
    "chatgpt-5": "gpt-5.4",
    "gpt5.4": "gpt-5.4",
    "gpt-5.4": "gpt-5.4",
    "gpt5": "gpt-5.4",
    "gpt-5": "gpt-5.4",
}


def resolve_openai_model(model_name: Optional[str]) -> str:
    raw = str(model_name or "").strip()
    if not raw:
        return OPENAI_DEFAULT_MODEL
    return _OPENAI_MODEL_ALIASES.get(raw.lower(), raw)


def get_openai_model() -> str:
    return resolve_openai_model(os.getenv("OPENAI_MODEL"))


def get_openai_api_key() -> Optional[str]:
    return (os.getenv("OPENAI_API_KEY") or "").strip() or None


def extract_json_from_content(content: str) -> Optional[Dict[str, Any]]:
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


def call_openai_json(
    user_message: str,
    *,
    model: str | None = None,
    max_tokens: int = 4096,
    timeout: int = 120,
    temperature: float = 0.3,
) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    key = get_openai_api_key()
    if not key:
        return False, None, "missing_credentials"
    try:
        import urllib.request

        resolved_model = resolve_openai_model(model)
        body = json.dumps(
            {
                "model": resolved_model,
                "messages": [{"role": "user", "content": user_message}],
                "temperature": temperature,
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
    except Exception as exc:
        return False, None, str(exc)

    choices = data.get("choices")
    if not choices or not isinstance(choices, list):
        return False, None, "invalid_response_no_choices"
    msg = choices[0].get("message") if choices else {}
    content = (msg.get("content") or "").strip()
    if not content:
        return False, None, "empty_content"
    parsed = extract_json_from_content(content)
    if parsed is None:
        return False, None, "json_parse_failed"
    return True, parsed, ""


def call_openai_multimodal_json(
    user_message: str,
    *,
    image_bytes: bytes | None = None,
    image_mime: str = "image/png",
    model: str | None = None,
    max_tokens: int = 4096,
    timeout: int = 120,
    temperature: float = 0.2,
) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    key = get_openai_api_key()
    if not key:
        return False, None, "missing_credentials"
    try:
        import urllib.request

        resolved_model = resolve_openai_model(model)
        content: list[Dict[str, Any]] = [{"type": "text", "text": user_message}]
        if image_bytes:
            encoded = b64encode(image_bytes).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{image_mime};base64,{encoded}"},
                }
            )
        body = json.dumps(
            {
                "model": resolved_model,
                "messages": [{"role": "user", "content": content}],
                "temperature": temperature,
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
    except Exception as exc:
        return False, None, str(exc)

    choices = data.get("choices")
    if not choices or not isinstance(choices, list):
        return False, None, "invalid_response_no_choices"
    msg = choices[0].get("message") if choices else {}
    raw_content = msg.get("content")
    if isinstance(raw_content, list):
        parts: list[str] = []
        for item in raw_content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
        content_text = "\n".join(x for x in parts if x).strip()
    else:
        content_text = str(raw_content or "").strip()
    if not content_text:
        return False, None, "empty_content"
    parsed = extract_json_from_content(content_text)
    if parsed is None:
        return False, None, "json_parse_failed"
    return True, parsed, ""
