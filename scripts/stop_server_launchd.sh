#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

LABEL="${LABEL:-com.zhifei.bizsystem.server}"
PORT="${PORT:-8000}"
MODE_FILE="$ROOT_DIR/build/daemon_mode.txt"

if ! command -v launchctl >/dev/null 2>&1; then
  echo "launchctl not found; this script is for macOS launchd."
  exit 1
fi

echo "Stopping launchd service: $LABEL"
launchctl remove "$LABEL" >/dev/null 2>&1 || true
PORT="$PORT" ./scripts/stop_server.sh >/dev/null 2>&1 || true
{
  echo "mode=stopped"
  echo "label=$LABEL"
  echo "port=$PORT"
  echo "updated_at=$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  echo "reason=user_stop"
} > "$MODE_FILE"
echo "launchd service stopped: $LABEL"
