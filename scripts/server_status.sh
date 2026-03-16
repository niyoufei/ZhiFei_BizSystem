#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

PORT="${PORT:-8000}"
PID_FILE="build/server.pid"
API_KEY="${API_KEY:-}"
HEALTH_URL="http://127.0.0.1:${PORT}/health"
SCREEN_SESSION="zhifei_server_${PORT}"

if [[ -z "$API_KEY" ]]; then
  if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
  else
    PYTHON_BIN="python3"
  fi
  API_KEY="$("$PYTHON_BIN" "$ROOT_DIR/scripts/resolve_api_key.py" --preferred-role ops --fallback-role admin 2>/dev/null || true)"
fi

curl_with_auth() {
  if [[ -n "$API_KEY" ]]; then
    curl -fsS -H "X-API-Key: $API_KEY" "$@"
  else
    curl -fsS "$@"
  fi
}

pids="$(lsof -nP -iTCP:${PORT} -sTCP:LISTEN -t 2>/dev/null || true)"
if [[ -n "$pids" ]]; then
  echo "LISTEN on :$PORT -> $pids"
else
  echo "No LISTEN process on :$PORT"
fi

pid_file_pid=""
pid_in_listeners=0
if [[ -f "$PID_FILE" ]]; then
  pid_file_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "${pid_file_pid}" ]] && [[ -n "$pids" ]] && printf '%s\n' "$pids" | grep -Fxq "$pid_file_pid"; then
    pid_in_listeners=1
    echo "PID file matches active listener: $pid_file_pid"
  elif [[ -n "${pid_file_pid}" ]] && kill -0 "$pid_file_pid" 2>/dev/null; then
    echo "PID file process is running: $pid_file_pid"
  else
    echo "PID file exists but process is not running: ${pid_file_pid:-unknown}"
  fi
else
  echo "PID file not found."
fi

if curl_with_auth "$HEALTH_URL" >/dev/null 2>&1; then
  echo "Health check: OK"
  health_payload="$(curl_with_auth "$HEALTH_URL" 2>/dev/null || true)"
  if [[ -n "$health_payload" ]]; then
    version="$(printf '%s' "$health_payload" | grep -o '"version":"[^"]*' | head -n 1 | cut -d'"' -f4 || true)"
    status="$(printf '%s' "$health_payload" | grep -o '"status":"[^"]*' | head -n 1 | cut -d'"' -f4 || true)"
    if [[ -n "$version" || -n "$status" ]]; then
      echo "Health payload: status=${status:-unknown}, version=${version:-unknown}"
    fi
  fi
else
  echo "Health check: FAIL"
fi

if [[ -f "$PID_FILE" ]] && [[ -n "${pid_file_pid}" ]] && [[ "$pid_in_listeners" -eq 0 ]] && ! kill -0 "$pid_file_pid" 2>/dev/null; then
  if [[ -z "$pids" ]]; then
    rm -f "$PID_FILE"
    echo "Auto-fix: removed stale PID file ($pid_file_pid) because no listener exists."
  else
    pid_count="$(printf '%s\n' "$pids" | awk 'NF{c++} END{print c+0}')"
    if [[ "$pid_count" -eq 1 ]]; then
      listener_pid="$(printf '%s\n' "$pids" | head -n 1)"
      printf '%s\n' "$listener_pid" > "$PID_FILE"
      echo "Auto-fix: PID file updated from dead $pid_file_pid to active listener $listener_pid."
    else
      echo "Warning: PID file points to dead process ($pid_file_pid), multiple listeners found: $pids"
    fi
  fi
fi

if command -v screen >/dev/null 2>&1; then
  if screen -ls 2>/dev/null | grep -q "[.]${SCREEN_SESSION}[[:space:]]"; then
    echo "Screen session: $SCREEN_SESSION (detached)"
  fi
fi
