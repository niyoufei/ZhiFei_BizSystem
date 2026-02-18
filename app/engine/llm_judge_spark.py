from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from app.engine.dimensions import DIMENSIONS
from app.schemas import ScoreReport

# 讯飞星火 HTTP 接口（OpenAI 兼容）
SPARK_HTTP_URL = "https://spark-api-open.xf-yun.com/v1/chat/completions"
SPARK_DEFAULT_MODEL = "4.0Ultra"


def _get_spark_model() -> str:
    """进化/评标用模型，默认最高端 4.0Ultra；可通过 SPARK_MODEL 覆盖（如 generalv3.5）。"""
    return (os.getenv("SPARK_MODEL") or "").strip() or SPARK_DEFAULT_MODEL


def _get_spark_bearer_token() -> str | None:
    """获取 HTTP Bearer 鉴权令牌。仅 SPARK_APIPASSWORD 会触发真实 HTTP 调用（讯飞控制台 HTTP 接口的 APIPassword）。"""
    return os.getenv("SPARK_APIPASSWORD") or None


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

    for must in ["07", "09", "02", "03"]:
        dim = dim_scores.get(must, {})
        if "weight_multiplier" not in dim:
            errors.append(f"missing_weight_multiplier:{must}")

    if errors:
        return False, {"error": "llm_judge_json_invalid", "details": errors}
    return True, {}


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

        evidence = dim.get("evidence") or []
        if not isinstance(evidence, list) or len(evidence) == 0:
            evidence = [
                {
                    "start_index": 0,
                    "end_index": 0,
                    "snippet": "未在输入文本中检索到可支撑该维度的直接证据。",
                }
            ]
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
    }


def _build_from_rules(report: ScoreReport, rubric: Dict[str, Any]) -> Dict[str, Any]:
    weight_profile = rubric.get("llm_weight_profile", {})
    high_dims = set(weight_profile.get("high_priority_dims", []))
    high_mul = float(weight_profile.get("high_priority_multiplier", 1.4))
    normal_mul = float(weight_profile.get("normal_multiplier", 1.0))

    dimension_scores: Dict[str, Any] = {}
    for dim_id, meta in DIMENSIONS.items():
        dim = report.dimension_scores[dim_id]
        evidence = [e.model_dump() for e in dim.evidence]
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
        "judge_mode": "spark",
        "model": "spark",
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
            "evidence": [e.model_dump() for e in report.logic_lock.evidence],
        },
        "dimension_scores": dimension_scores,
        "penalties": [
            {
                "code": p.code,
                "message": p.message,
                "deduct": p.deduct or 0.0,
                "evidence": p.evidence_span.model_dump()
                if p.evidence_span
                else _placeholder_evidence(),
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
    调用讯飞星火 HTTP 接口，非流式。
    返回 (成功, 解析后的 JSON 或 None, 错误信息)。
    """
    token = _get_spark_bearer_token()
    if not token:
        return False, None, "missing_credentials"
    if model is None:
        model = _get_spark_model()

    try:
        import urllib.request

        body = json.dumps(
            {
                "model": model,
                "messages": [{"role": "user", "content": user_message}],
                "stream": False,
                "max_tokens": max_tokens,
                "temperature": 0.3,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        req = urllib.request.Request(
            SPARK_HTTP_URL,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
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


def run_spark_judge(
    text: str,
    rubric: Dict[str, Any],
    prompt_name: str,
    rules_report: ScoreReport,
) -> Dict[str, Any]:
    """
    执行星火评标：若配置了 SPARK_APIPASSWORD 或 SPARK_API_KEY，则调用讯飞星火 HTTP 接口；
    否则若配置了 SPARK_APP_ID + SPARK_API_KEY + SPARK_API_SECRET，则仅用规则结果（兼容旧行为）；
    否则返回 missing_credentials。
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
                payload.setdefault("judge_mode", "spark")
                return payload
        # API 失败或校验不通过：回退到规则结果
        payload = _build_from_rules(rules_report, rubric)
        payload = post_process_llm_output(payload, rubric)
        payload["called_spark_api"] = False
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
            "reason": "api_error",
            "error": err,
        }
    payload["called_spark_api"] = True
    return payload
