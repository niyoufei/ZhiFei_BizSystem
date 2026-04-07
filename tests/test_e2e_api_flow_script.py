from pathlib import Path

SCRIPT_PATH = Path("/Users/youfeini/Desktop/ZhiFei_BizSystem/scripts/e2e_api_flow.sh")


def test_e2e_flow_script_supports_optional_compare_fetch() -> None:
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "fetch_optional_json() {" in source
    assert 'fetch_optional_json "$BUILD_DIR/compare.json" \\' in source
    assert 'fetch_optional_json "$BUILD_DIR/compare_report.json" \\' in source


def test_e2e_flow_script_falls_back_to_submissions_latest_for_compare_rankings() -> None:
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert 'submissions_latest = _load("submissions_latest.json")' in source
    assert "fallback_rankings = []" in source
    assert 'compare["rankings"] = fallback_rankings' in source
    assert 'raise SystemExit("compare linkage failed: rankings empty")' in source
