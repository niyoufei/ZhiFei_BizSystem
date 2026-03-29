from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

DEFAULT_MATERIAL_TYPE_ORDER = ["tender_qa", "boq", "drawing", "site_photo"]


@dataclass(frozen=True)
class LatestSubmissionContext:
    ensure_report_material_usage_metadata: Callable[[Dict[str, object]], None]
    ensure_report_score_self_awareness: Callable[..., None]
    build_submission_evidence_trace_report: Callable[..., Dict[str, object]]
    build_submission_scoring_basis_report: Callable[..., Dict[str, object]]


@dataclass(frozen=True)
class MaterialCardContext:
    normalize_material_type: Callable[..., str]
    normalize_numeric_token: Callable[[object], str]
    classify_numeric_anchor_category: Callable[..., str]
    append_numeric_anchor_bucket: Callable[[Dict[str, List[str]], str, str], None]
    build_numeric_anchor_category_summary: Callable[[Dict[str, List[str]]], List[str]]
    material_type_label: Callable[[object], str]
    to_float_or_none: Callable[[Any], Optional[float]]


def prepare_latest_submission_context(
    *,
    project_id: str,
    latest: Optional[Dict[str, object]],
    material_knowledge: Dict[str, object],
    context: LatestSubmissionContext,
) -> Tuple[Dict[str, object], Optional[Dict[str, object]], Optional[Dict[str, object]]]:
    latest_submission: Dict[str, object] = {
        "exists": False,
        "submission_id": None,
        "filename": None,
        "created_at": None,
        "scoring_status": None,
        "is_scored": False,
    }
    evidence_trace: Optional[Dict[str, object]] = None
    scoring_basis: Optional[Dict[str, object]] = None

    if not isinstance(latest, dict):
        return latest_submission, evidence_trace, scoring_basis

    report = latest.get("report") if isinstance(latest.get("report"), dict) else {}
    context.ensure_report_material_usage_metadata(report)
    context.ensure_report_score_self_awareness(
        report,
        project_id=project_id,
        material_knowledge_snapshot=material_knowledge,
    )
    scoring_status = str(report.get("scoring_status") or "unknown")
    report_meta = report.get("meta") if isinstance(report.get("meta"), dict) else {}
    latest_submission = {
        "exists": True,
        "submission_id": str(latest.get("id") or ""),
        "filename": str(latest.get("filename") or ""),
        "created_at": str(latest.get("created_at") or ""),
        "scoring_status": scoring_status,
        "is_scored": scoring_status not in {"pending", "blocked", "unknown"},
        "score_self_awareness": (
            report_meta.get("score_self_awareness")
            if isinstance(report_meta.get("score_self_awareness"), dict)
            else {}
        ),
        "score_confidence_level": str(report_meta.get("score_confidence_level") or ""),
    }
    evidence_trace = context.build_submission_evidence_trace_report(
        project_id=project_id,
        submission=latest,
    )
    scoring_basis = context.build_submission_scoring_basis_report(
        project_id=project_id,
        submission=latest,
    )
    return latest_submission, evidence_trace, scoring_basis


def build_dimension_support_cards(
    knowledge_by_dimension: Sequence[object],
    *,
    to_float_or_none: Callable[[Any], Optional[float]],
    normalize_material_type: Callable[..., str],
) -> List[Dict[str, object]]:
    cards: List[Dict[str, object]] = []
    for row in knowledge_by_dimension:
        if not isinstance(row, dict):
            continue
        cards.append(
            {
                "dimension_id": str(row.get("dimension_id") or ""),
                "dimension_name": str(row.get("dimension_name") or ""),
                "coverage_score": to_float_or_none(row.get("coverage_score")) or 0.0,
                "coverage_level": str(row.get("coverage_level") or "low"),
                "keyword_hits": int(to_float_or_none(row.get("keyword_hits")) or 0),
                "numeric_signal_hits": int(to_float_or_none(row.get("numeric_signal_hits")) or 0),
                "source_types": [
                    normalize_material_type(item) or str(item or "").strip()
                    for item in (row.get("source_types") or [])
                    if str(item or "").strip()
                ][:6],
                "source_file_count": int(to_float_or_none(row.get("source_file_count")) or 0),
                "source_files_preview": [
                    str(item or "").strip()
                    for item in (row.get("source_files_preview") or [])
                    if str(item or "").strip()
                ][:6],
                "suggested_keywords": [
                    str(item or "").strip()
                    for item in (row.get("suggested_keywords") or [])
                    if str(item or "").strip()
                ][:4],
            }
        )
    cards.sort(
        key=lambda item: (
            -float(to_float_or_none(item.get("coverage_score")) or 0.0),
            str(item.get("dimension_id") or ""),
        )
    )
    return cards


