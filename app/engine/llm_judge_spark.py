from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

from app.engine.dimensions import DIMENSIONS
from app.engine.feature_distillation import select_top_few_shot_prompt_examples
from app.engine.openai_compat import call_openai_json, get_openai_api_key, get_openai_model
from app.schemas import ScoreReport

# 兼容保留：历史文件名/函数名不变，但实际 provider 已切到 OpenAI GPT-5.4。
SPARK_DEFAULT_MODEL = "gpt-5.4"
LEGACY_SPARK_ENV_KEYS = ("SPARK_APIPASSWORD", "SPARK_APP_ID", "SPARK_API_KEY", "SPARK_API_SECRET")
LLM_JUDGE_MAX_ATTEMPTS = 3
LLM_JUDGE_INITIAL_BACKOFF_SECONDS = 1.0
LLM_JUDGE_INTERRUPT_MESSAGE = "计算中断异常，请重试"
_DIRECTORY_LINE_RE = re.compile(r"[.．·•\-\s]{4,}")
_PAGE_HINT_RE = re.compile(r"\[PAGE:(\d+)\]|\[PAGE_SECTION_HINTS:(\d+)\]|第\s*(\d+)\s*页")
_RETRYABLE_ERROR_MARKERS = (
    "timeout",
    "timed out",
    "time out",
    "json_parse_failed",
    "empty_content",
    "invalid_response_no_choices",
    "truncated",
    "truncate",
    "context length",
    "max_tokens",
    "token",
    "rate limit",
    "429",
    "500",
    "502",
    "503",
    "504",
    "temporarily unavailable",
    "connection reset",
    "remote end closed",
)
_GENERIC_SCORING_PHRASES = (
    "由项目经理牵头",
    "项目经理牵头",
    "按每周1次",
    "按每周一次",
    "建议由项目经理牵头",
    "由项目部牵头",
)

logger = logging.getLogger(__name__)


def _get_spark_model() -> str:
    """
    历史兼容入口。
    优先读取 OPENAI_MODEL；若用户仍设置 SPARK_MODEL，则将其视为别名并透传到 OpenAI 模型解析器。
    默认切到 gpt-5.4。
    """
    return (
        get_openai_model()
        if os.getenv("OPENAI_MODEL")
        else ((os.getenv("SPARK_MODEL") or "").strip() or SPARK_DEFAULT_MODEL)
    )


def _get_spark_bearer_token() -> str | None:
    """历史兼容入口，实际仅读取 OPENAI_API_KEY。"""
    return get_openai_api_key()


def _list_legacy_spark_env_keys() -> List[str]:
    return [key for key in LEGACY_SPARK_ENV_KEYS if str(os.getenv(key) or "").strip()]


def load_prompt(prompt_name: str) -> str:
    base = Path(__file__).resolve().parents[1]
    prompt_path = base / "resources" / "prompts" / f"{prompt_name}.txt"
    return prompt_path.read_text(encoding="utf-8")


