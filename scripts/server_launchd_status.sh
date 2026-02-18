#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

LABEL="${LABEL:-com.zhifei.bizsystem.server}"
PORT="${PORT:-8000}"
MODE_FILE="$ROOT_DIR/build/daemon_mode.txt"
AUTO_HEAL="${AUTO_HEAL:-1}"

if ! command -v launchctl >/dev/null 2>&1; then
  echo "launchctl not found; this script is for macOS launchd."
  exit 1
fi

entry="$(launchctl list | grep -F "$LABEL" || true)"
if [[ -n "$entry" ]]; then
  echo "launchd entry: $entry"
else
  echo "launchd entry not found: $LABEL"
fi

if [[ -f "$MODE_FILE" ]]; then
  echo "daemon mode file: $MODE_FILE"
  cat "$MODE_FILE"
  mode="$(grep -E '^mode=' "$MODE_FILE" | head -n 1 | cut -d= -f2- || true)"
  reason="$(grep -E '^reason=' "$MODE_FILE" | head -n 1 | cut -d= -f2- || true)"
else
  echo "daemon mode file not found."
  mode=""
  reason=""
fi

PORT="$PORT" ./scripts/server_status.sh

health_ok=0
if curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
  health_ok=1
fi

if [[ "$mode" == "fallback" ]]; then
  echo "note: running in fallback mode (not true launchd keepalive)."
  if [[ "$health_ok" -ne 1 && "$AUTO_HEAL" == "1" ]]; then
    echo "auto-heal: service is down in fallback mode, restarting once..."
    if PORT="$PORT" ./scripts/restart_server.sh; then
      echo "auto-heal: restart completed."
      PORT="$PORT" ./scripts/server_status.sh
    else
      echo "auto-heal: restart failed."
    fi
  fi
  if [[ "$reason" == "launchd_workspace_permission_blocked" || "$reason" == "desktop_path_default_fallback" ]]; then
    echo "note: current workspace path may be blocked for launchd access (macOS privacy)."
    echo "note: in restricted environments, fallback background process may not stay alive after command exits."
  fi
fi