def _build_knowledge_maps(
    knowledge_rows: Sequence[object],
    *,
    context: MaterialCardContext,
) -> Tuple[Dict[str, Dict[str, object]], Dict[str, List[str]], Dict[str, List[str]]]:
    knowledge_by_type_map: Dict[str, Dict[str, object]] = {}
    knowledge_numeric_terms_by_type: Dict[str, List[str]] = {}
    knowledge_numeric_summary_by_type: Dict[str, List[str]] = {}
    for row in knowledge_rows:
        if not isinstance(row, dict):
            continue
        mat_type = context.normalize_material_type(row.get("material_type"))
        if not mat_type:
            continue
        knowledge_by_type_map[mat_type] = row
        top_numeric_terms: List[str] = []
        for item in row.get("top_numeric_terms") or []:
            token = context.normalize_numeric_token(item)
            if token:
                top_numeric_terms.append(token)
        top_numeric_terms = top_numeric_terms[:12]
        knowledge_numeric_terms_by_type[mat_type] = top_numeric_terms

        category_buckets: Dict[str, List[str]] = {}
        top_terms = row.get("top_terms") if isinstance(row.get("top_terms"), list) else []
        top_dimensions = (
            row.get("top_dimensions") if isinstance(row.get("top_dimensions"), list) else []
        )
        first_dim = ""
        if top_dimensions and isinstance(top_dimensions[0], dict):
            first_dim = str(top_dimensions[0].get("dimension_id") or "")
        for token in top_numeric_terms:
            category = context.classify_numeric_anchor_category(
                terms=top_terms[:8],
                material_type=mat_type,
                dimension_id=first_dim,
                label=row.get("material_type_label"),
            )
            context.append_numeric_anchor_bucket(category_buckets, category, token)
        knowledge_numeric_summary_by_type[mat_type] = context.build_numeric_anchor_category_summary(
            category_buckets
        )
    return knowledge_by_type_map, knowledge_numeric_terms_by_type, knowledge_numeric_summary_by_type


def _build_requirement_maps(
    requirement_hits: Sequence[object],
    *,
    normalize_material_type: Callable[..., str],
) -> Tuple[
    Dict[str, List[str]],
    Dict[str, List[str]],
    Dict[str, List[str]],
    Dict[str, List[Dict[str, str]]],
    Dict[str, List[Dict[str, str]]],
]:
    hit_filenames_by_type: Dict[str, List[str]] = {}
    hit_requirement_labels_by_type: Dict[str, List[str]] = {}
    miss_requirement_labels_by_type: Dict[str, List[str]] = {}
    hit_evidence_preview_by_type: Dict[str, List[Dict[str, str]]] = {}
    miss_evidence_preview_by_type: Dict[str, List[Dict[str, str]]] = {}

    for item in requirement_hits:
        if not isinstance(item, dict):
            continue
        mat_type = normalize_material_type(item.get("material_type"))
        label = str(item.get("label") or "").strip()
        source_filename = str(item.get("source_filename") or "").strip()
        if not source_filename:
            chunk_id = str(item.get("chunk_id") or "").strip()
            if "#c" in chunk_id:
                source_filename = chunk_id.split("#c", 1)[0].strip()
        preview_row = {
            "label": label,
            "source_filename": source_filename,
            "source_mode": str(item.get("source_mode") or "").strip(),
            "reason": str(item.get("reason") or "").strip()[:120],
        }
        if mat_type and label:
            label_bucket = (
                hit_requirement_labels_by_type.setdefault(mat_type, [])
                if bool(item.get("hit"))
                else miss_requirement_labels_by_type.setdefault(mat_type, [])
            )
            if label not in label_bucket:
                label_bucket.append(label)
            preview_bucket = (
                hit_evidence_preview_by_type.setdefault(mat_type, [])
                if bool(item.get("hit"))
                else miss_evidence_preview_by_type.setdefault(mat_type, [])
            )
            if preview_row not in preview_bucket:
                preview_bucket.append(preview_row)
        if not bool(item.get("hit")):
            continue
        if not mat_type or not source_filename:
            continue
        filename_bucket = hit_filenames_by_type.setdefault(mat_type, [])
        if source_filename not in filename_bucket:
            filename_bucket.append(source_filename)

    return (
        hit_filenames_by_type,
        hit_requirement_labels_by_type,
        miss_requirement_labels_by_type,
        hit_evidence_preview_by_type,
        miss_evidence_preview_by_type,
    )


