from __future__ import annotations

from typing import Callable, Dict, Iterable

from app.domain.learning.ground_truth_records import (
    normalize_score_scale_max,
    resolve_project_score_scale_max,
    score_scale_label,
    to_float_or_none,
)


def default_ground_truth_score_rule(
    *,
    project_id: str,
    project: dict[str, object],
) -> dict[str, object]:
    score_scale_max = resolve_project_score_scale_max(project)
    return {
        "project_id": project_id,
        "score_scale_max": score_scale_max,
        "score_scale_label": score_scale_label(score_scale_max),
        "score_scale_detected": False,
        "score_scale_source_filename": None,
        "score_scale_source_page_hint": None,
        "score_scale_source_excerpt": None,
        "formula": "manual",
        "label": "未识别招标文件取分规则，请人工录入最终得分",
        "rounding_digits": 2,
        "drop_highest_count": 0,
        "drop_lowest_count": 0,
        "auto_compute": False,
        "detected": False,
        "source_filename": None,
        "source_page_hint": None,
        "source_excerpt": None,
    }


def resolve_project_ground_truth_score_rule(
    project_id: str,
    *,
    project: dict[str, object],
    materials: Iterable[dict[str, object]],
    extract_rule_from_text: Callable[[str, str], dict[str, object] | None],
    extract_rule_from_material: Callable[[dict[str, object]], dict[str, object] | None],
    extract_scale_from_material: Callable[[dict[str, object]], dict[str, object] | None],
) -> dict[str, object]:
    default_rule = default_ground_truth_score_rule(project_id=project_id, project=project)
    meta = project.get("meta") if isinstance(project.get("meta"), dict) else {}
    override_formula = str((meta or {}).get("ground_truth_final_score_formula") or "").strip()
    if override_formula in {"simple_mean", "trim_one_each_mean"}:
        override_rule = dict(default_rule)
        override_rule.update(
            {
                "formula": override_formula,
                "label": (
                    "按项目配置：评标委员会各成员打分平均值"
                    if override_formula == "simple_mean"
                    else "按项目配置：去最高分、最低分后取平均"
                ),
                "drop_highest_count": 1 if override_formula == "trim_one_each_mean" else 0,
                "drop_lowest_count": 1 if override_formula == "trim_one_each_mean" else 0,
                "auto_compute": True,
                "detected": True,
                "source_filename": "project.meta.ground_truth_final_score_formula",
            }
        )
        return override_rule

    tender_materials = [
        material
        for material in materials
        if str(material.get("project_id") or "") == str(project_id)
        and str(material.get("material_type") or "") == "tender_qa"
    ]
    candidates: list[Dict[str, object]] = []
    for material in tender_materials:
        parsed_text = str(material.get("parsed_text") or "").strip()
        if not parsed_text:
            continue
        candidate = extract_rule_from_text(parsed_text, str(material.get("filename") or ""))
        if candidate:
            candidates.append(candidate)
    if not candidates:
        for material in tender_materials:
            candidate = extract_rule_from_material(material)
            if candidate:
                candidates.append(candidate)

    scale_candidates: list[Dict[str, object]] = []
    for material in tender_materials:
        scale_candidate = extract_scale_from_material(material)
        if scale_candidate:
            scale_candidates.append(scale_candidate)
    scale_candidates.sort(
        key=lambda item: (
            -int(to_float_or_none(item.get("score_scale_confidence")) or 0),
            str(item.get("score_scale_source_filename") or ""),
        )
    )

    if not candidates:
        if scale_candidates:
            default_rule.update(scale_candidates[0])
            detected_scale = normalize_score_scale_max(
                default_rule.get("score_scale_max"),
                default=default_rule["score_scale_max"],
            )
            default_rule["score_scale_max"] = detected_scale
            default_rule["score_scale_label"] = score_scale_label(detected_scale)
            default_rule["score_scale_detected"] = True
        return default_rule

    candidates.sort(
        key=lambda item: (
            -int(to_float_or_none(item.get("confidence")) or 0),
            str(item.get("source_filename") or ""),
        )
    )
    resolved_rule = dict(default_rule)
    resolved_rule.update(candidates[0])
    if scale_candidates and not bool(resolved_rule.get("score_scale_detected")):
        resolved_rule.update(scale_candidates[0])
    resolved_scale = normalize_score_scale_max(
        resolved_rule.get("score_scale_max"),
        default=default_rule["score_scale_max"],
    )
    resolved_rule["score_scale_max"] = resolved_scale
    resolved_rule["score_scale_label"] = score_scale_label(resolved_scale)
    resolved_rule["score_scale_detected"] = bool(resolved_rule.get("score_scale_detected"))
    resolved_rule["auto_compute"] = resolved_rule.get("formula") in {
        "simple_mean",
        "trim_one_each_mean",
    }
    return resolved_rule
