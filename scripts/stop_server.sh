#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

PORT="${PORT:-8000}"
PID_FILE="build/server.pid"
SCREEN_SESSION="zhifei_server_${PORT}"

stopped=0
if [[ -f "$PID_FILE" ]]; then
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "${pid}" ]] && kill -0 "$pid" 2>/dev/null; then
    echo "Stopping server pid: $pid"
    kill "$pid" 2>/dev/null || true
    sleep 1
    if kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid" 2>/dev/null || true
    fi
    stopped=1
  fi
  rm -f "$PID_FILE"
fi

pids="$(lsof -nP -iTCP:${PORT} -sTCP:LISTEN -t 2>/dev/null || true)"
if [[ -n "$pids" ]]; then
  echo "Stopping process(es) on :$PORT -> $pids"
  for p in $pids; do
    kill "$p" 2>/dev/null || true
  done
  sleep 1
  pids="$(lsof -nP -iTCP:${PORT} -sTCP:LISTEN -t 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    for p in $pids; do
      kill -9 "$p" 2>/dev/null || true
    done
  fi
  stopped=1
fi

if command -v screen >/dev/null 2>&1; then
  if screen -ls 2>/dev/null | grep -q "[.]${SCREEN_SESSION}[[:space:]]"; then
    echo "Stopping screen session: $SCREEN_SESSION"
    screen -S "$SCREEN_SESSION" -X quit >/dev/null 2>&1 || true
    stopped=1
  fi
fi

if [[ "$stopped" -eq 1 ]]; then
  echo "Server stopped."
else
  echo "No server process found on :$PORT."
fi