def _build_numeric_maps(
    retrieval_preview: Sequence[object],
    consistency_preview: Sequence[object],
    *,
    context: MaterialCardContext,
) -> Tuple[
    Dict[str, List[str]],
    Dict[str, List[str]],
    Dict[str, Dict[str, List[str]]],
    Dict[str, Dict[str, List[str]]],
]:
    hit_numeric_terms_by_type: Dict[str, List[str]] = {}
    expected_numeric_terms_by_type: Dict[str, List[str]] = {}
    hit_numeric_categories_by_type: Dict[str, Dict[str, List[str]]] = {}
    expected_numeric_categories_by_type: Dict[str, Dict[str, List[str]]] = {}

    for row in retrieval_preview:
        if not isinstance(row, dict):
            continue
        mat_type = context.normalize_material_type(row.get("material_type"))
        if not mat_type:
            continue
        token_bucket = hit_numeric_terms_by_type.setdefault(mat_type, [])
        category = context.classify_numeric_anchor_category(
            terms=row.get("matched_terms") if isinstance(row.get("matched_terms"), list) else [],
            material_type=mat_type,
            dimension_id=row.get("dimension_id"),
            label=row.get("filename"),
        )
        for raw in row.get("matched_numeric_terms") or []:
            token = context.normalize_numeric_token(raw)
            if token and token not in token_bucket:
                token_bucket.append(token)
            if token:
                context.append_numeric_anchor_bucket(
                    hit_numeric_categories_by_type.setdefault(mat_type, {}),
                    category,
                    token,
                )

    for row in consistency_preview:
        if not isinstance(row, dict):
            continue
        mat_type = context.normalize_material_type(row.get("material_type"))
        if not mat_type:
            continue
        token_bucket = expected_numeric_terms_by_type.setdefault(mat_type, [])
        category = context.classify_numeric_anchor_category(
            terms=row.get("terms") if isinstance(row.get("terms"), list) else [],
            material_type=mat_type,
            dimension_id=row.get("dimension_id"),
            label=row.get("label"),
        )
        for raw in row.get("numbers") or []:
            token = context.normalize_numeric_token(raw)
            if token and token not in token_bucket:
                token_bucket.append(token)
            if token:
                context.append_numeric_anchor_bucket(
                    expected_numeric_categories_by_type.setdefault(mat_type, {}),
                    category,
                    token,
                )

    return (
        hit_numeric_terms_by_type,
        expected_numeric_terms_by_type,
        hit_numeric_categories_by_type,
        expected_numeric_categories_by_type,
    )


