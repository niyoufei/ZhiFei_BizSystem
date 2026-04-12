from __future__ import annotations

from app.domain.material_parse_state import (
    default_material_parse_state,
    material_parse_has_terminal_payload,
    material_parse_ready_for_gate,
    material_parse_status_label,
    normalize_material_parse_job,
    normalize_material_row_for_parse,
)


def test_default_material_parse_state_uses_path_to_choose_initial_status() -> None:
    queued = default_material_parse_state(
        "drawing",
        path="/tmp/example.pdf",
        parse_version="v-test",
        now_iso="2026-04-12T00:00:00+00:00",
    )
    missing = default_material_parse_state(
        "drawing",
        path="",
        parse_version="v-test",
        now_iso="2026-04-12T00:00:00+00:00",
    )
    assert queued["parse_status"] == "queued"
    assert queued["parse_backend"] == "queued"
    assert missing["parse_status"] == "failed"
    assert missing["parse_error_class"] == "missing_path"


def test_normalize_material_row_for_parse_backfills_defaults_and_recovers_processing() -> None:
    normalized, changed = normalize_material_row_for_parse(
        {
            "filename": "现场照片.JPG",
            "material_type": "",
            "parse_status": "processing",
        },
        parse_version="v-test",
        now_iso="2026-04-12T00:00:00+00:00",
    )
    assert changed is True
    assert normalized["path"] == ""
    assert normalized["material_type"] == "site_photo"
    assert normalized["parse_status"] == "queued"
    assert normalized["parse_backend"] == "queued"
    assert normalized["parse_error_class"] == "worker_recovered"
    assert normalized["updated_at"] == "2026-04-12T00:00:00+00:00"


def test_normalize_material_row_for_parse_marks_full_parse_as_gate_ready() -> None:
    normalized, changed = normalize_material_row_for_parse(
        {
            "filename": "图纸.pdf",
            "material_type": "drawing",
            "path": "/tmp/a.pdf",
            "parse_status": "parsed",
        },
        parse_version="v-test",
        now_iso="2026-04-12T00:00:00+00:00",
    )
    assert changed is True
    assert normalized["parse_phase"] == "full"
    assert normalized["parse_ready_for_gate"] is True


def test_material_parse_gate_and_terminal_payload_helpers_are_stable() -> None:
    assert material_parse_ready_for_gate({"parse_status": "parsed", "parse_phase": "full"}) is True
    assert (
        material_parse_ready_for_gate({"parse_status": "parsed", "parse_phase": "preview"}) is False
    )
    assert material_parse_has_terminal_payload({"parsed_text": "已提取正文"}) is True
    assert material_parse_has_terminal_payload({"parsed_chars": 12}) is True
    assert material_parse_has_terminal_payload({"numeric_terms_norm": ["120天"]}) is True
    assert material_parse_has_terminal_payload({}) is False


def test_material_parse_status_label_formats_backend_and_error_detail() -> None:
    assert material_parse_status_label("previewed") == "预解析完成"
    assert (
        material_parse_status_label("parsed", parse_backend="gpt-5.4-mini") == "已解析（GPT-5.4）"
    )
    assert material_parse_status_label("parsed", parse_backend="local") == "已解析（local）"
    assert (
        material_parse_status_label("failed", parse_error_message="missing_path")
        == "解析失败：missing_path"
    )
    assert material_parse_status_label("queued") == "排队中"


def test_normalize_material_parse_job_backfills_defaults_and_normalizes_type() -> None:
    normalized, changed = normalize_material_parse_job(
        {
            "filename": "工程量清单.xlsx",
            "material_type": "",
        },
        now_iso="2026-04-12T00:00:00+00:00",
    )
    assert changed is True
    assert normalized["material_type"] == "boq"
    assert normalized["status"] == "queued"
    assert normalized["parse_mode"] == "full"
    assert normalized["created_at"] == "2026-04-12T00:00:00+00:00"