def validate_llm_judge_json(payload: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    errors: List[str] = []
    required_top = [
        "judge_mode",
        "model",
        "prompt_version",
        "weights",
        "overall",
        "logic_lock",
        "dimension_scores",
        "penalties",
    ]
    for key in required_top:
        if key not in payload:
            errors.append(f"missing_field:{key}")

    dim_scores = payload.get("dimension_scores", {})
    for dim_id in [f"{i:02d}" for i in range(1, 17)]:
        if dim_id not in dim_scores:
            errors.append(f"missing_dimension:{dim_id}")

    weights = payload.get("weights", {})
    for key in ["high_priority_dims", "high_priority_multiplier", "normal_multiplier"]:
        if key not in weights:
            errors.append(f"missing_weights:{key}")

    for dim_id, dim in dim_scores.items():
        for key in [
            "id",
            "name",
            "module",
            "score_0_10",
            "max_score_0_10",
            "weight_multiplier",
            "definition_points",
            "defects",
            "improvements",
            "evidence",
        ]:
            if key not in dim:
                errors.append(f"missing_dim_field:{dim_id}:{key}")
        evidence = dim.get("evidence", [])
        if not isinstance(evidence, list) or len(evidence) == 0:
            errors.append(f"invalid_evidence_type:{dim_id}")
        elif not all(_is_valid_evidence_item(item) for item in evidence):
            errors.append(f"invalid_evidence_item:{dim_id}")

    for must in ["07", "09", "02", "03"]:
        dim = dim_scores.get(must, {})
        if "weight_multiplier" not in dim:
            errors.append(f"missing_weight_multiplier:{must}")

    penalties = payload.get("penalties", [])
    if not isinstance(penalties, list):
        errors.append("invalid_penalties_type")
    else:
        for idx, penalty in enumerate(penalties):
            if not isinstance(penalty, dict):
                errors.append(f"invalid_penalty:{idx}")
                continue
            evidence = penalty.get("evidence")
            if not _is_valid_evidence_item(evidence):
                errors.append(f"invalid_penalty_evidence:{idx}")

    if errors:
        return False, {"error": "llm_judge_json_invalid", "details": errors}
    return True, {}


def _is_valid_evidence_item(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    snippet = str(item.get("snippet") or "").strip()
    quote = str(item.get("quote") or "").strip()
    anchor_label = str(item.get("anchor_label") or "").strip()
    return bool(snippet and quote and anchor_label)


def _extract_page_hint(text: Any) -> str:
    raw = str(text or "")
    matches = _PAGE_HINT_RE.findall(raw)
    if not matches:
        return ""
    for page_a, page_b, page_c in reversed(matches):
        page_no = str(page_a or page_b or page_c).strip()
        if page_no:
            return f"第{page_no}页"
    return ""


def _looks_like_directory_fragment(text: Any) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    if "........" in value or "……" in value:
        return True
    if (
        _DIRECTORY_LINE_RE.search(value)
        and len(value.replace(".", "").replace("．", "").strip()) < 16
    ):
        return True
    return False


def _normalize_anchor_label(anchor_label: Any, *, snippet: str, quote: str) -> str:
    anchor = str(anchor_label or "").strip() or "正文片段"
    page_hint = (
        _extract_page_hint(anchor) or _extract_page_hint(snippet) or _extract_page_hint(quote)
    )
    if page_hint and page_hint not in anchor:
        anchor = f"{page_hint}｜{anchor}"
    return anchor


def _sanitize_scoring_text(text: Any, *, fallback: str) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized:
        return fallback
    for phrase in _GENERIC_SCORING_PHRASES:
        if normalized.startswith(phrase):
            trimmed = normalized[len(phrase) :].lstrip("，,：:；; ")
            if len(trimmed) >= 10:
                normalized = trimmed
                break
    return normalized or fallback


def _build_few_shot_prompt_context(top_k: int = 4) -> str:
    rows = select_top_few_shot_prompt_examples(
        dimension_ids=list(DIMENSIONS.keys()),
        top_k=max(1, int(top_k or 4)),
    )
    if not rows:
        return ""
    lines = ["已采纳高分少样本逻辑骨架（仅用于评分一致性参考，仍必须以输入原文为唯一评分依据）："]
    for item in rows:
        if not isinstance(item, dict):
            continue
        dim_name = str(item.get("dimension_name") or item.get("dimension_id") or "").strip()
        logic = [str(x).strip() for x in (item.get("logic_skeleton") or []) if str(x).strip()]
        highlights = [
            str(x).strip() for x in (item.get("source_highlights") or []) if str(x).strip()
        ]
        if not dim_name or not logic:
            continue
        line = f"- {dim_name}：{'；'.join(logic[:2])}"
        if highlights:
            line += f"｜高分信号：{'；'.join(highlights[:2])}"
        lines.append(line)
    return "\n".join(lines)


def _normalize_evidence_item(item: Any) -> Dict[str, Any]:
    if not isinstance(item, dict):
        return _placeholder_evidence()
    snippet = str(item.get("snippet") or item.get("quote") or "").strip()
    if not snippet:
        return _placeholder_evidence()
    try:
        start_index = int(item.get("start_index") or 0)
    except (TypeError, ValueError):
        start_index = 0
    try:
        end_index = int(item.get("end_index") or 0)
    except (TypeError, ValueError):
        end_index = 0
    quote = str(item.get("quote") or snippet).strip()
    if _looks_like_directory_fragment(snippet):
        return _placeholder_evidence()
    anchor_label = _normalize_anchor_label(
        item.get("anchor_label"),
        snippet=snippet,
        quote=quote,
    )
    return {
        "start_index": max(0, start_index),
        "end_index": max(0, end_index),
        "snippet": snippet,
        "anchor_label": anchor_label,
        "quote": quote,
    }


def post_process_llm_output(payload: Dict[str, Any], rubric: Dict[str, Any]) -> Dict[str, Any]:
    weight_profile = rubric.get("llm_weight_profile", {})
    high_dims = set(weight_profile.get("high_priority_dims", []))
    high_mul = float(weight_profile.get("high_priority_multiplier", 1.4))
    normal_mul = float(weight_profile.get("normal_multiplier", 1.0))

    dim_scores = payload.get("dimension_scores", {})
    for dim_id, dim in dim_scores.items():
        default_used = False

        def_points = dim.get("definition_points") or []
        if not isinstance(def_points, list) or len(def_points) == 0:
            dim["definition_points"] = ["未在文本中提取到明确的定义要点。"]
        else:
            dim["definition_points"] = [
                _sanitize_scoring_text(item, fallback="未在文本中提取到明确的定义要点。")
                for item in def_points
                if str(item or "").strip()
            ] or ["未在文本中提取到明确的定义要点。"]

        defects = dim.get("defects") or []
        if not isinstance(defects, list) or len(defects) == 0:
            defects = [
                "文本对本维度的关键要素表述不足，存在参数/频次/验收/责任等落实要素缺失风险。"
            ]
            default_used = True
        else:
            defects = [
                _sanitize_scoring_text(
                    item,
                    fallback="文本对本维度的关键要素表述不足，存在参数/频次/验收/责任等落实要素缺失风险。",
                )
                for item in defects
                if str(item or "").strip()
            ]
        dim["defects"] = defects

        improvements = dim.get("improvements") or []
        if not isinstance(improvements, list) or len(improvements) == 0:
            improvements = [
                "建议补充可量化控制指标（阈值/参数）与管理频次（日报/周检），并明确责任岗位与验收闭环（报验/签认）。"
            ]
            default_used = True
        else:
            improvements = [
                _sanitize_scoring_text(
                    item,
                    fallback="建议补充可量化控制指标（阈值/参数）与管理频次（日报/周检），并明确责任岗位与验收闭环（报验/签认）。",
                )
                for item in improvements
                if str(item or "").strip()
            ]
        dim["improvements"] = improvements

        evidence_raw = dim.get("evidence") or []
        normalized_evidence = (
            [_normalize_evidence_item(item) for item in evidence_raw]
            if isinstance(evidence_raw, list) and evidence_raw
            else []
        )
        if not normalized_evidence:
            normalized_evidence = [_placeholder_evidence()]
            default_used = True
        elif any(
            str(item.get("quote") or "").strip() == _placeholder_evidence()["quote"]
            for item in normalized_evidence
        ):
            default_used = True
        if default_used and not any("证据" in str(item) for item in defects):
            defects.append("证据缺失或原文锚点不足，当前评分已按严格口径下调。")
        evidence = normalized_evidence
        dim["evidence"] = evidence

        if default_used:
            dim["score_0_10"] = min(float(dim.get("score_0_10", 0.0)), 4.0)
            dim["defects"].append("[系统提示] 输出不完整，已按严格模板自动补全并封顶评分。")

        dim["weight_multiplier"] = high_mul if dim_id in high_dims else normal_mul

    payload["dimension_scores"] = dim_scores
    payload["weights"] = {
        "high_priority_dims": list(high_dims),
        "high_priority_multiplier": high_mul,
        "normal_multiplier": normal_mul,
    }
    raw_penalties = payload.get("penalties") or []
    normalized_penalties: List[Dict[str, Any]] = []
    if isinstance(raw_penalties, list):
        for idx, penalty in enumerate(raw_penalties):
            if not isinstance(penalty, dict):
                continue
            evidence = _normalize_evidence_item(penalty.get("evidence"))
            if evidence == _placeholder_evidence():
                continue
            try:
                deduct = float(penalty.get("deduct") or 0.0)
            except (TypeError, ValueError):
                deduct = 0.0
            if deduct <= 0:
                continue
            normalized_penalties.append(
                {
                    "code": str(penalty.get("code") or f"LLM_PENALTY_{idx + 1}").strip()
                    or f"LLM_PENALTY_{idx + 1}",
                    "message": _sanitize_scoring_text(
                        penalty.get("message"),
                        fallback="该扣分项未提供可核验原文证据，已从结果中移除。",
                    ),
                    "deduct": round(deduct, 2),
                    "evidence": evidence,
                }
            )
    payload["penalties"] = normalized_penalties
    return payload


def _placeholder_evidence() -> Dict[str, Any]:
    return {
        "start_index": 0,
        "end_index": 0,
        "snippet": "未在输入文本中检索到可支撑该维度的直接证据。",
        "anchor_label": "未定位锚点",
        "quote": "未在输入文本中检索到可支撑该维度的直接证据。",
    }


def _evidence_payload_from_span(span: Any, *, fallback_anchor: str) -> Dict[str, Any]:
    if span is None:
        return _placeholder_evidence()
    payload = span.model_dump() if hasattr(span, "model_dump") else dict(span)
    payload["anchor_label"] = str(payload.get("anchor_label") or "").strip() or fallback_anchor
    payload["quote"] = (
        str(payload.get("quote") or payload.get("snippet") or "").strip()
        or str(payload.get("snippet") or "").strip()
    )
    return _normalize_evidence_item(payload)


def _build_from_rules(report: ScoreReport, rubric: Dict[str, Any]) -> Dict[str, Any]:
    weight_profile = rubric.get("llm_weight_profile", {})
    high_dims = set(weight_profile.get("high_priority_dims", []))
    high_mul = float(weight_profile.get("high_priority_multiplier", 1.4))
    normal_mul = float(weight_profile.get("normal_multiplier", 1.0))

    dimension_scores: Dict[str, Any] = {}
    for dim_id, meta in DIMENSIONS.items():
        dim = report.dimension_scores[dim_id]
        evidence = [
            _evidence_payload_from_span(e, fallback_anchor=f"{meta['name']}证据片段")
            for e in dim.evidence
        ]
        defects: List[str] = []
        score = float(dim.score)
        if not evidence:
            evidence = [_placeholder_evidence()]
            defects.append("未在文本中检索到证据，表述不足。")
            score = min(score, 4.0)

        dimension_scores[dim_id] = {
            "id": dim_id,
            "name": meta["name"],
            "module": meta["module"],
            "score_0_10": round(score, 2),
            "max_score_0_10": 10,
            "weight_multiplier": high_mul if dim_id in high_dims else normal_mul,
            "definition_points": dim.hits or [],
            "defects": defects or [],
            "improvements": [],
            "evidence": evidence,
        }

    breaks = []
    for b in report.logic_lock.breaks:
        if b == "definition":
            t = "missing_definition"
        elif b == "analysis":
            t = "missing_analysis"
        else:
            t = "missing_solution"
        breaks.append({"type": t, "evidence": _placeholder_evidence()})

    payload = {
        "judge_mode": "openai",
        "model": _get_spark_model(),
        "prompt_version": "qingtian_v1",
        "weights": {
            "high_priority_dims": list(high_dims),
            "high_priority_multiplier": high_mul,
            "normal_multiplier": normal_mul,
        },
        "overall": {
            "total_score_0_100": round(report.total_score, 2),
            "overall_comment": "",
            "confidence_0_1": 0.5,
        },
        "logic_lock": {
            "definition_score_0_5": report.logic_lock.definition_score,
            "analysis_score_0_5": report.logic_lock.analysis_score,
            "solution_score_0_5": report.logic_lock.solution_score,
            "breaks": breaks,
            "evidence": [
                _evidence_payload_from_span(e, fallback_anchor="逻辑锁原文片段")
                for e in report.logic_lock.evidence
            ],
        },
        "dimension_scores": dimension_scores,
        "penalties": [
            {
                "code": p.code,
                "message": p.message,
                "deduct": p.deduct or 0.0,
                "evidence": _evidence_payload_from_span(
                    p.evidence_span,
                    fallback_anchor="扣分原文片段",
                ),
            }
            for p in report.penalties
        ],
    }
    return payload


def build_spark_payload_from_rules(report: ScoreReport, rubric: Dict[str, Any]) -> Dict[str, Any]:
    payload = _build_from_rules(report, rubric)
    return post_process_llm_output(payload, rubric)


def _extract_json_from_content(content: str) -> Dict[str, Any] | None:
    """从 LLM 返回的文本中提取 JSON 对象（允许前后有说明文字）。"""
    content = content.strip()
    # 尝试直接解析
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    # 尝试提取 ```json ... ``` 或第一个 { ... }
    for pattern in (r"```(?:json)?\s*([\s\S]*?)\s*```", r"(\{[\s\S]*\})"):
        match = re.search(pattern, content)
        if match:
            raw = match.group(1).strip() if match.lastindex else match.group(0)
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                continue
    return None


def _call_spark_http(
    user_message: str,
    model: str | None = None,
    max_tokens: int = 8192,
) -> Tuple[bool, Dict[str, Any] | None, str]:
    """
    历史兼容入口：实际调用 OpenAI GPT-5.4 HTTP 接口，非流式。
    返回 (成功, 解析后的 JSON 或 None, 错误信息)。
    """
    return call_openai_json(
        user_message,
        model=model or _get_spark_model(),
        max_tokens=max_tokens,
        timeout=120,
        temperature=0.3,
    )


def _is_retryable_llm_failure(reason: str) -> bool:
    normalized = str(reason or "").strip().lower()
    if not normalized:
        return False
    return any(marker in normalized for marker in _RETRYABLE_ERROR_MARKERS)


def _build_llm_interrupted_payload(
    *,
    reason: str,
    error_message: str,
    retry_attempts: int,
    prompt_version: str = "qingtian_v1",
    legacy_spark_env_keys: List[str] | None = None,
    migration_hint: str | None = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "called_spark_api": False,
        "called_openai_api": False,
        "judge_mode": "openai_interrupted",
        "judge_source": "openai_api",
        "processing_interrupted": True,
        "error_code": "llm_processing_interrupted",
        "message": LLM_JUDGE_INTERRUPT_MESSAGE,
        "reason": str(reason or "llm_processing_interrupted").strip()
        or "llm_processing_interrupted",
        "fallback_reason": str(error_message or "").strip() or str(reason or "").strip(),
        "retry_attempts": max(1, int(retry_attempts or 1)),
        "prompt_version": prompt_version,
    }
    if legacy_spark_env_keys:
        payload["legacy_spark_env_keys"] = legacy_spark_env_keys
    if migration_hint:
        payload["migration_hint"] = migration_hint
    return payload


def run_spark_judge(
    text: str,
    rubric: Dict[str, Any],
    prompt_name: str,
    rules_report: ScoreReport,
) -> Dict[str, Any]:
    """
    历史兼容入口：现已改为调用 OpenAI GPT-5.4。
    为避免打断旧 CLI/测试，保留函数名和 called_spark_api 标志位；
    但 judge_mode / model / judge_source 会写入 OpenAI 语义。
    """
    token = _get_spark_bearer_token()
    legacy_spark_env_keys = _list_legacy_spark_env_keys()
    if not token:
        migration_hint = None
        if legacy_spark_env_keys:
            migration_hint = (
                "当前评分兼容层已迁移到 OpenAI；请配置 OPENAI_API_KEY，"
                "旧 Spark 凭证不会再作为真实评分凭证使用。"
            )
        return _build_llm_interrupted_payload(
            reason="missing_openai_api_key",
            error_message="missing_openai_api_key",
            retry_attempts=1,
            legacy_spark_env_keys=legacy_spark_env_keys,
            migration_hint=migration_hint,
        )

    prompt_template = load_prompt(prompt_name)
    prompt_parts = [prompt_template]
    few_shot_context = _build_few_shot_prompt_context()
    if few_shot_context:
        prompt_parts.extend(["\n---\n", few_shot_context])
    prompt_parts.extend(["\n\n---\n输入文本：\n", text[:12000]])
    user_message = "".join(prompt_parts)
    if len(text) > 12000:
        user_message += "\n\n（文本已截断）"

    if token:
        attempt = 0
        backoff_seconds = LLM_JUDGE_INITIAL_BACKOFF_SECONDS
        last_reason = "request_failed"
        last_error_message = ""
        while attempt < LLM_JUDGE_MAX_ATTEMPTS:
            attempt += 1
            ok, parsed, err_msg = _call_spark_http(user_message)
            if ok and parsed:
                payload = post_process_llm_output(parsed, rubric)
                valid, err = validate_llm_judge_json(payload)
                if valid:
                    payload["called_spark_api"] = True
                    payload["called_openai_api"] = True
                    payload.setdefault("judge_mode", "openai")
                    payload.setdefault("model", _get_spark_model())
                    payload.setdefault("judge_source", "openai_api")
                    payload["retry_attempts"] = attempt
                    return payload
                last_reason = (
                    str(err.get("error") or "llm_output_invalid").strip() or "llm_output_invalid"
                )
                last_error_message = "; ".join(str(x) for x in (err.get("details") or [])[:4])
                retryable = True
            else:
                last_reason = "request_failed"
                last_error_message = str(err_msg or "").strip() or "request_failed"
                retryable = _is_retryable_llm_failure(last_error_message)

            if attempt >= LLM_JUDGE_MAX_ATTEMPTS or not retryable:
                return _build_llm_interrupted_payload(
                    reason=last_reason,
                    error_message=last_error_message,
                    retry_attempts=attempt,
                )
            logger.warning(
                "llm_judge_retry prompt=%s attempt=%s/%s reason=%s detail=%s",
                prompt_name,
                attempt,
                LLM_JUDGE_MAX_ATTEMPTS,
                last_reason,
                last_error_message,
            )
            time.sleep(backoff_seconds)
            backoff_seconds *= 2
        return _build_llm_interrupted_payload(
            reason=last_reason,
            error_message=last_error_message,
            retry_attempts=attempt,
        )
