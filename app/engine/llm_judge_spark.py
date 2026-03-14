from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from app.engine.dimensions import DIMENSIONS
from app.engine.openai_compat import call_openai_json, get_openai_api_key, get_openai_model
from app.schemas import ScoreReport

# 兼容保留：历史文件名/函数名不变，但实际 provider 已切到 OpenAI GPT-5.4。
SPARK_DEFAULT_MODEL = "gpt-5.4"


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
    """历史兼容入口，实际读取 OpenAI API Key。"""
    return get_openai_api_key()


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
    anchor_label = str(item.get("anchor_label") or "").strip() or "正文片段"
    quote = str(item.get("quote") or snippet).strip()
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

        defects = dim.get("defects") or []
        if not isinstance(defects, list) or len(defects) == 0:
            defects = [
                "文本对本维度的关键要素表述不足，存在参数/频次/验收/责任等落实要素缺失风险。"
            ]
            default_used = True
        dim["defects"] = defects

        improvements = dim.get("improvements") or []
        if not isinstance(improvements, list) or len(improvements) == 0:
            improvements = [
                "建议补充可量化控制指标（阈值/参数）与管理频次（日报/周检），并明确责任岗位与验收闭环（报验/签认）。"
            ]
            default_used = True
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
    required_env = ["SPARK_APP_ID", "SPARK_API_KEY", "SPARK_API_SECRET"]
    if not token and not all(os.getenv(k) for k in required_env):
        return {
            "called_spark_api": False,
            "reason": "missing_credentials",
        }

    prompt_template = load_prompt(prompt_name)
    user_message = f"{prompt_template}\n\n---\n输入文本：\n{text[:12000]}"
    if len(text) > 12000:
        user_message += "\n\n（文本已截断）"

    if token:
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
                return payload
        # API 失败或校验不通过：回退到规则结果
        payload = _build_from_rules(rules_report, rubric)
        payload = post_process_llm_output(payload, rubric)
        payload["called_spark_api"] = False
        payload["called_openai_api"] = False
        payload["reason"] = "api_error" if ok else "request_failed"
        if err_msg:
            payload["fallback_reason"] = err_msg
        return payload

    # 未配置 HTTP 令牌：沿用原有逻辑（仅规则，用于兼容测试/旧配置）
    payload = _build_from_rules(rules_report, rubric)
    payload = post_process_llm_output(payload, rubric)
    ok, err = validate_llm_judge_json(payload)
    if not ok:
        return {
            "called_spark_api": False,
            "called_openai_api": False,
            "reason": "api_error",
            "error": err,
        }
    payload["called_spark_api"] = True
    payload["called_openai_api"] = True
    payload["judge_mode"] = "openai"
    payload["model"] = _get_spark_model()
    payload["judge_source"] = "openai_api"
    return payload
