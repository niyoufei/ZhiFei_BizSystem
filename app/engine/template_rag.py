from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Dict, List, Sequence

from app.config import RESOURCES_DIR

PROBE_DIMENSIONS_PATH = RESOURCES_DIR / "high_score_probe_dimensions.json"
HIGH_SCORE_TEMPLATES_PATH = RESOURCES_DIR / "high_score_templates.json"


@lru_cache(maxsize=1)
def _load_probe_dimensions() -> List[Dict[str, Any]]:
    if not PROBE_DIMENSIONS_PATH.exists():
        return []
    data = json.loads(PROBE_DIMENSIONS_PATH.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


@lru_cache(maxsize=1)
def _load_templates() -> Dict[str, Any]:
    if not HIGH_SCORE_TEMPLATES_PATH.exists():
        return {}
    data = json.loads(HIGH_SCORE_TEMPLATES_PATH.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def compute_probe_dimensions(
    *,
    text: str,
    dim_scores: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    probes = _load_probe_dimensions()
    src = str(text or "")
    out: List[Dict[str, Any]] = []
    for probe in probes:
        probe_id = str(probe.get("id") or "").strip()
        if not probe_id:
            continue
        target_dims = [str(x) for x in (probe.get("target_dimensions") or []) if str(x).strip()]
        keyword_terms = [str(x) for x in (probe.get("keywords") or []) if str(x).strip()]

        dim_rates: List[float] = []
        for dim_id in target_dims:
            dim = dim_scores.get(dim_id) or {}
            score = _safe_float(dim.get("dim_score"))
            max_score = max(1e-6, _safe_float(dim.get("max_score"), 10.0))
            dim_rates.append(max(0.0, min(1.0, score / max_score)))
        model_rate = sum(dim_rates) / len(dim_rates) if dim_rates else 0.0

        keyword_hits = sum(1 for kw in keyword_terms if kw in src)
        keyword_rate = keyword_hits / len(keyword_terms) if keyword_terms else 0.0

        score_rate = max(0.0, min(1.0, 0.75 * model_rate + 0.25 * keyword_rate))
        out.append(
            {
                "id": probe_id,
                "name": str(probe.get("name") or probe_id),
                "target_dimensions": target_dims,
                "weight_boost": _safe_float(probe.get("weight_boost"), 1.0),
                "score_rate": round(score_rate, 4),
                "model_rate": round(model_rate, 4),
                "keyword_rate": round(keyword_rate, 4),
            }
        )
    return out


def build_probe_template_suggestions(
    probe_dimensions: Sequence[Dict[str, Any]],
    *,
    threshold: float = 0.8,
) -> List[Dict[str, Any]]:
    templates = _load_templates()
    suggestions: List[Dict[str, Any]] = []
    for probe in probe_dimensions or []:
        score_rate = _safe_float(probe.get("score_rate"), 1.0)
        if score_rate >= threshold:
            continue
        probe_id = str(probe.get("id") or "")
        bucket = templates.get(probe_id) or {}
        template_chunks = bucket.get("templates") if isinstance(bucket, dict) else None
        if not isinstance(template_chunks, list):
            template_chunks = []
        gap = round(max(0.0, threshold - score_rate) * 100.0, 2)
        suggestions.append(
            {
                "dimension_id": probe_id,
                "title": f"高分探针补强：{probe.get('name')}",
                "expected_gain": round(min(25.0, 8.0 + gap * 0.25), 2),
                "action_steps": [
                    "大模型近期极其偏好此维度，当前文本在该探针明显偏弱。",
                    "请优先补充可执行动作、量化阈值、责任岗位与验收闭环。",
                ],
                "references": template_chunks[:2],
                "rag_tip": "请直接参考以下历史高分结构进行改写。",
                "loss_reason": (
                    f"探针得分率 {round(score_rate * 100, 1)}% < {round(threshold * 100, 1)}%，"
                    "存在高概率失分风险。"
                ),
            }
        )
    suggestions.sort(key=lambda x: -_safe_float(x.get("expected_gain")))
    return suggestions
