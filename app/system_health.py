from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple


def build_readiness_status(
    *,
    load_config: Callable[[], Any],
    ensure_data_dirs: Callable[[], None],
    validate_runtime_security_settings: Optional[Callable[[], None]] = None,
) -> Dict[str, object]:
    checks: Dict[str, bool] = {}

    try:
        load_config()
        checks["config"] = True
    except Exception:
        checks["config"] = False

    try:
        ensure_data_dirs()
        checks["data_dirs"] = True
    except Exception:
        checks["data_dirs"] = False

    if validate_runtime_security_settings is not None:
        try:
            validate_runtime_security_settings()
            checks["runtime_security"] = True
        except Exception:
            checks["runtime_security"] = False

    return {
        "status": "ready" if checks and all(checks.values()) else "not_ready",
        "checks": checks,
    }


@dataclass(frozen=True)
class SystemSelfCheckContext:
    required_item_names: Sequence[str]
    load_config: Callable[[], Any]
    ensure_data_dirs: Callable[[], None]
    storage_probe_dir: str
    get_auth_status: Callable[[], Dict[str, Any]]
    get_rate_limit_status: Callable[[], Dict[str, Any]]
    get_runtime_security_status: Callable[[], Dict[str, Any]]
    is_secure_desktop_mode_enabled: Callable[[], bool]
    get_openai_api_key: Callable[[], Optional[str]]
    get_openai_model: Callable[[], str]
    pdf_backend_name: Callable[[], str]
    document_class: Any
    pytesseract_module: Any
    image_module: Any
    resolve_dwg_converter_binaries: Callable[[], List[str]]
    build_material_parse_jobs_summary: Callable[[Optional[str]], Tuple[Any, Dict[str, object]]]
    to_float_or_none: Callable[[Any], Optional[float]]
    material_parse_worker: Any
    material_parse_backlog_warn: int
    load_materials: Callable[[], List[Dict[str, object]]]
    normalize_material_row_for_parse: Callable[[Dict[str, object]], Tuple[Dict[str, object], bool]]
    build_data_hygiene_report: Callable[..., Dict[str, object]]
    load_projects: Callable[[], List[Dict[str, object]]]
    load_submissions: Callable[[], List[Dict[str, object]]]
    build_scoring_readiness: Callable[[str, Dict[str, object]], Dict[str, object]]
    material_type_label: Callable[[object], str]
    now_iso: Callable[[], str]


