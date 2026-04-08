from pathlib import Path

SCRIPT_PATH = Path("/Users/youfeini/Desktop/ZhiFei_BizSystem/scripts/restart_server.sh")


def test_restart_server_script_tracks_lock_owner_and_stale_threshold() -> None:
    source = SCRIPT_PATH.read_text(encoding="utf-8")

    assert 'LOCK_OWNER_FILE="$LOCK_DIR/owner.pid"' in source
    assert 'LOCK_META_FILE="$LOCK_DIR/meta.env"' in source
    assert 'LOCK_STALE_SECONDS="${RESTART_LOCK_STALE_SECONDS:-180}"' in source
    assert "record_lock_owner() {" in source
    assert "lock_owner_pid() {" in source
    assert "lock_age_seconds() {" in source


def test_restart_server_script_reclaims_stale_restart_lock() -> None:
    source = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "lock_looks_stale() {" in source
    assert "clear_stale_lock() {" in source
    assert 'echo "Detected stale restart lock owned by inactive pid: $owner_pid"' in source
    assert (
        'echo "Detected stale legacy restart lock older than ${LOCK_STALE_SECONDS}s: $LOCK_DIR"'
        in source
    )
    assert "if lock_looks_stale; then" in source
    assert "clear_stale_lock" in source


def test_restart_server_script_is_sourceable_for_helper_checks() -> None:
    source = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "main() {" in source
    assert 'if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then' in source
    assert 'main "$@"' in source
    assert "trap cleanup_lock EXIT INT TERM" in source
