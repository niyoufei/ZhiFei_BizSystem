#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

PORT="${PORT:-8000}"
STRICT="${STRICT:-0}"
AUTO_REPAIR_DATA_HYGIENE="${AUTO_REPAIR_DATA_HYGIENE:-1}"
API_KEY="${API_KEY:-}"
PID_FILE="build/server.pid"
LOG_FILE="build/server.log"
LOCK_DIR="build/.restart.lock"
LOCK_OWNER_FILE="$LOCK_DIR/owner.pid"
LOCK_META_FILE="$LOCK_DIR/meta.env"
LOCK_STALE_SECONDS="${RESTART_LOCK_STALE_SECONDS:-180}"
SCREEN_SESSION="zhifei_server_${PORT}"
LOG_KEEP="${LOG_KEEP:-12}"

if [[ -x ".venv/bin/python" ]]; then
  PYTHON_BIN=".venv/bin/python"
else
  PYTHON_BIN="python3"
fi

if [[ -z "$API_KEY" ]]; then
  API_KEY="$("$PYTHON_BIN" "$ROOT_DIR/scripts/resolve_api_key.py" --preferred-role ops --fallback-role admin 2>/dev/null || true)"
fi

mkdir -p build

preflight_python_runtime() {
  local tmp_log
  tmp_log="$(mktemp "${TMPDIR:-/tmp}/zhifei_pycheck.XXXXXX")"
  if "$PYTHON_BIN" - <<'PY' > /dev/null 2>"$tmp_log"; then
import fastapi  # noqa: F401
import pydantic  # noqa: F401
import pydantic_core  # noqa: F401
PY
    rm -f "$tmp_log"
    return 0
  fi

  if grep -qi "incompatible architecture" "$tmp_log"; then
    local shell_arch py_arch
    shell_arch="$(arch 2>/dev/null || uname -m)"
    py_arch="$("$PYTHON_BIN" -c 'import platform; print(platform.machine())' 2>/dev/null || echo unknown)"
    echo "Python 依赖架构不匹配，无法启动服务。"
    echo "当前终端架构: $shell_arch"
    echo "当前虚拟环境 Python 架构: $py_arch"
    echo "请在当前终端执行以下命令修复虚拟环境："
    echo "  rm -rf .venv"
    echo "  python3 -m venv .venv"
    echo "  .venv/bin/python -m pip install -r requirements.txt"
    echo "如你在 Rosetta 终端中运行，请先切换到原生终端再重建 .venv。"
  else
    echo "Python 运行时预检查失败（前80行）："
    sed -n '1,80p' "$tmp_log" || true
  fi
  rm -f "$tmp_log"
  return 1
}

cleanup_lock() {
  rm -f "$LOCK_OWNER_FILE" "$LOCK_META_FILE"
  rmdir "$LOCK_DIR" >/dev/null 2>&1 || rm -rf "$LOCK_DIR" >/dev/null 2>&1 || true
}

record_lock_owner() {
  printf '%s\n' "$$" >"$LOCK_OWNER_FILE"
  cat >"$LOCK_META_FILE" <<EOF
pid=$$
started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
host=$(hostname 2>/dev/null || echo unknown)
port=$PORT
EOF
}

lock_owner_pid() {
  if [[ -f "$LOCK_OWNER_FILE" ]]; then
    tr -cd '0-9' <"$LOCK_OWNER_FILE" || true
  fi
}

lock_age_seconds() {
  "$PYTHON_BIN" - "$LOCK_DIR" <<'PY'
import os
import sys
import time

path = sys.argv[1]
try:
    age = max(0, int(time.time() - os.stat(path).st_mtime))
except FileNotFoundError:
    age = 0
print(age)
PY
}

lock_looks_stale() {
  if [[ ! -e "$LOCK_DIR" ]]; then
    return 1
  fi

  local owner_pid age_seconds
  owner_pid="$(lock_owner_pid)"
  if [[ -n "$owner_pid" ]] && kill -0 "$owner_pid" 2>/dev/null; then
    return 1
  fi

  age_seconds="$(lock_age_seconds)"
  if [[ -n "$owner_pid" ]]; then
    echo "Detected stale restart lock owned by inactive pid: $owner_pid"
    return 0
  fi

  if [[ "${age_seconds:-0}" -ge "$LOCK_STALE_SECONDS" ]]; then
    echo "Detected stale legacy restart lock older than ${LOCK_STALE_SECONDS}s: $LOCK_DIR"
    return 0
  fi

  return 1
}

clear_stale_lock() {
  rm -rf "$LOCK_DIR" >/dev/null 2>&1 || true
}

acquire_lock() {
  local attempts=120
  local i
  for i in $(seq 1 "$attempts"); do
    if mkdir "$LOCK_DIR" 2>/dev/null; then
      record_lock_owner
      trap cleanup_lock EXIT INT TERM
      return 0
    fi
    if lock_looks_stale; then
      clear_stale_lock
      continue
    fi
    sleep 0.1
  done
  echo "Failed to acquire restart lock: $LOCK_DIR"
  return 1
}

stop_by_pid_file() {
  if [[ -f "$PID_FILE" ]]; then
    local old_pid
    old_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "${old_pid}" ]] && kill -0 "$old_pid" 2>/dev/null; then
      echo "Stopping existing server by pid: $old_pid"
      kill "$old_pid" 2>/dev/null || true
      sleep 1
      if kill -0 "$old_pid" 2>/dev/null; then
        kill -9 "$old_pid" 2>/dev/null || true
      fi
    fi
    rm -f "$PID_FILE"
  fi
}

stop_by_port() {
  local pids
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
  fi
}

