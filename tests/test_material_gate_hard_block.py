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
        "parse_status_by_type": {
            "tender_qa": {"parsed": 1},
            "boq": {"parsed": 1},
            "drawing": {"failed": 1},
        },
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


def _strong_material_knowledge() -> dict:
    return {
        "summary": {
            "cross_type_consensus_score": 0.28,
            "cross_type_consensus_type_count": 3,
            "structured_quality_avg": 0.41,
            "structured_quality_type_rate": 0.34,
        },
        "by_type": [
            {
                "material_type": "boq",
                "structured_quality_score": 0.4,
                "structured_quality_max": 0.48,
                "structured_signal_count": 9,
                "focused_dimensions": ["04", "11", "13"],
            },
            {
                "material_type": "tender_qa",
                "structured_quality_score": 0.39,
                "structured_quality_max": 0.46,
                "structured_signal_count": 7,
                "focused_dimensions": ["01", "09", "14"],
            },
            {
                "material_type": "drawing",
                "structured_quality_score": 0.37,
                "structured_quality_max": 0.45,
                "structured_signal_count": 6,
                "focused_dimensions": ["06", "12", "14"],
            },
        ],
    }


def test_material_gate_blocks_when_any_parse_failure_enabled():
    from app.main import _validate_material_gate_for_scoring

    snapshot = _base_snapshot()
    with patch("app.main._build_material_quality_snapshot", return_value=snapshot):
        with patch("app.main._build_material_knowledge_profile", return_value={}):
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
        with patch("app.main._build_material_knowledge_profile", return_value={}):
            with patch("app.main._resolve_material_gate_config", return_value=_base_cfg(False)):
                out, issues = _validate_material_gate_for_scoring(
                    "p1", {"meta": {}}, raise_on_fail=False
                )
    gate = out.get("gate") or {}
    assert gate.get("passed") is False
    assert any("图纸解析失败" in str(x) for x in issues)


def test_material_gate_adapts_numeric_threshold_when_evidence_is_strong():
    from app.main import _validate_material_gate_for_scoring

    snapshot = _base_snapshot()
    snapshot["parsed_ok_files"] = 3
    snapshot["parsed_failed_files"] = 0
    snapshot["parsed_ok_by_type"] = {"tender_qa": 1, "boq": 1, "drawing": 1}
    snapshot["parsed_fail_by_type"] = {}
    snapshot["parse_status_by_type"] = {
        "tender_qa": {"parsed": 1},
        "boq": {"parsed": 1},
        "drawing": {"parsed": 1},
    }
    snapshot["parse_fail_ratio"] = 0.0
    snapshot["parsed_fail_details"] = []
    snapshot["numeric_terms_by_type"] = {"tender_qa": 12, "boq": 6, "drawing": 6}
    cfg = _base_cfg(False)
    cfg["enforce_depth_gate"] = True
    cfg["min_numeric_terms_by_type"] = {"tender_qa": 6, "boq": 8, "drawing": 4}

    with patch("app.main._build_material_quality_snapshot", return_value=snapshot):
        with patch(
            "app.main._build_material_knowledge_profile",
            return_value=_strong_material_knowledge(),
        ):
            with patch("app.main._resolve_material_gate_config", return_value=cfg):
                out, issues = _validate_material_gate_for_scoring(
                    "p1", {"meta": {}}, raise_on_fail=False
                )
    depth_gate = out.get("depth_gate") or {}
    adjustments = depth_gate.get("adaptive_numeric_adjustments") or {}
    assert depth_gate.get("passed") is True
    assert issues == []
    assert adjustments["boq"]["base_min_numeric_terms"] == 8
    assert adjustments["boq"]["effective_min_numeric_terms"] == 6


def test_material_gate_keeps_numeric_threshold_when_evidence_is_weak():
    from app.main import _validate_material_gate_for_scoring

    snapshot = _base_snapshot()
    snapshot["parsed_ok_files"] = 3
    snapshot["parsed_failed_files"] = 0
    snapshot["parsed_ok_by_type"] = {"tender_qa": 1, "boq": 1, "drawing": 1}
    snapshot["parsed_fail_by_type"] = {}
    snapshot["parse_status_by_type"] = {
        "tender_qa": {"parsed": 1},
        "boq": {"parsed": 1},
        "drawing": {"parsed": 1},
    }
    snapshot["parse_fail_ratio"] = 0.0
    snapshot["parsed_fail_details"] = []
    snapshot["numeric_terms_by_type"] = {"tender_qa": 12, "boq": 6, "drawing": 6}
    cfg = _base_cfg(False)
    cfg["enforce_depth_gate"] = True
    cfg["min_numeric_terms_by_type"] = {"tender_qa": 6, "boq": 8, "drawing": 4}

    weak_knowledge = {
        "summary": {
            "cross_type_consensus_score": 0.05,
            "cross_type_consensus_type_count": 1,
            "structured_quality_avg": 0.18,
            "structured_quality_type_rate": 0.0,
        },
        "by_type": [
            {
                "material_type": "boq",
                "structured_quality_score": 0.18,
                "structured_quality_max": 0.22,
                "structured_signal_count": 2,
                "focused_dimensions": ["04"],
            }
        ],
    }

    with patch("app.main._build_material_quality_snapshot", return_value=snapshot):
        with patch("app.main._build_material_knowledge_profile", return_value=weak_knowledge):
            with patch("app.main._resolve_material_gate_config", return_value=cfg):
                out, issues = _validate_material_gate_for_scoring(
                    "p1", {"meta": {}}, raise_on_fail=False
                )
    depth_gate = out.get("depth_gate") or {}
    assert depth_gate.get("passed") is False
    assert any("清单数字约束提取不足" in str(x) for x in depth_gate.get("issues") or [])
