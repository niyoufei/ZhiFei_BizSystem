from pathlib import Path

SCRIPT_PATH = Path("/Users/youfeini/Desktop/ZhiFei_BizSystem/scripts/acceptance.sh")


def test_acceptance_script_wraps_strict_e2e_flow_with_pty_logging() -> None:
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "run_strict_e2e_flow() {" in source
    assert 'local raw_log="$ROOT_DIR/build/e2e_flow/acceptance_e2e_raw.log"' in source
    assert 'local clean_log="$ROOT_DIR/build/e2e_flow/acceptance_e2e.log"' in source
    assert (
        'KEEP_E2E_PROJECT="$keep_project" STRICT=1 API_KEY="$API_KEY" '
        'BASE_URL="http://127.0.0.1:${PORT}" \\'
    ) in source
    assert ('bash -x "$ROOT_DIR/scripts/e2e_api_flow.sh" >"$raw_log" 2>&1') in source


def test_acceptance_script_sanitizes_e2e_log_and_reuses_wrapper_in_step_two() -> None:
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert 'text = text.replace("\\x04", "").replace("\\x08", "")' in source
    assert 'echo "[acceptance] e2e log: $clean_log"' in source
    assert "run_strict_e2e_flow 0" in source
    assert "run_strict_e2e_flow 1" in source
    assert (
        'echo "  - /Users/youfeini/Desktop/ZhiFei_BizSystem/build/e2e_flow/acceptance_e2e.log"'
        in source
    )
