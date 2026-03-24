#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

PID_FILE="${PID_FILE:-$ROOT_DIR/build/ops_agents.pid}"
SCREEN_SESSION="${SCREEN_SESSION:-zhifei_ops_agents}"
BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"

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
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "${pid}" ]] && kill -0 "$pid" >/dev/null 2>&1; then
    kill "$pid" >/dev/null 2>&1 || true
    sleep 1
    if kill -0 "$pid" >/dev/null 2>&1; then
      kill -9 "$pid" >/dev/null 2>&1 || true
    fi
  fi
  rm -f "$PID_FILE"
fi

kill_matching_ops_agents

echo "Ops-agents stopped."
