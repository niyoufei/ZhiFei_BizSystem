#!/bin/sh

set -u

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
APP_DIR="${APP_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
PORT="${PORT:-8000}"
LOG_FILE="$APP_DIR/build/server.log"
PY_BIN="${PY_BIN:-$APP_DIR/.venv/bin/python}"
if [ ! -x "$PY_BIN" ]; then
  PY_BIN="python3"
fi

cd "$APP_DIR" || exit 70

{
  echo "=== launchd start $(date '+%Y-%m-%d %H:%M:%S %z') ==="
  echo "PWD=$PWD"
  echo "PORT=$PORT"
  echo "PY_BIN=$PY_BIN"
  echo "PATH=$PATH"
} >> "$LOG_FILE"

PORT="$PORT" "$PY_BIN" -m app.main --no-browser >> "$LOG_FILE" 2>&1
RC=$?

echo "=== launchd exit rc=$RC $(date '+%Y-%m-%d %H:%M:%S %z') ===" >> "$LOG_FILE"
exit "$RC"
