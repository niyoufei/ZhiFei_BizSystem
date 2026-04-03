from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Sequence
from uuid import uuid4

from pydantic import ValidationError

from app.config import RESOURCES_DIR
from app.engine.dimensions import DIMENSIONS
from app.schemas import ExtractedFeature
from app.storage import StorageDataError, load_high_score_features, save_high_score_features

FEATURE_BOOTSTRAP_PATH = RESOURCES_DIR / "high_score_templates.json"
logger = logging.getLogger(__name__)

DEFAULT_SKELETON = (
    "[前置条件] 适用场景与风险边界清晰 + "
    "[技术/动作] 构建可执行动作链与责任分工 + "
    "[量化指标类型] 频次阈值与闭环验收"
)


class FeatureDistillationError(RuntimeError):
    """特征蒸馏流程异常。"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        num = float(value)
    except Exception:
        return default
    if not math.isfinite(num):
        return default
    return num


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _coerce_feature(raw: Dict[str, Any]) -> ExtractedFeature | None:
    try:
        feature = ExtractedFeature(**raw)
    except ValidationError:
        return None
    if not feature.updated_at:
        feature.updated_at = _now_iso()
    if not feature.created_at:
        feature.created_at = feature.updated_at
    if not feature.logic_skeleton:
        return None
    return feature


def _normalize_governance_status(value: object) -> str | None:
    status = str(value or "").strip().lower()
    if status in {"pending", "adopted", "ignored", "auto_adopted", "legacy"}:
        return status
    return None


def _merge_unique_strings(*groups: Sequence[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for group in groups:
        for item in group:
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            out.append(text)
    return out


def _merge_governance_status(current: object, incoming: object) -> str | None:
    current_status = _normalize_governance_status(current)
    incoming_status = _normalize_governance_status(incoming)
    adopted_statuses = {"adopted", "auto_adopted"}
    if current_status in adopted_statuses or incoming_status in adopted_statuses:
        if "adopted" in {current_status, incoming_status}:
            return "adopted"
        return "auto_adopted"
    if current_status == "ignored" or incoming_status == "ignored":
        return "ignored"
    if current_status == "pending" or incoming_status == "pending":
        return "pending"
    return current_status or incoming_status


def _feature_runtime_allowed(feature: ExtractedFeature) -> bool:
    if not feature.active:
        return False
    governance_status = _normalize_governance_status(feature.governance_status)
    return governance_status not in {"pending", "ignored"}


def _load_bootstrap_features() -> List[ExtractedFeature]:
    if not FEATURE_BOOTSTRAP_PATH.exists():
        return []
    try:
        payload = json.loads(FEATURE_BOOTSTRAP_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    rows = payload.get("features") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return []
    out: List[ExtractedFeature] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        feature = _coerce_feature(row)
        if feature is not None:
            out.append(feature)
    return out


def load_feature_kb() -> List[ExtractedFeature]:
    try:
        rows = load_high_score_features()
    except StorageDataError as exc:
        logger.warning("feature_kb_storage_fallback error=%s", exc)
        return _load_bootstrap_features()
    out: List[ExtractedFeature] = []
    if isinstance(rows, list) and rows:
        for row in rows:
            if not isinstance(row, dict):
                continue
            feature = _coerce_feature(row)
            if feature is not None:
                out.append(feature)
        return out
    return _load_bootstrap_features()


def save_feature_kb(features: Sequence[ExtractedFeature]) -> None:
    payload = [f.model_dump() if hasattr(f, "model_dump") else dict(f) for f in features]
    save_high_score_features(payload)


def distill_feature_from_text(
    *,
    dimension_id: str,
    source_text: str,
    confidence_score: float = 0.62,
    governance_status: str | None = None,
    source_record_ids: Sequence[str] | None = None,
    source_highlights: Sequence[str] | None = None,
) -> ExtractedFeature | None:
    clean_text = str(source_text or "").strip()
    if not clean_text:
        return None
    candidate_lines = _skeleton_lines_from_text(clean_text)
    if clean_text not in candidate_lines:
        candidate_lines.append(clean_text)
    logic_skeleton = _sanitize_logic_skeleton(candidate_lines, clean_text)
    if not logic_skeleton:
        return None
    now = _now_iso()
    return ExtractedFeature(
        feature_id=str(uuid4()),
        dimension_id=str(dimension_id),
        logic_skeleton=logic_skeleton,
        confidence_score=_clip(_safe_float(confidence_score, 0.62), 0.0, 1.0),
        usage_count=0,
        active=True,
        governance_status=_normalize_governance_status(governance_status),
        source_record_ids=_merge_unique_strings(source_record_ids or []),
        source_highlights=_merge_unique_strings(source_highlights or []),
        created_at=now,
        updated_at=now,
    )


def upsert_distilled_features(features: Sequence[ExtractedFeature]) -> Dict[str, int]:
    clean_features = [feature for feature in features if isinstance(feature, ExtractedFeature)]
    if not clean_features:
        return {"added": 0, "updated": 0, "total": len(load_feature_kb())}

    existing = load_feature_kb()
    index = {
        (feature.dimension_id, tuple(feature.logic_skeleton)): feature
        for feature in existing
        if feature.active
    }
    added = 0
    updated = 0
    for feature in clean_features:
        key = (feature.dimension_id, tuple(feature.logic_skeleton))
        current = index.get(key)
        if current is None:
            existing.append(feature)
            index[key] = feature
            added += 1
            continue
        current.confidence_score = _clip(
            max(_safe_float(current.confidence_score), _safe_float(feature.confidence_score)),
            0.0,
            1.0,
        )
        current.active = True
        current.governance_status = _merge_governance_status(
            current.governance_status,
            feature.governance_status,
        )
        current.source_record_ids = _merge_unique_strings(
            current.source_record_ids or [],
            feature.source_record_ids or [],
        )
        current.source_highlights = _merge_unique_strings(
            current.source_highlights or [],
            feature.source_highlights or [],
        )
        current.retired_at = None
        current.updated_at = _now_iso()
        updated += 1
    if added or updated:
        save_feature_kb(existing)
    return {"added": added, "updated": updated, "total": len(existing)}


def _skeleton_lines_from_text(raw_text: str) -> List[str]:
    lines = re.split(r"[\n;；]+", raw_text)
    out = [x.strip("- ").strip() for x in lines if x.strip()]
    compact = [x for x in out if len(x) <= 80]
    return compact[:8]


def _normalize_formula_line(line: str) -> str:
    text = re.sub(r"\s+", " ", str(line or "")).strip(" -\t\r\n")
    if not text:
        return ""
    required_tokens = ("[前置条件]", "[技术/动作]", "[量化指标类型]")
    if all(token in text for token in required_tokens):
        return text

    parts = [p.strip() for p in re.split(r"\s*\+\s*|\s*[|｜;；]\s*", text) if p.strip()]
    if len(parts) >= 3:
        return f"[前置条件] {parts[0]} + " f"[技术/动作] {parts[1]} + " f"[量化指标类型] {parts[2]}"
    return (
        "[前置条件] 适用场景与约束条件明确 + "
        f"[技术/动作] {text} + "
        "[量化指标类型] 频次阈值与闭环证据"
    )


def _sanitize_logic_skeleton(candidates: Sequence[str], source_text: str) -> List[str]:
    """
    防查重净化：
    - 禁止数字（避免时间/金额/编号等可溯源信息）
    - 禁止原文整句复现（简单子串检验）
    - 强制输出抽象公式格式
    """
    src_plain = re.sub(r"\s+", "", str(source_text or "")).lower()
    out: List[str] = []
    seen: set[str] = set()

    for line in candidates:
        normalized = _normalize_formula_line(str(line or ""))
        if not normalized:
            continue
        if len(normalized) > 140:
            continue
        if re.search(r"\d", normalized):
            continue

        plain = re.sub(r"\s+", "", normalized).lower()
        if len(plain) >= 12 and plain in src_plain:
            continue

        if plain in seen:
            continue
        seen.add(plain)
        out.append(normalized)
        if len(out) >= 8:
            break

    if not out:
        out = [DEFAULT_SKELETON]
    return out


def _extract_prompt(chunk_text: str) -> str:
    return (
        "你是一个结构化解析引擎。\n"
        "请分析这段高分文本，绝对禁止摘抄任何原文句式或具体业务数据。\n"
        "只提取“得分逻辑公式、量化指标类型、技术实体群”，并转成高度抽象骨架。\n"
        "输出要求：JSON，仅包含 key=logic_skeleton，值为字符串数组。\n"
        "每条必须是：[前置条件] + [技术/动作] + [量化指标类型]。\n"
        "禁止输出任何完整原文句子、项目名称、时间/金额/编号等可溯源信息。\n"
        f"\n待解析文本：\n{chunk_text}\n"
    )


def extract_logic_skeleton_via_llm(
    chunk_text: str,
    *,
    dimension_id: str = "unknown",
    llm_invoke: Callable[[str], str] | None = None,
) -> Dict[str, Any]:
    """
    模块一：语义解构抽取。
    - 如传入 llm_invoke，则调用模型并解析 JSON。
    - 未传入时返回 prompt，便于上层接入任意本地模型。
    """
    prompt = _extract_prompt(chunk_text)
    if llm_invoke is None:
        return {"prompt": prompt, "logic_skeleton": []}

    try:
        raw = llm_invoke(prompt)
    except Exception as exc:
        return {"prompt": prompt, "logic_skeleton": [], "error": f"llm_invoke_failed: {exc}"}

    parsed: Dict[str, Any]
    try:
        parsed = json.loads(str(raw or "{}"))
    except Exception:
        parsed = {"logic_skeleton": _skeleton_lines_from_text(str(raw or ""))}

    skeleton = parsed.get("logic_skeleton")
    if not isinstance(skeleton, list):
        skeleton = _skeleton_lines_from_text(str(raw or ""))

    clean = _sanitize_logic_skeleton([str(x) for x in skeleton], chunk_text)
    now = _now_iso()
    feature = ExtractedFeature(
        feature_id=str(uuid4()),
        dimension_id=str(dimension_id),
        logic_skeleton=clean,
        confidence_score=0.5,
        usage_count=0,
        active=True,
        created_at=now,
        updated_at=now,
    )
    return {
        "prompt": prompt,
        "logic_skeleton": feature.logic_skeleton,
        "feature": feature.model_dump(),
    }


def select_top_logic_skeletons(
    *,
    dimension_ids: Sequence[str],
    top_k: int = 3,
) -> List[ExtractedFeature]:
    features = load_feature_kb()
    dim_set = {str(x) for x in dimension_ids}
    candidates = [f for f in features if _feature_runtime_allowed(f) and f.dimension_id in dim_set]
    candidates.sort(key=lambda f: (f.confidence_score, -f.usage_count), reverse=True)
    return candidates[: max(1, top_k)]


def select_top_few_shot_prompt_examples(
    *,
    dimension_ids: Sequence[str] | None = None,
    top_k: int = 4,
) -> List[Dict[str, Any]]:
    dim_set = {
        str(item or "").strip().upper() for item in (dimension_ids or []) if str(item or "").strip()
    }
    candidates: List[ExtractedFeature] = []
    for feature in load_feature_kb():
        governance_status = _normalize_governance_status(feature.governance_status)
        if governance_status not in {"adopted", "auto_adopted"}:
            continue
        if not _feature_runtime_allowed(feature):
            continue
        feature_dim_id = str(feature.dimension_id or "").strip().upper()
        if dim_set and feature_dim_id not in dim_set:
            continue
        candidates.append(feature)
    candidates.sort(key=lambda f: (f.confidence_score, -f.usage_count), reverse=True)
    out: List[Dict[str, Any]] = []
    for feature in candidates[: max(1, top_k)]:
        dim_id = str(feature.dimension_id or "").strip().upper()
        dim_name = str((DIMENSIONS.get(dim_id) or {}).get("name") or dim_id).strip()
        out.append(
            {
                "feature_id": str(feature.feature_id or "").strip(),
                "dimension_id": dim_id,
                "dimension_name": dim_name,
                "logic_skeleton": list(feature.logic_skeleton or [])[:3],
                "source_highlights": list(feature.source_highlights or [])[:3],
                "governance_status": governance_status,
                "confidence_score": round(float(feature.confidence_score or 0.0), 4),
            }
        )
    return out


def update_feature_confidence(
    applied_feature_ids: Sequence[str],
    actual_score: float,
    predicted_score: float,
    *,
    dead_zone_ratio: float = 0.02,
    score_band: float = 12.0,
    alpha_up: float = 0.12,
    alpha_down: float = 0.18,
    max_step: float = 0.25,
) -> Dict[str, Any]:
    """
    模块二：基于真实反馈更新置信度（带防抖动）。

    数学防抖逻辑：
    1) 误差归一化：error_norm = tanh((actual - predicted) / score_band)
       - 通过 tanh 压缩极值，防止单次异常反馈造成剧烈震荡。
    2) 死区过滤：|error_norm| <= dead_zone_ratio 不更新
       - 屏蔽小噪声，避免频繁抖动。
    3) 非对称更新：
       - 提升：delta = alpha_up * step * (1 - confidence)
       - 下调：delta = alpha_down * step * (0.25 + confidence)
       其中 step ∈ [-max_step, max_step]。
    4) 淘汰：confidence_score < 0.2 且 usage_count >= 3 -> 软删除。
    """
    target_ids = {str(x).strip() for x in applied_feature_ids if str(x).strip()}
    if not target_ids:
        return {"updated": 0, "retired": 0, "reason": "empty_applied_feature_ids"}

    features = load_feature_kb()
    if not features:
        return {"updated": 0, "retired": 0, "reason": "feature_kb_empty"}

    actual = _safe_float(actual_score)
    predicted = _safe_float(predicted_score)
    error = actual - predicted

    band = max(1e-6, _safe_float(score_band, 12.0))
    error_norm = math.tanh(error / band)
    if abs(error_norm) <= _clip(_safe_float(dead_zone_ratio, 0.02), 0.0, 0.2):
        return {
            "updated": 0,
            "retired": 0,
            "reason": "inside_dead_zone",
            "delta": round(error, 4),
            "error_norm": round(error_norm, 6),
        }

    step = _clip(error_norm, -abs(_safe_float(max_step, 0.25)), abs(_safe_float(max_step, 0.25)))
    up = _clip(_safe_float(alpha_up, 0.12), 1e-6, 1.0)
    down = _clip(_safe_float(alpha_down, 0.18), 1e-6, 1.0)

    updated = 0
    retired = 0
    now = _now_iso()
    for feature in features:
        if feature.feature_id not in target_ids or not feature.active:
            continue

        feature.usage_count += 1
        confidence_before = _clip(feature.confidence_score, 0.0, 1.0)

        if step >= 0:
            delta = up * step * (1.0 - confidence_before)
        else:
            delta = down * step * (0.25 + confidence_before)

        feature.confidence_score = _clip(confidence_before + delta, 0.0, 1.0)
        feature.updated_at = now

        if feature.confidence_score < 0.2 and feature.usage_count >= 3:
            feature.active = False
            feature.retired_at = now
            retired += 1
        updated += 1

    if updated > 0:
        save_feature_kb(features)

    return {
        "updated": updated,
        "retired": retired,
        "delta": round(error, 4),
        "error_norm": round(error_norm, 6),
        "step": round(step, 6),
    }


def _advice_prompt(
    *,
    weak_text: str,
    project_context: str,
    top_logic_skeletons: Sequence[Sequence[str]],
) -> str:
    skeleton_text = json.dumps([list(x) for x in top_logic_skeletons], ensure_ascii=False)
    return (
        f"已知当前正在编制的新项目背景为：{project_context}。\n"
        f"为了提升得分，请严格遵循以下从历史高分库提取的逻辑骨架：{skeleton_text}。\n"
        f"请对用户的弱项文本进行深度重写指导：{weak_text}\n\n"
        "要求：\n"
        "1) 必须将抽象骨架具象化到当前新项目的真实业务场景中。\n"
        "2) 给出可执行改写提纲：章节落点、动作链、量化指标类型、验收闭环。\n"
        "3) 禁止输出任何历史原句、固定模板句、可检索业务特征串。\n"
        "4) 警告：绝不带有模板套用痕迹，必须保证输出内容完全原创，防范查重引擎。\n"
    )


def generate_tailored_advice(
    weak_text: str,
    project_context: str,
    top_logic_skeletons: Sequence[Sequence[str]],
    *,
    llm_invoke: Callable[[str], str] | None = None,
) -> Dict[str, Any]:
    """
    模块三：上下文感知动态纠偏生成器。
    """
    prompt = _advice_prompt(
        weak_text=str(weak_text or "").strip(),
        project_context=str(project_context or "").strip(),
        top_logic_skeletons=top_logic_skeletons,
    )
    if llm_invoke is None:
        return {"prompt": prompt, "advice": None}
    try:
        text = llm_invoke(prompt)
    except Exception as exc:
        return {"prompt": prompt, "advice": None, "error": f"llm_invoke_failed: {exc}"}

    advice = str(text or "").strip()
    if not advice:
        return {"prompt": prompt, "advice": None, "error": "empty_llm_output"}
    return {"prompt": prompt, "advice": advice}
