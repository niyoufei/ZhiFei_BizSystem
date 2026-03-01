#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

INTERVAL_SECONDS="${INTERVAL_SECONDS:-60}"
MAX_CYCLES="${MAX_CYCLES:-0}"
API_KEY="${API_KEY:-}"
BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
LOG_FILE="${LOG_FILE:-$ROOT_DIR/build/ops_agents.log}"
PID_FILE="${PID_FILE:-$ROOT_DIR/build/ops_agents.pid}"
SCREEN_SESSION="${SCREEN_SESSION:-zhifei_ops_agents}"

mkdir -p "$ROOT_DIR/build"

if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
else
  PYTHON_BIN="python3"
fi

if command -v screen >/dev/null 2>&1; then
  screen -S "$SCREEN_SESSION" -X quit >/dev/null 2>&1 || true
  screen -wipe >/dev/null 2>&1 || true
  echo "Starting ops-agents in detached screen session: $SCREEN_SESSION"
  screen -dmS "$SCREEN_SESSION" env \
    ROOT_DIR="$ROOT_DIR" \
    BASE_URL="$BASE_URL" \
    API_KEY="$API_KEY" \
    INTERVAL_SECONDS="$INTERVAL_SECONDS" \
    MAX_CYCLES="$MAX_CYCLES" \
    PYTHON_BIN="$PYTHON_BIN" \
    LOG_FILE="$LOG_FILE" \
    /bin/zsh -lc 'cd "$ROOT_DIR" && "$PYTHON_BIN" scripts/ops_agents.py --base-url "$BASE_URL" --api-key "$API_KEY" --auto-repair 1 --auto-evolve 1 --interval-seconds "$INTERVAL_SECONDS" --max-cycles "$MAX_CYCLES" >>"$LOG_FILE" 2>&1'
  sleep 1
  screen_pid="$(
    (screen -ls 2>/dev/null || true) \
      | awk '/\.'"$SCREEN_SESSION"'[[:space:]]/{split($1,a,"."); print a[1]; found=1} END{if (!found) print ""}'
  )"
  if [[ -n "${screen_pid:-}" ]]; then
    printf '%s\n' "$screen_pid" > "$PID_FILE"
  fi
else
  echo "screen not found; fallback to nohup background mode."
  nohup "$PYTHON_BIN" scripts/ops_agents.py \
    --base-url "$BASE_URL" \
    --api-key "$API_KEY" \
    --auto-repair 1 \
    --auto-evolve 1 \
    --interval-seconds "$INTERVAL_SECONDS" \
    --max-cycles "$MAX_CYCLES" \
    >>"$LOG_FILE" 2>&1 &
  printf '%s\n' "$!" > "$PID_FILE"
fi

echo "Ops-agents started."
echo "Log: $LOG_FILE"
echo "PID file: $PID_FILE"
