from __future__ import annotations

from app.system_health import (
    SystemSelfCheckContext,
    build_readiness_status,
    run_system_self_check,
)


def test_build_readiness_status_marks_runtime_security_failure_not_ready():
    def _load_config() -> None:
        return None

    def _ensure_data_dirs() -> None:
        return None

    def _validate_runtime_security_settings() -> None:
        raise RuntimeError("missing api keys")

    payload = build_readiness_status(
        load_config=_load_config,
        ensure_data_dirs=_ensure_data_dirs,
        validate_runtime_security_settings=_validate_runtime_security_settings,
        probe_config_completeness=lambda: {"ok": True, "detail": "complete"},
        probe_storage_lock_status=lambda: {"ok": True, "detail": "lock_ok"},
        probe_event_log_appendability=lambda: {"ok": True, "detail": "event_log_ok"},
    )

    assert payload["status"] == "not_ready"
    assert payload["checks"]["config"] is True
    assert payload["checks"]["data_dirs"] is True
    assert payload["checks"]["config_completeness"] is True
    assert payload["checks"]["storage_lock"] is True
    assert payload["checks"]["event_log_appendable"] is True
    assert payload["checks"]["runtime_security"] is False


def test_run_system_self_check_builds_summary_from_explicit_context(tmp_path):
    class DummyWorker:
        def is_alive(self) -> bool:
            return True

    class DummyTesseract:
        @staticmethod
        def get_tesseract_version() -> str:
            return "5.0.0"

    probe_dir = tmp_path / "data"
    probe_dir.mkdir()
    context = SystemSelfCheckContext(
        required_item_names={
            "health",
            "config",
            "data_dirs_writable",
            "auth_status",
            "rate_limit_status",
            "parser_pdf",
            "parser_docx",
        },
        load_config=lambda: {"rubric": {}, "lexicon": {}},
        ensure_data_dirs=lambda: None,
        storage_probe_dir=str(probe_dir),
        build_storage_backend_status=lambda: {
            "primary_backend": "json",
            "event_log_enabled": True,
            "sqlite_mirror_enabled": False,
        },
        probe_config_completeness=lambda: {"ok": True, "detail": "dimension_count=16"},
        probe_storage_lock_status=lambda: {"ok": True, "detail": "lock_ok"},
        probe_event_log_appendability=lambda: {"ok": True, "detail": "event_log_ok"},
        probe_projection_consistency=lambda: {"ok": True, "detail": "projection_ok"},
        probe_learning_artifact_versions=lambda: {"ok": True, "detail": "versions_ok"},
        probe_agent_dependency_health=lambda: {"ok": True, "detail": "agents=3"},
        probe_scoring_replay_consistency=lambda project_id: {
            "ok": True,
            "detail": "skipped:no_scored_submission_available",
        },
        get_auth_status=lambda: {"enabled": False},
        get_rate_limit_status=lambda: {"enabled": True},
        get_runtime_security_status=lambda: {
            "production_mode": True,
            "allowed_hosts_configured": True,
            "upload_limit_enabled": True,
            "api_docs_enabled": False,
            "require_api_keys": True,
        },
        is_secure_desktop_mode_enabled=lambda: False,
        get_openai_api_key=lambda: "sk-test",
        get_openai_model=lambda: "gpt-5.4",
        pdf_backend_name=lambda: "pymupdf",
        document_class=object(),
        pytesseract_module=DummyTesseract(),
        image_module=object(),
        resolve_dwg_converter_binaries=lambda: ["/usr/local/bin/dwg2dxf"],
        build_material_parse_jobs_summary=lambda project_id: (
            [],
            {"backlog": 0, "failed_jobs": 0, "total_jobs": 1},
        ),
        to_float_or_none=lambda value: float(value) if value is not None else None,
        material_parse_worker=DummyWorker(),
        material_parse_backlog_warn=18,
        load_materials=lambda: [{"parse_status": "parsed", "structured_summary": {"ok": True}}],
        normalize_material_row_for_parse=lambda row: (row, False),
        build_data_hygiene_report=lambda **kwargs: {
            "orphan_records_total": 0,
            "datasets": [],
        },
        load_projects=lambda: [],
        load_submissions=lambda: [],
        build_scoring_readiness=lambda project_id, project: {},
        material_type_label=lambda material_type: str(material_type),
        now_iso=lambda: "2026-03-13T00:00:00+00:00",
    )

    payload = run_system_self_check(None, context=context)

    assert payload["ok"] is True
    assert payload["required_ok"] is True
    assert payload["checks"]["runtime_security"] is True
    assert payload["checks"]["event_log_appendability"] is True
    assert payload["checks"]["projection_consistency"] is True
    assert payload["checks"]["learning_artifact_versions"] is True
    assert payload["checks"]["agent_dependency_health"] is True
    assert payload["checks"]["scoring_replay_consistency"] is True
    assert payload["checks"]["structured_summary_schema_ok"] is True
    assert payload["summary"]["parser_capability_total"] == 4
    assert payload["summary"]["data_hygiene_orphan_records"] == 0
    assert payload["summary"]["openai_api_available"] is True


