from __future__ import annotations

from unittest.mock import patch


def _base_snapshot() -> dict:
    return {
        "project_id": "p1",
        "total_files": 3,
        "counts_by_type": {"tender_qa": 1, "boq": 1, "drawing": 1},
        "parsed_ok_files": 2,
        "parsed_failed_files": 1,
        "parsed_ok_by_type": {"tender_qa": 1, "boq": 1, "drawing": 0},
        "parsed_fail_by_type": {"drawing": 1},
        "chars_by_type": {"tender_qa": 20000, "boq": 3000, "drawing": 2000},
        "chunks_by_type": {"tender_qa": 20, "boq": 4, "drawing": 3},
        "numeric_terms_by_type": {"tender_qa": 12, "boq": 12, "drawing": 6},
        "lexical_terms_by_type": {"tender_qa": 120, "boq": 40, "drawing": 22},
        "total_parsed_chars": 25000,
        "total_parsed_chunks": 27,
        "total_numeric_terms": 30,
        "total_lexical_terms": 182,
        "parse_fail_ratio": 0.2,
        "parsed_fail_details": [
            {
                "filename": "drawing.pdf",
                "material_type": "drawing",
                "reason": "ValueError: PDF parser missing",
            }
        ],
    }


def _base_cfg(block_on_any_parse_failure: bool) -> dict:
    return {
        "enforce": True,
        "enforce_depth_gate": False,
        "required_types": ["tender_qa", "boq", "drawing"],
        "min_chars_by_type": {"tender_qa": 1000, "boq": 1000, "drawing": 1000},
        "min_chunks_by_type": {"tender_qa": 1, "boq": 1, "drawing": 1},
        "min_numeric_terms_by_type": {"tender_qa": 1, "boq": 1, "drawing": 1},
        "min_total_chunks": 1,
        "min_total_chars": 1000,
        "max_fail_ratio": 0.9,
        "block_on_any_parse_failure": block_on_any_parse_failure,
    }


def test_material_gate_blocks_when_any_parse_failure_enabled():
    from app.main import _validate_material_gate_for_scoring

    snapshot = _base_snapshot()
    with patch("app.main._build_material_quality_snapshot", return_value=snapshot):
        with patch("app.main._resolve_material_gate_config", return_value=_base_cfg(True)):
            out, issues = _validate_material_gate_for_scoring(
                "p1", {"meta": {}}, raise_on_fail=False
            )
    gate = out.get("gate") or {}
    assert gate.get("passed") is False
    assert any("硬闸门" in str(x) for x in issues)


def test_material_gate_allows_parse_failure_when_hard_block_disabled():
    from app.main import _validate_material_gate_for_scoring

    snapshot = _base_snapshot()
    with patch("app.main._build_material_quality_snapshot", return_value=snapshot):
        with patch("app.main._resolve_material_gate_config", return_value=_base_cfg(False)):
            out, issues = _validate_material_gate_for_scoring(
                "p1", {"meta": {}}, raise_on_fail=False
            )
    gate = out.get("gate") or {}
    assert gate.get("passed") is True
    assert issues == []