def build_material_type_cards(
    *,
    material_rows: Sequence[Dict[str, object]],
    material_depth: Dict[str, object],
    material_knowledge: Dict[str, object],
    readiness: Dict[str, object],
    latest_submission: Optional[Dict[str, object]] = None,
    basis_util: Dict[str, object],
    basis_retrieval: Dict[str, object],
    conflict_summary: Dict[str, object],
    requirement_hits: Sequence[object],
    context: MaterialCardContext,
) -> List[Dict[str, object]]:
    depth_gate = (
        material_depth.get("depth_gate")
        if isinstance(material_depth.get("depth_gate"), dict)
        else {}
    )
    material_gate = (
        readiness.get("material_gate") if isinstance(readiness.get("material_gate"), dict) else {}
    )
    depth_rows = (
        material_depth.get("by_type") if isinstance(material_depth.get("by_type"), list) else []
    )
    depth_by_type: Dict[str, Dict[str, object]] = {}
    for row in depth_rows:
        if not isinstance(row, dict):
            continue
        mat_type = context.normalize_material_type(row.get("material_type"))
        if mat_type:
            depth_by_type[mat_type] = row

    knowledge_rows = (
        material_knowledge.get("by_type")
        if isinstance(material_knowledge.get("by_type"), list)
        else []
    )
    (
        knowledge_by_type_map,
        knowledge_numeric_terms_by_type,
        knowledge_numeric_summary_by_type,
    ) = _build_knowledge_maps(knowledge_rows, context=context)

    util_by_type = basis_util.get("by_type") if isinstance(basis_util.get("by_type"), dict) else {}
    required_types = (
        material_gate.get("required_types")
        if isinstance(material_gate.get("required_types"), list)
        else []
    )
    available_types = (
        basis_util.get("available_types")
        if isinstance(basis_util.get("available_types"), list)
        else []
    )
    latest_submission = latest_submission if isinstance(latest_submission, dict) else {}
    latest_submission_exists = bool(latest_submission.get("exists"))
    latest_submission_scored = bool(latest_submission.get("is_scored"))
    normalized_required_types = {
        context.normalize_material_type(item)
        for item in required_types
        if context.normalize_material_type(item)
    }
    normalized_available_types = {
        context.normalize_material_type(item)
        for item in available_types
        if context.normalize_material_type(item)
    }
    all_types: List[str] = []
    for source in (
        DEFAULT_MATERIAL_TYPE_ORDER,
        list(normalized_required_types),
        list(normalized_available_types),
        list(depth_by_type.keys()),
        list(util_by_type.keys()),
    ):
        for item in source:
            key = context.normalize_material_type(item)
            if key and key not in all_types:
                all_types.append(key)

    uploaded_filenames_by_type: Dict[str, List[str]] = {}
    for row in material_rows:
        mat_type = context.normalize_material_type(
            row.get("material_type"), filename=row.get("filename")
        )
        filename = str(row.get("filename") or "").strip()
        if not mat_type or not filename:
            continue
        bucket = uploaded_filenames_by_type.setdefault(mat_type, [])
        if filename not in bucket:
            bucket.append(filename)

    (
        hit_filenames_by_type,
        hit_requirement_labels_by_type,
        miss_requirement_labels_by_type,
        hit_evidence_preview_by_type,
        miss_evidence_preview_by_type,
    ) = _build_requirement_maps(
        requirement_hits,
        normalize_material_type=context.normalize_material_type,
    )

    retrieval_preview = (
        basis_retrieval.get("preview") if isinstance(basis_retrieval.get("preview"), list) else []
    )
    consistency_preview = (
        basis_retrieval.get("consistency_preview")
        if isinstance(basis_retrieval.get("consistency_preview"), list)
        else []
    )
    (
        hit_numeric_terms_by_type,
        expected_numeric_terms_by_type,
        hit_numeric_categories_by_type,
        expected_numeric_categories_by_type,
    ) = _build_numeric_maps(
        retrieval_preview,
        consistency_preview,
        context=context,
    )

    conflict_labels_by_type: Dict[str, List[str]] = {}
    conflict_rows = (
        conflict_summary.get("conflicts")
        if isinstance(conflict_summary.get("conflicts"), list)
        else []
    )
    for row in conflict_rows:
        if not isinstance(row, dict):
            continue
        mat_type = context.normalize_material_type(row.get("material_type"))
        label = str(row.get("label") or row.get("conflict_kind") or "").strip()
        if not mat_type or not label:
            continue
        bucket = conflict_labels_by_type.setdefault(mat_type, [])
        if label not in bucket:
            bucket.append(label)

    cards: List[Dict[str, object]] = []
    for mat_type in all_types:
        parse_rows = [
            row
            for row in material_rows
            if context.normalize_material_type(
                row.get("material_type"), filename=row.get("filename")
            )
            == mat_type
        ]
        parse_status_counts: Dict[str, int] = {}
        parse_backend_counts: Dict[str, int] = {}
        parse_error_preview: List[str] = []
        parse_confidence_values: List[float] = []
        queued_count = 0
        processing_count = 0
        failed_count = 0
        parsed_count = 0
        for parse_row in parse_rows:
            parse_status = str(parse_row.get("parse_status") or "queued").strip().lower()
            parse_status_counts[parse_status] = parse_status_counts.get(parse_status, 0) + 1
            if parse_status == "queued":
                queued_count += 1
            elif parse_status == "processing":
                processing_count += 1
            elif parse_status == "failed":
                failed_count += 1
            elif parse_status == "parsed":
                parsed_count += 1
            backend_key = str(parse_row.get("parse_backend") or "").strip()
            if backend_key:
                parse_backend_counts[backend_key] = parse_backend_counts.get(backend_key, 0) + 1
            conf = context.to_float_or_none(parse_row.get("parse_confidence"))
            if conf is not None:
                parse_confidence_values.append(float(conf))
            error_text = str(
                parse_row.get("parse_error_message") or parse_row.get("parse_error_class") or ""
            ).strip()
            if error_text and error_text not in parse_error_preview:
                parse_error_preview.append(error_text[:120])

        depth_row = (
            depth_by_type.get(mat_type) if isinstance(depth_by_type.get(mat_type), dict) else {}
        )
        util_row = (
            util_by_type.get(mat_type) if isinstance(util_by_type.get(mat_type), dict) else {}
        )
        knowledge_row = (
            knowledge_by_type_map.get(mat_type)
            if isinstance(knowledge_by_type_map.get(mat_type), dict)
            else {}
        )

        files = int(context.to_float_or_none(depth_row.get("files")) or 0)
        parsed_chars = int(context.to_float_or_none(depth_row.get("parsed_chars")) or 0)
        parsed_chunks = int(context.to_float_or_none(depth_row.get("parsed_chunks")) or 0)
        numeric_terms = int(context.to_float_or_none(depth_row.get("numeric_terms")) or 0)
        retrieval_total = int(context.to_float_or_none(util_row.get("retrieval_total")) or 0)
        retrieval_hit = int(context.to_float_or_none(util_row.get("retrieval_hit")) or 0)
        consistency_total = int(context.to_float_or_none(util_row.get("consistency_total")) or 0)
        consistency_hit = int(context.to_float_or_none(util_row.get("consistency_hit")) or 0)
        fallback_total = int(context.to_float_or_none(util_row.get("fallback_total")) or 0)
        fallback_hit = int(context.to_float_or_none(util_row.get("fallback_hit")) or 0)
        has_evidence = (retrieval_hit + consistency_hit + fallback_hit) > 0
        required = mat_type in normalized_required_types
        in_scope = mat_type in normalized_available_types or files > 0 or required

        uploaded_filenames = list(uploaded_filenames_by_type.get(mat_type) or [])
        hit_filenames = list(hit_filenames_by_type.get(mat_type) or [])
        hit_requirement_labels = list(hit_requirement_labels_by_type.get(mat_type) or [])
        miss_requirement_labels = list(miss_requirement_labels_by_type.get(mat_type) or [])
        hit_evidence_preview = list(hit_evidence_preview_by_type.get(mat_type) or [])
        miss_evidence_preview = list(miss_evidence_preview_by_type.get(mat_type) or [])
        conflict_labels = list(conflict_labels_by_type.get(mat_type) or [])
        project_numeric_terms = list(knowledge_numeric_terms_by_type.get(mat_type) or [])
        project_numeric_category_summary = list(
            knowledge_numeric_summary_by_type.get(mat_type) or []
        )
        hit_numeric_terms = list(hit_numeric_terms_by_type.get(mat_type) or [])
        expected_numeric_terms = list(expected_numeric_terms_by_type.get(mat_type) or [])
        missing_numeric_terms = [
            token for token in expected_numeric_terms if token not in hit_numeric_terms
        ]
        hit_numeric_categories = {
            key: list(value)
            for key, value in (hit_numeric_categories_by_type.get(mat_type) or {}).items()
        }
        expected_numeric_categories = {
            key: list(value)
            for key, value in (expected_numeric_categories_by_type.get(mat_type) or {}).items()
        }
        missing_numeric_categories: Dict[str, List[str]] = {}
        for category, tokens in expected_numeric_categories.items():
            hit_tokens = set(hit_numeric_categories.get(category) or [])
            for token in tokens:
                if token not in hit_tokens:
                    context.append_numeric_anchor_bucket(
                        missing_numeric_categories, category, token
                    )

        meets_chars = bool(depth_row.get("meets_chars")) if depth_row else False
        meets_chunks = bool(depth_row.get("meets_chunks")) if depth_row else False
        meets_numeric_terms = bool(depth_row.get("meets_numeric_terms")) if depth_row else False
        structured_quality_score = float(
            context.to_float_or_none(knowledge_row.get("structured_quality_score")) or 0.0
        )
        structured_quality_max = float(
            context.to_float_or_none(knowledge_row.get("structured_quality_max")) or 0.0
        )
        structured_quality_signal_coverage = float(
            context.to_float_or_none(knowledge_row.get("structured_quality_signal_coverage")) or 0.0
        )

        guidance: List[str] = []
        if files <= 0 and required:
            status = "missing"
            status_label = "缺失"
            guidance.append(
                f"缺少{context.material_type_label(mat_type)}，当前不能形成完整评分依据。"
            )
        elif processing_count > 0:
            status = "processing"
            status_label = "解析中"
            guidance.append(
                f"{context.material_type_label(mat_type)}正在后台深读，完成后才会进入评分。"
            )
        elif queued_count > 0:
            status = "queued"
            status_label = "排队中"
            guidance.append(
                f"{context.material_type_label(mat_type)}已进入深读队列，请等待解析完成。"
            )
        elif parsed_count <= 0 and failed_count > 0:
            status = "failed"
            status_label = "解析失败"
            guidance.append(
                f"{context.material_type_label(mat_type)}解析失败，请重试或更换更清晰的源文件。"
            )
        elif files <= 0:
            status = "idle"
            status_label = "未上传"
            guidance.append(f"{context.material_type_label(mat_type)}未上传，当前按可选资料处理。")
        elif parsed_chunks <= 0 or parsed_chars <= 0:
            status = "uploaded_unparsed"
            status_label = "已上传未解析"
            guidance.append(
                f"{context.material_type_label(mat_type)}已上传，但尚未形成可检索文本。"
            )
        elif has_evidence:
            status = "active"
            status_label = "已参与评分"
            guidance.append(f"{context.material_type_label(mat_type)}已进入评分证据链。")
        elif latest_submission_exists and not latest_submission_scored:
            status = "parsed_ready"
            status_label = "已解析待评分"
            guidance.append(
                f"{context.material_type_label(mat_type)}已解析，待完成施组评分后自动进入证据链。"
            )
        elif not latest_submission_exists:
            status = "parsed_ready"
            status_label = "已解析待施组"
            guidance.append(
                f"{context.material_type_label(mat_type)}已解析，待上传施组后自动进入评分证据链。"
            )
        else:
            status = "parsed_not_used"
            status_label = "已解析待补证据"
            guidance.append(
                f"{context.material_type_label(mat_type)}已解析，但当前施组评分尚未命中到有效证据。"
            )

        if files > 0 and not meets_chars and required:
            guidance.append(
                f"{context.material_type_label(mat_type)}解析字数不足，建议补齐关键章节或提升可解析版本。"
            )
        if files > 0 and not meets_chunks and bool(depth_gate.get("enforce")):
            guidance.append(
                f"{context.material_type_label(mat_type)}分块不足，建议补充更多结构化约束内容。"
            )
        if files > 0 and not meets_numeric_terms and mat_type in {"tender_qa", "boq", "drawing"}:
            guidance.append(
                f"{context.material_type_label(mat_type)}中的数值约束不足，建议补充工期、规格、阈值、清单量等硬参数。"
            )
        if files > 0 and structured_quality_score < 0.35:
            guidance.append(
                f"{context.material_type_label(mat_type)}已形成结构化解析，但质量偏弱，建议补充章节标题、评分点、强制条款或更清晰的硬参数。"
            )

        cards.append(
            {
                "material_type": mat_type,
                "material_type_label": context.material_type_label(mat_type),
                "required": required,
                "in_scope": in_scope,
                "status": status,
                "status_label": status_label,
                "files": files,
                "uploaded_filenames": uploaded_filenames[:20],
                "uploaded_filename_count": len(uploaded_filenames),
                "parsed_chars": parsed_chars,
                "parsed_chunks": parsed_chunks,
                "numeric_terms": numeric_terms,
                "hit_requirement_labels": hit_requirement_labels[:12],
                "hit_requirement_count": len(hit_requirement_labels),
                "miss_requirement_labels": miss_requirement_labels[:12],
                "miss_requirement_count": len(miss_requirement_labels),
                "hit_evidence_preview": hit_evidence_preview[:6],
                "miss_evidence_preview": miss_evidence_preview[:6],
                "conflict_labels": conflict_labels[:12],
                "conflict_label_count": len(conflict_labels),
                "project_numeric_terms": project_numeric_terms[:12],
                "project_numeric_term_count": len(project_numeric_terms),
                "project_numeric_category_summary": project_numeric_category_summary[:8],
                "structured_quality_score": round(structured_quality_score, 4),
                "structured_quality_max": round(structured_quality_max, 4),
                "structured_quality_signal_coverage": round(structured_quality_signal_coverage, 4),
                "parse_status_counts": parse_status_counts,
                "queued_count": queued_count,
                "processing_count": processing_count,
                "parsed_count": parsed_count,
                "failed_count": failed_count,
                "parse_backend_summary": [
                    (("GPT-5.4" if str(key).startswith("gpt") else str(key)) + "×" + str(value))
                    for key, value in sorted(
                        parse_backend_counts.items(),
                        key=lambda item: (-item[1], item[0]),
                    )
                ][:6],
                "parse_confidence_avg": round(
                    sum(parse_confidence_values) / max(1, len(parse_confidence_values)),
                    4,
                )
                if parse_confidence_values
                else 0.0,
                "parse_confidence_max": round(max(parse_confidence_values), 4)
                if parse_confidence_values
                else 0.0,
                "parse_error_preview": parse_error_preview[:4],
                "hit_numeric_terms": hit_numeric_terms[:12],
                "hit_numeric_term_count": len(hit_numeric_terms),
                "expected_numeric_terms": expected_numeric_terms[:12],
                "expected_numeric_term_count": len(expected_numeric_terms),
                "missing_numeric_terms": missing_numeric_terms[:12],
                "missing_numeric_term_count": len(missing_numeric_terms),
                "hit_numeric_category_summary": context.build_numeric_anchor_category_summary(
                    hit_numeric_categories
                ),
                "expected_numeric_category_summary": context.build_numeric_anchor_category_summary(
                    expected_numeric_categories
                ),
                "missing_numeric_category_summary": context.build_numeric_anchor_category_summary(
                    missing_numeric_categories
                ),
                "meets_chars": meets_chars,
                "meets_chunks": meets_chunks,
                "meets_numeric_terms": meets_numeric_terms,
                "retrieval_hit": retrieval_hit,
                "retrieval_total": retrieval_total,
                "consistency_hit": consistency_hit,
                "consistency_total": consistency_total,
                "fallback_hit": fallback_hit,
                "fallback_total": fallback_total,
                "has_evidence": has_evidence,
                "hit_filenames": hit_filenames[:20],
                "hit_filename_count": len(hit_filenames),
                "guidance": guidance[:3],
            }
        )

    return cards


