from __future__ import annotations

from typing import Any, Mapping

from app.domain.material_types import normalize_material_type


def _to_float_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def default_material_parse_state(
    material_type: str,
    *,
    path: str = "",
    parse_version: str,
    now_iso: str,
) -> dict[str, object]:
    has_path = bool(str(path or "").strip())
    return {
        "parse_status": "queued" if has_path else "failed",
        "parse_backend": "queued" if has_path else "none",
        "content_hash": None,
        "parse_phase": None,
        "parse_ready_for_gate": False,
        "parse_confidence": 0.0,
        "parse_error_class": None if has_path else "missing_path",
        "parse_error_message": None if has_path else "missing_path",
        "parse_started_at": None,
        "parse_finished_at": None,
        "parse_version": parse_version,
        "structured_summary": None,
        "job_id": None,
        "parsed_text": "",
        "parsed_chars": 0,
        "parsed_chunks": [],
        "numeric_terms_norm": [],
        "lexical_terms": [],
        "updated_at": now_iso,
    }


def normalize_material_row_for_parse(
    row: Mapping[str, object],
    *,
    parse_version: str,
    now_iso: str,
) -> tuple[dict[str, object], bool]:
    normalized = dict(row)
    changed = False
    missing_parse_phase = "parse_phase" not in normalized
    missing_parse_ready_for_gate = "parse_ready_for_gate" not in normalized
    if "path" not in normalized:
        normalized["path"] = ""
        changed = True
    filename = str(normalized.get("filename") or "")
    material_type = normalize_material_type(normalized.get("material_type"), filename=filename)
    if str(normalized.get("material_type") or "") != material_type:
        normalized["material_type"] = material_type
        changed = True
    defaults = default_material_parse_state(
        material_type,
        path=str(normalized.get("path") or ""),
        parse_version=parse_version,
        now_iso=now_iso,
    )
    for key, value in defaults.items():
        if key not in normalized:
            normalized[key] = value
            changed = True
    parse_status = str(normalized.get("parse_status") or "").strip().lower()
    parse_phase = str(normalized.get("parse_phase") or "").strip().lower()
    if missing_parse_phase and parse_status == "parsed" and parse_phase != "preview":
        normalized["parse_phase"] = "full"
        parse_phase = "full"
        changed = True
    if missing_parse_ready_for_gate and parse_status == "parsed" and parse_phase != "preview":
        normalized["parse_ready_for_gate"] = True
        changed = True
    if str(normalized.get("parse_status") or "") == "processing":
        normalized["parse_status"] = "queued"
        normalized["parse_backend"] = "queued"
        normalized["parse_error_class"] = "worker_recovered"
        normalized["parse_error_message"] = "worker_recovered"
        normalized["parse_started_at"] = None
        normalized["updated_at"] = now_iso
        changed = True
    return normalized, changed


def material_parse_ready_for_gate(material_row: Mapping[str, object]) -> bool:
    if "parse_ready_for_gate" in material_row:
        return bool(material_row.get("parse_ready_for_gate"))
    parse_status = str(material_row.get("parse_status") or "").strip().lower()
    parse_phase = str(material_row.get("parse_phase") or "").strip().lower()
    return parse_status == "parsed" and parse_phase != "preview"


def material_parse_has_terminal_payload(material_row: Mapping[str, object]) -> bool:
    if str(material_row.get("parsed_text") or "").strip():
        return True
    if isinstance(material_row.get("structured_summary"), dict) and material_row.get(
        "structured_summary"
    ):
        return True
    if int(_to_float_or_none(material_row.get("parsed_chars")) or 0) > 0:
        return True
    if isinstance(material_row.get("parsed_chunks"), list) and material_row.get("parsed_chunks"):
        return True
    if isinstance(material_row.get("numeric_terms_norm"), list) and material_row.get(
        "numeric_terms_norm"
    ):
        return True
    if isinstance(material_row.get("lexical_terms"), list) and material_row.get("lexical_terms"):
        return True
    return False


def material_parse_status_label(
    parse_status: str | None,
    *,
    parse_backend: str | None = None,
    parse_error_message: str | None = None,
) -> str:
    status = str(parse_status or "").strip().lower()
    backend = str(parse_backend or "").strip()
    if status == "previewed":
        return "预解析完成"
    if status == "parsed":
        if backend.startswith("gpt"):
            return "已解析（GPT-5.4）"
        if backend:
            return f"已解析（{backend}）"
        return "已解析"
    if status == "processing":
        return "解析中"
    if status == "failed":
        detail = str(parse_error_message or "").strip()
        return f"解析失败：{detail[:36]}" if detail else "解析失败"
    if status == "queued":
        return "排队中"
    return "待解析"


def normalize_material_parse_job(
    job: Mapping[str, object],
    *,
    now_iso: str,
) -> tuple[dict[str, object], bool]:
    normalized = dict(job)
    changed = False
    filename = str(normalized.get("filename") or "")
    normalized_material_type = normalize_material_type(
        normalized.get("material_type"),
        filename=filename,
    )
    defaults: dict[str, Any] = {
        "id": "",
        "material_id": "",
        "project_id": "",
        "filename": "",
        "status": "queued",
        "attempt": 0,
        "parse_backend": None,
        "next_retry_at": None,
        "created_at": now_iso,
        "updated_at": now_iso,
        "started_at": None,
        "finished_at": None,
        "error_class": None,
        "error_message": None,
        "parse_confidence": 0.0,
        "parse_mode": "full",
        "followup_from_preview": False,
    }
    for key, value in defaults.items():
        if key not in normalized:
            normalized[key] = value
            changed = True
    if str(normalized.get("material_type") or "") != normalized_material_type:
        normalized["material_type"] = normalized_material_type
        changed = True
    return normalized, changed