start_server_process() {
  if command -v screen >/dev/null 2>&1; then
    # Prefer detached screen session for stronger keepalive in restricted shells.
    screen -S "$SCREEN_SESSION" -X quit >/dev/null 2>&1 || true
    screen -wipe >/dev/null 2>&1 || true
    if screen -dmS "$SCREEN_SESSION" env ROOT_DIR="$ROOT_DIR" PORT="$PORT" PYTHON_BIN="$PYTHON_BIN" LOG_FILE="$ROOT_DIR/$LOG_FILE" /bin/zsh -lc 'cd "$ROOT_DIR" && PORT="$PORT" "$PYTHON_BIN" -m app.main --no-browser >>"$LOG_FILE" 2>&1'; then
      START_MODE="screen"
      return 0
    fi
  fi

  nohup "$PYTHON_BIN" -m app.main --no-browser >"$LOG_FILE" 2>&1 &
  START_MODE="nohup"
  return 0
}

wait_until_ready() {
  local attempts="${RESTART_WAIT_ATTEMPTS:-120}"
  for _ in $(seq 1 "$attempts"); do
    if curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.5
  done
  return 1
}

listener_pid() {
  lsof -nP -iTCP:${PORT} -sTCP:LISTEN -t 2>/dev/null | head -n 1 || true
}

write_pid_from_listener() {
  local active_pid
  active_pid="$(listener_pid)"
  if [[ -n "$active_pid" ]]; then
    echo "$active_pid" >"$PID_FILE"
    return 0
  fi
  rm -f "$PID_FILE"
  return 1
}

confirm_ready_after_timeout() {
  local attempts="${RESTART_POST_TIMEOUT_GRACE_ATTEMPTS:-40}"
  for _ in $(seq 1 "$attempts"); do
    if [[ -n "$(listener_pid)" ]] && curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.5
  done
  return 1
}

curl_with_auth() {
  if [[ -n "$API_KEY" ]]; then
    curl -fsS -H "X-API-Key: $API_KEY" "$@"
  else
    curl -fsS "$@"
  fi
}

post_start_data_hygiene_repair() {
  if [[ "$AUTO_REPAIR_DATA_HYGIENE" != "1" ]]; then
    return 0
  fi
  local repair_url="http://127.0.0.1:${PORT}/api/v1/system/data_hygiene/repair"
  local audit_url="http://127.0.0.1:${PORT}/api/v1/system/data_hygiene"
  local audit_payload=""
  if curl_with_auth -X POST "$repair_url" >/dev/null 2>&1; then
    echo "Post-start hygiene repair: OK"
  else
    echo "Warning: post-start hygiene repair skipped or failed."
    return 0
  fi
  audit_payload="$(curl_with_auth "$audit_url" 2>/dev/null || true)"
  if [[ -n "$audit_payload" ]]; then
    local orphan_count
    orphan_count="$(printf '%s' "$audit_payload" | grep -o '"orphan_records_total":[[:space:]]*[0-9]*' | head -n 1 | tr -cd '0-9' || true)"
    echo "Post-start hygiene audit: orphan_records_total=${orphan_count:-unknown}"
  fi
  return 0
}

check_endpoint_coverage() {
  local openapi
  if ! openapi="$(curl -fsS "http://127.0.0.1:${PORT}/openapi.json" 2>/dev/null)"; then
    echo "Warning: unable to fetch openapi.json, skip endpoint coverage check."
    return 0
  fi
  local required_paths=(
    "/api/v1/projects/{project_id}/ground_truth/from_files"
    "/api/v1/projects/{project_id}/expert-profile"
    "/api/v1/projects/{project_id}/rescore"
    "/api/v1/scoring/factors"
    "/api/v1/system/self_check"
    "/api/v1/system/data_hygiene"
  )
  local missing=()
  local p
  for p in "${required_paths[@]}"; do
    if ! printf '%s' "$openapi" | grep -Fq "\"$p\""; then
      missing+=("$p")
    fi
  done
  if [[ "${#missing[@]}" -gt 0 ]]; then
    echo "Warning: running server is missing key V2 endpoints:"
    for p in "${missing[@]}"; do
      echo "  - $p"
    done
    if [[ "$STRICT" == "1" ]]; then
      echo "Strict mode enabled, treat missing endpoints as failure."
      return 1
    fi
  else
    echo "Endpoint coverage: OK"
  fi
  return 0
}

main() {
  local new_pid=""

  echo "Restarting server on http://127.0.0.1:${PORT}"
  acquire_lock
  stop_by_pid_file
  stop_by_port
  preflight_python_runtime

  START_MODE="unknown"
  if ! "$PYTHON_BIN" "$ROOT_DIR/scripts/rotate_runtime_logs.py" --keep "$LOG_KEEP" "$ROOT_DIR/$LOG_FILE" >/dev/null 2>&1; then
    echo "Warning: runtime log rotation failed; continuing with existing files."
  fi
  start_server_process

  if wait_until_ready; then
    new_pid="$(listener_pid)"
    write_pid_from_listener || true
    echo "Server started successfully. pid=$new_pid"
    echo "Start mode: $START_MODE"
    echo "URL: http://127.0.0.1:${PORT}/"
    echo "Log: $ROOT_DIR/$LOG_FILE"
    post_start_data_hygiene_repair
    check_endpoint_coverage
    return 0
  fi

  if confirm_ready_after_timeout; then
    new_pid="$(listener_pid)"
    write_pid_from_listener || true
    echo "Server started successfully after extended startup. pid=$new_pid"
    echo "Start mode: $START_MODE"
    echo "URL: http://127.0.0.1:${PORT}/"
    echo "Log: $ROOT_DIR/$LOG_FILE"
    post_start_data_hygiene_repair
    check_endpoint_coverage
    return 0
  fi

  echo "Server failed to become ready. Recent logs:"
  tail -n 80 "$LOG_FILE" || true
  return 1
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