def build_project_scoring_summary(
    *,
    readiness: Dict[str, object],
    parse_job_summary: Dict[str, object],
    material_rows: Sequence[Dict[str, object]],
    quality_summary: Dict[str, object],
    latest_submission: Dict[str, object],
    trace_summary: Dict[str, object],
    basis_util: Dict[str, object],
    basis_gate: Dict[str, object],
    conflict_summary: Dict[str, object],
    knowledge_summary: Dict[str, object],
    basis_runtime_constraints: Dict[str, object],
    to_float_or_none: Callable[[Any], Optional[float]],
) -> Dict[str, object]:
    feedback_evolution_requirements = int(
        to_float_or_none((basis_runtime_constraints or {}).get("feedback_evolution_requirements"))
        or 0
    )
    feature_confidence_requirements = int(
        to_float_or_none((basis_runtime_constraints or {}).get("feature_confidence_requirements"))
        or 0
    )
    return {
        "ready_to_score": bool(readiness.get("ready")),
        "material_gate_passed": bool(readiness.get("gate_passed")),
        "parse_job_summary": parse_job_summary,
        "parse_total_jobs": int(to_float_or_none(parse_job_summary.get("total_jobs")) or 0),
        "parse_backlog": int(to_float_or_none(parse_job_summary.get("backlog")) or 0),
        "parse_failed_jobs": int(to_float_or_none(parse_job_summary.get("failed_jobs")) or 0),
        "parse_gpt_ratio": to_float_or_none(parse_job_summary.get("gpt_ratio")),
        "parsed_materials": sum(
            1 for row in material_rows if str(row.get("parse_status") or "") == "parsed"
        ),
        "queued_materials": sum(
            1 for row in material_rows if str(row.get("parse_status") or "") == "queued"
        ),
        "processing_materials": sum(
            1 for row in material_rows if str(row.get("parse_status") or "") == "processing"
        ),
        "failed_materials": sum(
            1 for row in material_rows if str(row.get("parse_status") or "") == "failed"
        ),
        "material_files": int(to_float_or_none(quality_summary.get("total_files")) or 0),
        "material_parsed_chars": int(
            to_float_or_none(quality_summary.get("total_parsed_chars")) or 0
        ),
        "material_parsed_chunks": int(
            to_float_or_none(quality_summary.get("total_parsed_chunks")) or 0
        ),
        "latest_submission_exists": bool(latest_submission.get("exists")),
        "latest_submission_scored": bool(latest_submission.get("is_scored")),
        "evidence_total_requirements": int(
            to_float_or_none(trace_summary.get("total_requirements")) or 0
        ),
        "evidence_total_hits": int(to_float_or_none(trace_summary.get("total_hits")) or 0),
        "evidence_mandatory_hit_rate": to_float_or_none(trace_summary.get("mandatory_hit_rate")),
        "evidence_source_files_hit_count": int(
            to_float_or_none(trace_summary.get("source_files_hit_count")) or 0
        ),
        "retrieval_hit_rate": to_float_or_none(basis_util.get("retrieval_hit_rate")),
        "retrieval_file_coverage_rate": to_float_or_none(
            basis_util.get("retrieval_file_coverage_rate")
        ),
        "material_dimension_hit_rate": to_float_or_none(
            basis_util.get("material_dimension_hit_rate")
        ),
        "material_dimension_hit": int(
            to_float_or_none(basis_util.get("material_dimension_hit")) or 0
        ),
        "material_dimension_total": int(
            to_float_or_none(basis_util.get("material_dimension_total")) or 0
        ),
        "material_profile_query_terms_count": int(
            to_float_or_none(basis_util.get("material_profile_query_terms_count")) or 0
        ),
        "material_profile_query_numeric_terms_count": int(
            to_float_or_none(basis_util.get("material_profile_query_numeric_terms_count")) or 0
        ),
        "material_profile_focus_dimensions": [
            str(item or "").strip()
            for item in (basis_util.get("material_profile_focus_dimensions") or [])
            if str(item or "").strip()
        ][:8],
        "material_utilization_gate_blocked": bool(basis_gate.get("blocked")),
        "material_conflict_count": int(
            to_float_or_none(conflict_summary.get("conflict_count")) or 0
        ),
        "material_conflict_high_severity_count": int(
            to_float_or_none(conflict_summary.get("high_severity_count")) or 0
        ),
        "material_dimension_coverage_rate": to_float_or_none(
            knowledge_summary.get("dimension_coverage_rate")
        ),
        "material_structured_signal_total": int(
            to_float_or_none(knowledge_summary.get("structured_signal_total")) or 0
        ),
        "material_structured_quality_avg": to_float_or_none(
            knowledge_summary.get("structured_quality_avg")
        ),
        "material_structured_quality_max": to_float_or_none(
            knowledge_summary.get("structured_quality_max")
        ),
        "material_structured_quality_type_rate": to_float_or_none(
            knowledge_summary.get("structured_quality_type_rate")
        ),
        "material_strong_structured_types": int(
            to_float_or_none(knowledge_summary.get("strong_structured_types")) or 0
        ),
        "material_low_coverage_dimensions": int(
            to_float_or_none(knowledge_summary.get("low_coverage_dimensions")) or 0
        ),
        "material_covered_dimensions": int(
            to_float_or_none(knowledge_summary.get("covered_dimensions")) or 0
        ),
        "material_numeric_category_summary": [
            str(item or "").strip()
            for item in (knowledge_summary.get("numeric_category_summary") or [])
            if str(item or "").strip()
        ][:8],
        "current_weights_source": str(
            (basis_runtime_constraints or {}).get("weights_source") or "-"
        ),
        "current_effective_multipliers_preview": list(
            ((basis_runtime_constraints or {}).get("effective_multipliers_preview") or [])[:6]
        ),
        "current_feedback_evolution_requirements": feedback_evolution_requirements,
        "current_feature_confidence_requirements": feature_confidence_requirements,
        "recent_feedback_context_active": bool(
            feedback_evolution_requirements > 0 or feature_confidence_requirements > 0
        ),
        "latest_score_self_awareness": (
            latest_submission.get("score_self_awareness")
            if isinstance(latest_submission.get("score_self_awareness"), dict)
            else {}
        ),
        "latest_score_confidence_level": str(latest_submission.get("score_confidence_level") or ""),
    }


def collect_project_scoring_recommendations(
    *buckets: Sequence[object],
    latest_submission_exists: bool,
) -> List[str]:
    recommendations: List[str] = []
    for bucket in buckets:
        if not isinstance(bucket, list):
            continue
        for item in bucket:
            text = str(item or "").strip()
            if text and text not in recommendations:
                recommendations.append(text)
    if not latest_submission_exists:
        recommendations.insert(0, "暂无施组评分证据链，请先上传并评分至少 1 份施组。")
    return recommendations[:20]