def run_system_self_check(
    project_id: Optional[str],
    *,
    context: SystemSelfCheckContext,
) -> Dict[str, object]:
    items: List[Dict[str, object]] = []

    def add(
        name: str,
        ok: bool,
        detail: str = "",
        *,
        category: str = "runtime",
        required: Optional[bool] = None,
    ) -> None:
        is_required = (
            bool(required)
            if required is not None
            else str(name) in set(context.required_item_names)
        )
        items.append(
            {
                "name": name,
                "ok": bool(ok),
                "detail": detail or None,
                "category": category,
                "required": is_required,
            }
        )

    auth_enabled: Optional[bool] = None
    rate_limit_enabled: Optional[bool] = None
    pdf_backend = "none"
    ocr_available = False
    dwg_converter_found = False
    data_hygiene_orphan_count = 0
    data_hygiene_impacted = 0
    project_name: Optional[str] = None
    project_material_count: Optional[int] = None
    project_submission_count: Optional[int] = None
    project_ready: Optional[bool] = None
    project_gate_passed: Optional[bool] = None
    project_issue_count: Optional[int] = None
    project_warning_count: Optional[int] = None
    project_missing_required_types: List[str] = []
    project_issues_preview = "-"
    project_warnings_preview = "-"
    parse_job_summary: Dict[str, object] = {}
    openai_api_available = False
    structured_summary_schema_ok = True

    add("health", True, "service reachable", category="service")

    try:
        context.load_config()
        add("config", True, "rubric/lexicon loaded", category="config")
    except Exception as exc:
        add("config", False, str(exc), category="config")

    try:
        context.ensure_data_dirs()
        with tempfile.NamedTemporaryFile(
            prefix="selfcheck_",
            suffix=".tmp",
            dir=context.storage_probe_dir,
            delete=True,
        ):
            pass
        add("data_dirs_writable", True, "data directory writable", category="storage")
    except Exception as exc:
        add("data_dirs_writable", False, str(exc), category="storage")

    try:
        auth_status = context.get_auth_status()
        auth_enabled = bool(auth_status.get("auth_enabled", auth_status.get("enabled", False)))
        add("auth_status", True, f"enabled={auth_enabled}", category="security", required=False)
    except Exception as exc:
        add("auth_status", False, str(exc), category="security", required=False)

    try:
        rate_limit_status = context.get_rate_limit_status()
        rate_limit_enabled = bool(rate_limit_status.get("enabled"))
        add(
            "rate_limit_status",
            True,
            f"enabled={rate_limit_enabled}",
            category="security",
            required=False,
        )
    except Exception as exc:
        add("rate_limit_status", False, str(exc), category="security", required=False)

    try:
        runtime_status = context.get_runtime_security_status()
        production_mode = bool(runtime_status.get("production_mode"))
        allowed_hosts_configured = bool(runtime_status.get("allowed_hosts_configured"))
        upload_limit_enabled = bool(runtime_status.get("upload_limit_enabled"))
        api_docs_enabled = bool(runtime_status.get("api_docs_enabled"))
        require_api_keys = bool(runtime_status.get("require_api_keys"))
        runtime_ok = True
        detail_parts = [
            f"production={production_mode}",
            f"api_docs={api_docs_enabled}",
            f"allowed_hosts={allowed_hosts_configured}",
            f"upload_limit={upload_limit_enabled}",
            f"require_api_keys={require_api_keys}",
        ]
        if production_mode and api_docs_enabled:
            runtime_ok = False
        if (
            production_mode
            and not allowed_hosts_configured
            and not context.is_secure_desktop_mode_enabled()
        ):
            runtime_ok = False
        add(
            "runtime_security",
            runtime_ok,
            "; ".join(detail_parts),
            category="security",
            required=False,
        )
    except Exception as exc:
        add("runtime_security", False, str(exc), category="security", required=False)

    openai_api_available = bool(context.get_openai_api_key())
    add(
        "openai_api_available",
        openai_api_available,
        f"model={context.get_openai_model()}" if openai_api_available else "OPENAI_API_KEY missing",
        category="llm",
        required=False,
    )

    pdf_backend = context.pdf_backend_name()
    add(
        "parser_pdf",
        pdf_backend != "none",
        (f"backend={pdf_backend}" if pdf_backend != "none" else "PyMuPDF/pypdf missing"),
        category="parser",
        required=False,
    )
    add(
        "parser_docx",
        context.document_class is not None,
        "python-docx available" if context.document_class is not None else "python-docx missing",
        category="parser",
        required=False,
    )

    ocr_available = bool(
        context.pytesseract_module is not None and context.image_module is not None
    )
    if ocr_available:
        try:
            version = str(context.pytesseract_module.get_tesseract_version())
            add("parser_ocr", True, f"tesseract={version}", category="parser", required=False)
        except Exception:
            add("parser_ocr", True, "pytesseract available", category="parser", required=False)
    else:
        add("parser_ocr", False, "pytesseract or PIL missing", category="parser", required=False)

    try:
        dwg_bins = context.resolve_dwg_converter_binaries()
        dwg_converter_found = bool(dwg_bins)
        add(
            "parser_dwg_converter",
            dwg_converter_found,
            f"found={','.join(Path(path).name for path in dwg_bins)}" if dwg_bins else "not_found",
            category="parser",
            required=False,
        )
    except Exception as exc:
        add("parser_dwg_converter", False, str(exc), category="parser", required=False)

    try:
        _, parse_job_summary = context.build_material_parse_jobs_summary(project_id)
        backlog = int(context.to_float_or_none(parse_job_summary.get("backlog")) or 0)
        failed_jobs = int(context.to_float_or_none(parse_job_summary.get("failed_jobs")) or 0)
        total_jobs = int(context.to_float_or_none(parse_job_summary.get("total_jobs")) or 0)
        failure_rate = float(failed_jobs) / float(total_jobs) if total_jobs > 0 else 0.0
        worker_ok = bool(context.material_parse_worker and context.material_parse_worker.is_alive())
        add(
            "vision_parse_queue_healthy",
            worker_ok and backlog <= context.material_parse_backlog_warn,
            f"worker={worker_ok}, backlog={backlog}",
            category="async_parse",
            required=False,
        )
        add(
            "material_parse_backlog_ok",
            backlog <= context.material_parse_backlog_warn,
            f"backlog={backlog}",
            category="async_parse",
            required=False,
        )
        add(
            "gpt_parse_failure_rate_ok",
            failure_rate <= 0.35,
            f"failed_jobs={failed_jobs}, total_jobs={total_jobs}, rate={failure_rate:.1%}",
            category="async_parse",
            required=False,
        )
        parsed_rows = [
            row
            for row in (
                context.normalize_material_row_for_parse(dict(material))[0]
                for material in context.load_materials()
                if not project_id or str(material.get("project_id") or "") == str(project_id)
            )
            if str(row.get("parse_status") or "") == "parsed"
        ]
        if parsed_rows:
            structured_summary_schema_ok = all(
                isinstance(row.get("structured_summary"), dict) for row in parsed_rows[:20]
            )
        add(
            "structured_summary_schema_ok",
            structured_summary_schema_ok,
            f"parsed_rows={len(parsed_rows)}",
            category="async_parse",
            required=False,
        )
    except Exception as exc:
        add("vision_parse_queue_healthy", False, str(exc), category="async_parse", required=False)
        add("material_parse_backlog_ok", False, str(exc), category="async_parse", required=False)
        add("gpt_parse_failure_rate_ok", False, str(exc), category="async_parse", required=False)
        add("structured_summary_schema_ok", False, str(exc), category="async_parse", required=False)

    try:
        hygiene = context.build_data_hygiene_report(apply=False)
        data_hygiene_orphan_count = int(
            context.to_float_or_none(hygiene.get("orphan_records_total")) or 0
        )
        data_hygiene_impacted = sum(
            1
            for row in (hygiene.get("datasets") or [])
            if int(context.to_float_or_none((row or {}).get("orphan_count")) or 0) > 0
        )
        add(
            "data_hygiene",
            data_hygiene_orphan_count == 0,
            (
                f"orphan_records={data_hygiene_orphan_count}, "
                f"impacted_datasets={data_hygiene_impacted}"
            ),
            category="data",
            required=False,
        )
    except Exception as exc:
        add("data_hygiene", False, str(exc), category="data", required=False)

    if project_id:
        try:
            projects = context.load_projects()
            target = next((row for row in projects if str(row.get("id")) == str(project_id)), None)
            if target is None:
                add(
                    "project_exists",
                    False,
                    f"project not found: {project_id}",
                    category="project",
                )
            else:
                project_name = str(target.get("name") or project_id)
                add("project_exists", True, project_name, category="project")
                try:
                    project_material_count = len(
                        [
                            row
                            for row in context.load_materials()
                            if str(row.get("project_id")) == str(project_id)
                        ]
                    )
                    add(
                        "project_materials_listable",
                        True,
                        f"count={project_material_count}",
                        category="project",
                    )
                except Exception as exc:
                    add("project_materials_listable", False, str(exc), category="project")
                try:
                    project_submission_count = len(
                        [
                            row
                            for row in context.load_submissions()
                            if str(row.get("project_id")) == str(project_id)
                        ]
                    )
                    add(
                        "project_submissions_listable",
                        True,
                        f"count={project_submission_count}",
                        category="project",
                    )
                except Exception as exc:
                    add("project_submissions_listable", False, str(exc), category="project")
                try:
                    readiness = context.build_scoring_readiness(str(project_id), target)
                    project_ready = bool(readiness.get("ready"))
                    project_gate_passed = bool(readiness.get("gate_passed"))
                    issues = (
                        readiness.get("issues") if isinstance(readiness.get("issues"), list) else []
                    )
                    warnings = (
                        readiness.get("warnings")
                        if isinstance(readiness.get("warnings"), list)
                        else []
                    )
                    material_gate = (
                        readiness.get("material_gate")
                        if isinstance(readiness.get("material_gate"), dict)
                        else {}
                    )
                    project_issue_count = len(issues)
                    project_warning_count = len(warnings)
                    project_missing_required_types = [
                        str(item)
                        for item in (material_gate.get("missing_required_types") or [])
                        if str(item).strip()
                    ]
                    project_issues_preview = (
                        "；".join(str(item) for item in issues[:2]) if issues else "-"
                    )
                    project_warnings_preview = (
                        "；".join(str(item) for item in warnings[:2]) if warnings else "-"
                    )
                    add(
                        "project_scoring_readiness",
                        True,
                        (
                            f"ready={project_ready}, gate_passed={project_gate_passed}, "
                            f"issues={project_issues_preview}"
                        ),
                        category="project",
                    )
                except Exception as exc:
                    add("project_scoring_readiness", False, str(exc), category="project")
        except Exception as exc:
            add("project_exists", False, str(exc), category="project")

    required_item_names = set(context.required_item_names)
    required_items = [row for row in items if str(row.get("name")) in required_item_names]
    required_failures = [row for row in required_items if not bool(row.get("ok"))]
    optional_failures = [
        row
        for row in items
        if str(row.get("name")) not in required_item_names and not bool(row.get("ok"))
    ]
    all_ok = bool(required_items) and not required_failures
    checks = {
        str(row.get("name")): bool(row.get("ok")) for row in items if str(row.get("name")).strip()
    }
    parser_checks = {
        name: bool(checks.get(name))
        for name in ("parser_pdf", "parser_docx", "parser_ocr", "parser_dwg_converter")
    }
    passed_count = sum(1 for row in items if bool(row.get("ok")))
    missing_required_labels = [
        context.material_type_label(row)
        for row in project_missing_required_types
        if str(row).strip()
    ]
    summary = {
        "total_items": len(items),
        "passed_items": passed_count,
        "failed_items": len(items) - passed_count,
        "required_items": len(required_items),
        "optional_items": max(0, len(items) - len(required_items)),
        "failed_required_items": [
            str(row.get("name")) for row in required_failures if str(row.get("name")).strip()
        ],
        "failed_optional_items": [
            str(row.get("name")) for row in optional_failures if str(row.get("name")).strip()
        ],
        "parser_capabilities": parser_checks,
        "parser_capability_count": sum(1 for ok in parser_checks.values() if ok),
        "parser_capability_total": len(parser_checks),
        "auth_enabled": auth_enabled,
        "rate_limit_enabled": rate_limit_enabled,
        "pdf_backend": pdf_backend,
        "ocr_available": ocr_available,
        "dwg_converter_found": dwg_converter_found,
        "openai_api_available": openai_api_available,
        "parse_job_summary": parse_job_summary,
        "structured_summary_schema_ok": structured_summary_schema_ok,
        "data_hygiene_orphan_records": data_hygiene_orphan_count,
        "data_hygiene_impacted_datasets": data_hygiene_impacted,
        "project_id": project_id,
        "project_name": project_name,
        "project_material_count": project_material_count,
        "project_submission_count": project_submission_count,
        "project_ready": project_ready,
        "project_gate_passed": project_gate_passed,
        "project_issue_count": project_issue_count,
        "project_warning_count": project_warning_count,
        "project_missing_required_types": project_missing_required_types,
        "project_missing_required_labels": missing_required_labels,
        "project_issues_preview": project_issues_preview,
        "project_warnings_preview": project_warnings_preview,
    }
    return {
        "ok": all_ok,
        "required_ok": all_ok,
        "degraded": bool(optional_failures),
        "failed_required_count": len(required_failures),
        "failed_optional_count": len(optional_failures),
        "checked_at": context.now_iso(),
        "checks": checks,
        "summary": summary,
        "items": items,
    }
