from __future__ import annotations

from typing import Dict, Iterable, List

from app.domain.learning.ground_truth_records import to_float_or_none
from app.engine.dimensions import DIMENSIONS

DIMENSION_IDS = sorted(DIMENSIONS.keys())


def normalize_dimension_id(value: object) -> str:
    dim_id = str(value or "").strip().upper()
    if dim_id.startswith("P") and dim_id[1:] in DIMENSION_IDS:
        dim_id = dim_id[1:]
    if dim_id in DIMENSION_IDS:
        return dim_id
    return ""


def flatten_ground_truth_qualitative_tags(
    gt_record: Dict[str, object],
    *,
    limit: int = 8,
) -> List[str]:
    tags_by_judge = gt_record.get("qualitative_tags_by_judge")
    if not isinstance(tags_by_judge, list):
        return []
    seen: set[str] = set()
    out: List[str] = []
    for judge_tags in tags_by_judge:
        if not isinstance(judge_tags, list):
            continue
        for tag in judge_tags:
            clean = str(tag or "").strip()
            if not clean or clean in seen:
                continue
            seen.add(clean)
            out.append(clean)
            if len(out) >= max(1, int(limit)):
                return out
    return out


def collect_dimension_evidence_texts(
    report: Dict[str, object],
    *,
    dimension_id: str,
    limit: int = 3,
) -> List[str]:
    dim_scores = report.get("dimension_scores")
    payload = dim_scores.get(dimension_id) if isinstance(dim_scores, dict) else None
    if not isinstance(payload, dict):
        return []
    evidence_rows = payload.get("evidence") if isinstance(payload.get("evidence"), list) else []
    out: List[str] = []
    seen: set[str] = set()
    for row in evidence_rows:
        if not isinstance(row, dict):
            continue
        quote = str(row.get("quote") or row.get("snippet") or "").strip()
        anchor = str(row.get("anchor_label") or row.get("anchor") or "").strip()
        text = f"{anchor}：{quote}" if anchor and quote else (quote or anchor)
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= max(1, int(limit)):
            break
    return out


def collect_dimension_guidance_texts(
    report: Dict[str, object],
    *,
    dimension_id: str,
    limit: int = 2,
) -> List[str]:
    suggestions = report.get("suggestions")
    if not isinstance(suggestions, list):
        return []
    out: List[str] = []
    seen: set[str] = set()
    for item in suggestions:
        if not isinstance(item, dict):
            continue
        item_dim = normalize_dimension_id(item.get("dimension_id"))
        if item_dim != dimension_id:
            continue
        text = str(item.get("text") or item.get("suggestion") or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= max(1, int(limit)):
            break
    return out


def select_ground_truth_few_shot_dimensions(
    *,
    report: Dict[str, object],
    feature_confidence_update: Dict[str, object],
    max_dimensions: int = 4,
) -> List[str]:
    selected: List[str] = []
    for item in feature_confidence_update.get("applied_dimension_ids") or []:
        dim_id = normalize_dimension_id(item)
        if dim_id and dim_id not in selected:
            selected.append(dim_id)
        if len(selected) >= max(1, int(max_dimensions)):
            return selected

    dimension_scores = report.get("dimension_scores")
    ranked: List[tuple[int, float, str]] = []
    if isinstance(dimension_scores, dict):
        for raw_dim_id, payload in dimension_scores.items():
            dim_id = normalize_dimension_id(raw_dim_id)
            if not dim_id or not isinstance(payload, dict):
                continue
            evidence_count = (
                len(payload.get("evidence") or [])
                if isinstance(payload.get("evidence"), list)
                else 0
            )
            score = float(to_float_or_none(payload.get("score")) or 0.0)
            ranked.append((evidence_count, score, dim_id))
    ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
    for _, _, dim_id in ranked:
        if dim_id not in selected:
            selected.append(dim_id)
        if len(selected) >= max(1, int(max_dimensions)):
            break
    return selected


def build_ground_truth_project_by_record_id(
    rows: Iterable[Dict[str, object]],
) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        record_id = str(row.get("id") or "").strip()
        record_project_id = str(row.get("project_id") or "").strip()
        if record_id and record_project_id:
            mapping[record_id] = record_project_id
    return mapping


def extract_feature_project_ids(
    feature: object,
    *,
    ground_truth_project_by_record_id: Dict[str, str],
) -> set[str]:
    project_ids = {
        str(item or "").strip()
        for item in (getattr(feature, "source_project_ids", None) or [])
        if str(item or "").strip()
    }
    if project_ids:
        return project_ids
    for record_id in getattr(feature, "source_record_ids", None) or []:
        normalized_record_id = str(record_id or "").strip()
        mapped_project_id = ground_truth_project_by_record_id.get(normalized_record_id)
        if mapped_project_id:
            project_ids.add(mapped_project_id)
    return project_ids


def resolve_distillation_feature_ids_for_record(
    record: Dict[str, object],
    distillation: Dict[str, object],
    *,
    features: Iterable[object],
    ground_truth_rows: Iterable[Dict[str, object]],
) -> List[str]:
    explicit_ids = [
        str(item or "").strip()
        for item in (distillation.get("feature_ids") or [])
        if str(item or "").strip()
    ]
    explicit_id_set = set(explicit_ids)
    record_id = str(record.get("id") or "").strip()
    project_id = str(record.get("project_id") or "").strip()
    dimension_ids = {
        normalize_dimension_id(item)
        for item in (distillation.get("dimension_ids") or [])
        if normalize_dimension_id(item)
    }
    if not record_id and not project_id:
        return explicit_ids

    ground_truth_project_by_record_id = build_ground_truth_project_by_record_id(ground_truth_rows)
    valid_explicit_ids: List[str] = []
    matched_record_ids: List[str] = []
    matched_project_ids: List[str] = []
    for feature in features:
        feature_id = str(getattr(feature, "feature_id", "") or "").strip()
        if not feature_id:
            continue
        feature_dim_id = str(getattr(feature, "dimension_id", "") or "").strip().upper()
        normalized_feature_dim_id = (
            feature_dim_id[1:]
            if feature_dim_id.startswith("P") and feature_dim_id[1:] in DIMENSION_IDS
            else feature_dim_id
        )
        if dimension_ids and normalized_feature_dim_id not in dimension_ids:
            continue
        source_record_ids = {
            str(item or "").strip()
            for item in (getattr(feature, "source_record_ids", None) or [])
            if str(item or "").strip()
        }
        feature_project_ids = extract_feature_project_ids(
            feature,
            ground_truth_project_by_record_id=ground_truth_project_by_record_id,
        )
        belongs_to_record = bool(record_id and record_id in source_record_ids)
        belongs_to_project = bool(project_id and project_id in feature_project_ids)
        if (
            feature_id in explicit_id_set
            and (belongs_to_record or belongs_to_project)
            and feature_id not in valid_explicit_ids
        ):
            valid_explicit_ids.append(feature_id)
        if belongs_to_record:
            matched_record_ids.append(feature_id)
            continue
        if belongs_to_project:
            matched_project_ids.append(feature_id)
    resolved_ids: List[str] = list(valid_explicit_ids)
    resolved_id_set = set(resolved_ids)
    if matched_record_ids:
        for feature_id in matched_record_ids:
            if feature_id not in resolved_id_set:
                resolved_ids.append(feature_id)
                resolved_id_set.add(feature_id)
        return resolved_ids
    for feature_id in matched_project_ids:
        if feature_id not in resolved_id_set:
            resolved_ids.append(feature_id)
            resolved_id_set.add(feature_id)
    return resolved_ids
