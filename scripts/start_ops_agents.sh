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
STATUS_JSON="${STATUS_JSON:-$ROOT_DIR/build/ops_agents_status.json}"
STATUS_MD="${STATUS_MD:-$ROOT_DIR/build/ops_agents_status.md}"
LOG_KEEP="${LOG_KEEP:-12}"
SUPERVISOR_SLEEP_SECONDS="${SUPERVISOR_SLEEP_SECONDS:-$INTERVAL_SECONDS}"

mkdir -p "$ROOT_DIR/build"

if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
else
  PYTHON_BIN="python3"
fi

if [[ -z "$API_KEY" ]]; then
  API_KEY="$("$PYTHON_BIN" "$ROOT_DIR/scripts/resolve_api_key.py" --preferred-role ops --fallback-role admin 2>/dev/null || true)"
fi

kill_matching_ops_agents() {
  local pids
  pids="$(
    ps -axo pid=,command= | awk -v root="$ROOT_DIR" -v base="$BASE_URL" '
      index($0, "scripts/ops_agents.py") && (index($0, root) || index($0, "--base-url " base)) {print $1}
    ' | tr '\n' ' '
  )"
  if [[ -z "${pids// }" ]]; then
    return 0
  fi
  for pid in $pids; do
    [[ -z "${pid}" ]] && continue
    kill "$pid" >/dev/null 2>&1 || true
  done
  sleep 1
  for pid in $pids; do
    [[ -z "${pid}" ]] && continue
    if kill -0 "$pid" >/dev/null 2>&1; then
      kill -9 "$pid" >/dev/null 2>&1 || true
    fi
  done
}

if command -v screen >/dev/null 2>&1; then
  screen -S "$SCREEN_SESSION" -X quit >/dev/null 2>&1 || true
  screen -wipe >/dev/null 2>&1 || true
fi

if [[ -f "$PID_FILE" ]]; then
  old_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "${old_pid:-}" ]] && kill -0 "$old_pid" >/dev/null 2>&1; then
    kill "$old_pid" >/dev/null 2>&1 || true
    sleep 1
    if kill -0 "$old_pid" >/dev/null 2>&1; then
      kill -9 "$old_pid" >/dev/null 2>&1 || true
    fi
  fi
  rm -f "$PID_FILE"
fi

kill_matching_ops_agents

if ! "$PYTHON_BIN" "$ROOT_DIR/scripts/rotate_runtime_logs.py" --keep "$LOG_KEEP" "$LOG_FILE" "$STATUS_JSON" "$STATUS_MD" >/dev/null 2>&1; then
  echo "Warning: runtime log rotation failed; continuing with existing files." >&2
fi

launch_supervisor_loop() {
  cat <<'EOF'
cd "$ROOT_DIR" || exit 1
export OPS_AGENTS_LAUNCHER="supervisor"
while true; do
  "$PYTHON_BIN" scripts/ops_agents.py \
    --base-url "$BASE_URL" \
    --api-key "$API_KEY" \
    --auto-repair 1 \
    --auto-evolve 1 \
    --interval-seconds "$INTERVAL_SECONDS" \
    --max-cycles 1 \
    >>"$LOG_FILE" 2>&1
  rc=$?
  printf '[ops_agents_supervisor] child_exit=%s at=%s\n' "$rc" "$(date -u +%FT%TZ)" >>"$LOG_FILE"
  sleep "$SUPERVISOR_SLEEP_SECONDS"
done
EOF
}

launch_single_run() {
  cat <<'EOF'
cd "$ROOT_DIR" || exit 1
export OPS_AGENTS_LAUNCHER="direct"
"$PYTHON_BIN" scripts/ops_agents.py \
  --base-url "$BASE_URL" \
  --api-key "$API_KEY" \
  --auto-repair 1 \
  --auto-evolve 1 \
  --interval-seconds "$INTERVAL_SECONDS" \
  --max-cycles "$MAX_CYCLES" \
  >>"$LOG_FILE" 2>&1
EOF
}

if [[ "$MAX_CYCLES" == "0" ]]; then
  LAUNCH_SCRIPT="$(launch_supervisor_loop)"
else
  LAUNCH_SCRIPT="$(launch_single_run)"
fi

if command -v screen >/dev/null 2>&1; then
  echo "Starting ops-agents in detached screen session: $SCREEN_SESSION"
  screen -dmS "$SCREEN_SESSION" env \
    ROOT_DIR="$ROOT_DIR" \
    BASE_URL="$BASE_URL" \
    API_KEY="$API_KEY" \
    INTERVAL_SECONDS="$INTERVAL_SECONDS" \
    MAX_CYCLES="$MAX_CYCLES" \
    PYTHON_BIN="$PYTHON_BIN" \
    LOG_FILE="$LOG_FILE" \
    SUPERVISOR_SLEEP_SECONDS="$SUPERVISOR_SLEEP_SECONDS" \
    /bin/zsh -lc "$LAUNCH_SCRIPT"
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
  env \
    ROOT_DIR="$ROOT_DIR" \
    BASE_URL="$BASE_URL" \
    API_KEY="$API_KEY" \
    INTERVAL_SECONDS="$INTERVAL_SECONDS" \
    MAX_CYCLES="$MAX_CYCLES" \
    PYTHON_BIN="$PYTHON_BIN" \
    LOG_FILE="$LOG_FILE" \
    SUPERVISOR_SLEEP_SECONDS="$SUPERVISOR_SLEEP_SECONDS" \
    nohup /bin/zsh -lc "$LAUNCH_SCRIPT" >>"$LOG_FILE" 2>&1 &
  printf '%s\n' "$!" > "$PID_FILE"
fi

echo "Ops-agents started."
echo "Log: $LOG_FILE"
echo "PID file: $PID_FILE"
