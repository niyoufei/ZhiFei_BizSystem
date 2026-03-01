#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

LABEL="${LABEL:-com.zhifei.bizsystem.server}"
PORT="${PORT:-8000}"
RUNNER="$ROOT_DIR/scripts/run_server_launchd.sh"
LOG_FILE="$ROOT_DIR/build/server.log"
MODE_FILE="$ROOT_DIR/build/daemon_mode.txt"
FORCE_LAUNCHD="${FORCE_LAUNCHD:-0}"

if ! command -v launchctl >/dev/null 2>&1; then
  echo "launchctl not found; this script is for macOS launchd."
  exit 1
fi

mkdir -p "$ROOT_DIR/build"
if [[ -f "$LOG_FILE" ]]; then
  log_lines_before="$(wc -l < "$LOG_FILE" 2>/dev/null || echo 0)"
else
  log_lines_before=0
fi

if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PY_BIN="$ROOT_DIR/.venv/bin/python"
else
  PY_BIN="python3"
fi

if [[ ! -f "$RUNNER" ]]; then
  echo "runner not found: $RUNNER"
  exit 1
fi

echo "Starting launchd service: $LABEL (port=$PORT)"
launchctl remove "$LABEL" >/dev/null 2>&1 || true
PORT="$PORT" ./scripts/stop_server.sh >/dev/null 2>&1 || true

# Prefer true launchd daemon mode first. If launchd cannot access workspace
# (common with macOS privacy restrictions), fallback to regular background mode.
if [[ "$FORCE_LAUNCHD" != "1" ]]; then
  case "$ROOT_DIR" in
    */Desktop/*|*/Desktop)
      echo "desktop workspace detected; trying launchd first, will fallback automatically if blocked."
      ;;
  esac
fi

submit_err_file="$(mktemp "${TMPDIR:-/tmp}/zhifei_launchd_submit.XXXXXX")"
set +e
APP_DIR="$ROOT_DIR" PORT="$PORT" PY_BIN="$PY_BIN" launchctl submit -l "$LABEL" -- /bin/sh "$RUNNER" 2>"$submit_err_file"
submit_status=$?
set -e
if [[ "$submit_status" -ne 0 ]]; then
  echo "launchctl submit failed (status=$submit_status)."
  sed -n '1,40p' "$submit_err_file" || true
  rm -f "$submit_err_file"
  case "$ROOT_DIR" in
    */Desktop/*|*/Desktop)
      echo "fallback: using regular background restart (launchctl submit failed)."
      launchctl remove "$LABEL" >/dev/null 2>&1 || true
      PORT="$PORT" ./scripts/restart_server.sh
      {
        echo "mode=fallback"
        echo "label=$LABEL"
        echo "port=$PORT"
        echo "updated_at=$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
        echo "reason=launchctl_submit_failed"
      } > "$MODE_FILE"
      exit 0
      ;;
  esac
  exit 1
fi
rm -f "$submit_err_file"

for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
    echo "launchd service started: $LABEL"
    echo "URL: http://127.0.0.1:${PORT}/"
    echo "Log: $LOG_FILE"
    {
      echo "mode=launchd"
      echo "label=$LABEL"
      echo "port=$PORT"
      echo "updated_at=$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
      echo "reason="
    } > "$MODE_FILE"
    exit 0
  fi
  sleep 0.5
done

echo "launchd service failed to become ready: $LABEL"
entry="$(launchctl list | grep -F "$LABEL" || true)"
if [[ -n "$entry" ]]; then
  echo "$entry"
fi
launch_status="$(printf '%s' "$entry" | awk 'NR==1{print $2}')"
if [[ -f "$LOG_FILE" ]]; then
  start_line=$((log_lines_before + 1))
  sed -n "${start_line},\$p" "$LOG_FILE" | tail -n 80 || true
fi

# macOS privacy protection can block launchd from reading Desktop workspace paths.
# In that case, degrade to regular background restart so users still get a running service.
if [[ "$launch_status" == "126" || "$launch_status" == "1" ]]; then
  case "$ROOT_DIR" in
    */Desktop/*|*/Desktop)
      echo "launchd may not have permission to access workspace path: $ROOT_DIR"
      echo "fallback: using regular background restart (non-launchd daemon mode)."
      launchctl remove "$LABEL" >/dev/null 2>&1 || true
      PORT="$PORT" ./scripts/restart_server.sh
      {
        echo "mode=fallback"
        echo "label=$LABEL"
        echo "port=$PORT"
        echo "updated_at=$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
        echo "reason=launchd_workspace_permission_blocked"
      } > "$MODE_FILE"
      exit 0
      ;;
  esac
fi

exit 1