def test_run_system_self_check_ignores_non_gpt_failures_for_gpt_failure_metric(tmp_path):
    class DummyWorker:
        def is_alive(self) -> bool:
            return True

    probe_dir = tmp_path / "data"
    probe_dir.mkdir()
    context = SystemSelfCheckContext(
        required_item_names={
            "health",
            "config",
            "data_dirs_writable",
            "auth_status",
            "rate_limit_status",
            "parser_pdf",
            "parser_docx",
        },
        load_config=lambda: {"rubric": {}, "lexicon": {}},
        ensure_data_dirs=lambda: None,
        storage_probe_dir=str(probe_dir),
        build_storage_backend_status=lambda: {
            "primary_backend": "json",
            "event_log_enabled": False,
            "sqlite_mirror_enabled": False,
        },
        probe_config_completeness=lambda: {"ok": True, "detail": "dimension_count=16"},
        probe_storage_lock_status=lambda: {"ok": True, "detail": "lock_ok"},
        probe_event_log_appendability=lambda: {"ok": True, "detail": "event_log_disabled"},
        probe_projection_consistency=lambda: {"ok": True, "detail": "event_log_disabled"},
        probe_learning_artifact_versions=lambda: {"ok": True, "detail": "versions_ok"},
        probe_agent_dependency_health=lambda: {"ok": True, "detail": "agents=3"},
        probe_scoring_replay_consistency=lambda project_id: {
            "ok": True,
            "detail": "skipped:no_scored_submission_available",
        },
        get_auth_status=lambda: {"enabled": True},
        get_rate_limit_status=lambda: {"enabled": True},
        get_runtime_security_status=lambda: {
            "production_mode": False,
            "allowed_hosts_configured": False,
            "upload_limit_enabled": False,
            "api_docs_enabled": True,
            "require_api_keys": True,
        },
        is_secure_desktop_mode_enabled=lambda: False,
        get_openai_api_key=lambda: "sk-test",
        get_openai_model=lambda: "gpt-5.4",
        pdf_backend_name=lambda: "pymupdf",
        document_class=object(),
        pytesseract_module=None,
        image_module=None,
        resolve_dwg_converter_binaries=lambda: [],
        build_material_parse_jobs_summary=lambda project_id: (
            [],
            {
                "backlog": 0,
                "failed_jobs": 12,
                "total_jobs": 12,
                "gpt_jobs": 0,
                "gpt_failed_jobs": 0,
            },
        ),
        to_float_or_none=lambda value: float(value) if value is not None else None,
        material_parse_worker=DummyWorker(),
        material_parse_backlog_warn=18,
        load_materials=lambda: [],
        normalize_material_row_for_parse=lambda row: (row, False),
        build_data_hygiene_report=lambda **kwargs: {
            "orphan_records_total": 0,
            "datasets": [],
        },
        load_projects=lambda: [],
        load_submissions=lambda: [],
        build_scoring_readiness=lambda project_id, project: {},
        material_type_label=lambda material_type: str(material_type),
        now_iso=lambda: "2026-03-18T00:00:00+00:00",
    )

    payload = run_system_self_check(None, context=context)

    assert payload["checks"]["gpt_parse_failure_rate_ok"] is True
    metric = next(item for item in payload["items"] if item["name"] == "gpt_parse_failure_rate_ok")
    assert "gpt_jobs=0" in str(metric["detail"])
    assert "rate=0.0%" in str(metric["detail"])
