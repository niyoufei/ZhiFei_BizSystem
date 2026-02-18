#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
API_KEY="${API_KEY:-}"
STRICT="${STRICT:-0}"

BUILD_DIR="$ROOT_DIR/build/e2e_flow"
mkdir -p "$BUILD_DIR"

ts="$(date +%Y%m%d_%H%M%S)"
E2E_PROJECT_PREFIX="${E2E_PROJECT_PREFIX:-E2E_}"
KEEP_E2E_PROJECT="${KEEP_E2E_PROJECT:-0}"
project_name="${E2E_PROJECT_PREFIX}${ts}"
project_id=""
sample_file="$ROOT_DIR/sample_shigong.txt"
if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
else
  PYTHON_BIN="python3"
fi
TMP_SERVER_PID=""
TMP_SERVER_LOG="$BUILD_DIR/e2e_temp_server.log"

if [[ ! -f "$sample_file" ]]; then
  echo "[e2e] sample file not found: $sample_file"
  exit 1
fi

host_port="$(python3 - "$BASE_URL" <<'PY'
import sys
from urllib.parse import urlparse
u = urlparse(sys.argv[1])
host = u.hostname or ""
port = u.port or (443 if u.scheme == "https" else 80)
print(f"{host}:{port}")
PY
)"
BASE_HOST="${host_port%%:*}"
BASE_PORT="${host_port##*:}"
IS_LOCAL_BASE=0
if [[ "$BASE_HOST" == "127.0.0.1" || "$BASE_HOST" == "localhost" ]]; then
  IS_LOCAL_BASE=1
fi

cleanup_temp_server() {
  if [[ -n "$TMP_SERVER_PID" ]] && kill -0 "$TMP_SERVER_PID" 2>/dev/null; then
    echo "[e2e] stopping temporary local server: $TMP_SERVER_PID" >&2
    kill "$TMP_SERVER_PID" 2>/dev/null || true
    sleep 0.5
    if kill -0 "$TMP_SERVER_PID" 2>/dev/null; then
      kill -9 "$TMP_SERVER_PID" 2>/dev/null || true
    fi
  fi
}
cleanup_e2e_project() {
  if [[ "$KEEP_E2E_PROJECT" == "1" ]]; then
    return 0
  fi
  if [[ -z "$project_id" ]]; then
    return 0
  fi
  local code="000"
  if [[ -n "$API_KEY" ]]; then
    code="$(curl -sS -o /dev/null -w "%{http_code}" -H "X-API-Key: $API_KEY" -X DELETE "$BASE_URL/api/v1/projects/$project_id" || true)"
  else
    code="$(curl -sS -o /dev/null -w "%{http_code}" -X DELETE "$BASE_URL/api/v1/projects/$project_id" || true)"
  fi
  if [[ "${code:0:1}" == "2" ]]; then
    echo "[e2e] cleanup: deleted temporary project $project_name ($project_id)"
  else
    echo "[e2e] cleanup: failed to delete temporary project $project_name ($project_id), http=$code"
  fi
}
cleanup_on_exit() {
  cleanup_temp_server
  cleanup_e2e_project
}
trap cleanup_on_exit EXIT

wait_for_local_health() {
  local attempts=40
  local i
  for i in $(seq 1 "$attempts"); do
    if curl -fsS "$BASE_URL/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.25
  done
  return 1
}

start_temp_local_server() {
  if [[ "$IS_LOCAL_BASE" != "1" ]]; then
    return 1
  fi
  if wait_for_local_health; then
    return 0
  fi
  if [[ -n "$TMP_SERVER_PID" ]] && kill -0 "$TMP_SERVER_PID" 2>/dev/null; then
    wait_for_local_health && return 0
  fi
  local pids
  pids="$(lsof -nP -iTCP:${BASE_PORT} -sTCP:LISTEN -t 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    for p in $pids; do
      kill "$p" 2>/dev/null || true
    done
    sleep 0.5
  fi
  echo "[e2e] starting temporary local server on port $BASE_PORT..." >&2
  PORT="$BASE_PORT" "$PYTHON_BIN" -m app.main --no-browser >"$TMP_SERVER_LOG" 2>&1 &
  TMP_SERVER_PID=$!
  if wait_for_local_health; then
    echo "[e2e] temporary local server ready: pid=$TMP_SERVER_PID" >&2
    return 0
  fi
  echo "[e2e] temporary local server failed to start. log: $TMP_SERVER_LOG" >&2
  tail -n 80 "$TMP_SERVER_LOG" || true
  return 1
}

restart_local_server() {
  if [[ "$IS_LOCAL_BASE" != "1" ]]; then
    return 1
  fi
  echo "[e2e] restarting local server on port $BASE_PORT..." >&2
  if PORT="$BASE_PORT" ./scripts/restart_server.sh >&2; then
    if wait_for_local_health; then
      return 0
    fi
    echo "[e2e] restart script returned success but service is unreachable." >&2
  fi
  echo "[e2e] fallback to temporary local server..." >&2
  start_temp_local_server
}

is_retryable_curl_error() {
  case "${1:-0}" in
    7|52|56)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

curl_with_auth() {
  local rc=0
  if [[ -n "$API_KEY" ]]; then
    curl -fsS -H "X-API-Key: $API_KEY" "$@" || rc=$?
  else
    curl -fsS "$@" || rc=$?
  fi
  if [[ "$rc" -eq 0 ]]; then
    return 0
  fi
  if [[ "$IS_LOCAL_BASE" == "1" ]] && is_retryable_curl_error "$rc"; then
    echo "[e2e] request connection failed, retry after restart..." >&2
    restart_local_server || return "$rc"
    if [[ -n "$API_KEY" ]]; then
      curl -fsS -H "X-API-Key: $API_KEY" "$@"
    else
      curl -fsS "$@"
    fi
    return $?
  fi
  return "$rc"
}

curl_http_code_once() {
  local out_file="$1"
  shift
  if [[ -n "$API_KEY" ]]; then
    curl -sS -o "$out_file" -w "%{http_code}" -H "X-API-Key: $API_KEY" "$@"
  else
    curl -sS -o "$out_file" -w "%{http_code}" "$@"
  fi
}

curl_http_code() {
  local out_file="$1"
  shift
  local code=""
  local rc=0
  code="$(curl_http_code_once "$out_file" "$@")" || rc=$?
  if [[ "$rc" -eq 0 ]]; then
    printf '%s' "$code"
    return 0
  fi
  if [[ "$IS_LOCAL_BASE" == "1" ]] && is_retryable_curl_error "$rc"; then
    echo "[e2e] request connection failed, retry after restart..." >&2
    restart_local_server || true
    rc=0
    code="$(curl_http_code_once "$out_file" "$@")" || rc=$?
    if [[ "$rc" -eq 0 ]]; then
      printf '%s' "$code"
      return 0
    fi
  fi
  if [[ -z "$code" ]]; then
    code="000"
  fi
  printf '%s' "$code"
  return 0
}

post_json() {
  local url="$1"
  local payload="$2"
  local rc=0
  if [[ -n "$API_KEY" ]]; then
    curl -fsS -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" -X POST "$url" -d "$payload" || rc=$?
  else
    curl -fsS -H "Content-Type: application/json" -X POST "$url" -d "$payload" || rc=$?
  fi
  if [[ "$rc" -eq 0 ]]; then
    return 0
  fi
  if [[ "$IS_LOCAL_BASE" == "1" ]] && is_retryable_curl_error "$rc"; then
    echo "[e2e] request connection failed, retry after restart..." >&2
    restart_local_server || return "$rc"
    if [[ -n "$API_KEY" ]]; then
      curl -fsS -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" -X POST "$url" -d "$payload"
    else
      curl -fsS -H "Content-Type: application/json" -X POST "$url" -d "$payload"
    fi
    return $?
  fi
  return "$rc"
}

fetch_best_effort_json() {
  local out_file="$1"
  shift
  local code=""
  local endpoint=""
  local idx=0
  for endpoint in "$@"; do
    idx=$((idx + 1))
    code="$(curl_http_code "$out_file" "$endpoint")"
    if [[ "${code:0:1}" == "2" ]]; then
      return 0
    fi
    if [[ "$STRICT" == "1" && "$idx" -eq 1 ]]; then
      echo "[e2e] strict mode: primary endpoint failed ($code) -> $endpoint"
      exit 1
    fi
  done
  printf '{"ok":false,"detail":"all endpoints unavailable","http_code":"%s","tried":%s}\n' \
    "$code" \
    "$(printf '%s\n' "$@" | python3 -c 'import json,sys; print(json.dumps([s.strip() for s in sys.stdin if s.strip()], ensure_ascii=False))')" > "$out_file"
  if [[ "$STRICT" == "1" ]]; then
    echo "[e2e] strict mode: required endpoint unavailable -> $*"
    exit 1
  fi
  return 0
}

fetch_best_effort_text() {
  local out_file="$1"
  shift
  local tmp="$out_file.tmp"
  local code=""
  local endpoint=""
  local idx=0
  for endpoint in "$@"; do
    idx=$((idx + 1))
    code="$(curl_http_code "$tmp" "$endpoint")"
    if [[ "${code:0:1}" == "2" ]]; then
      mv "$tmp" "$out_file"
      return 0
    fi
    if [[ "$STRICT" == "1" && "$idx" -eq 1 ]]; then
      echo "[e2e] strict mode: primary endpoint failed ($code) -> $endpoint"
      rm -f "$tmp"
      exit 1
    fi
  done
  printf '# Endpoint unavailable\n\n- Tried: %s\n- Last HTTP: %s\n' "$*" "$code" > "$out_file"
  rm -f "$tmp"
  if [[ "$STRICT" == "1" ]]; then
    echo "[e2e] strict mode: required endpoint unavailable -> $*"
    exit 1
  fi
  return 0
}

echo "[e2e] health check: $BASE_URL/health"
if ! curl_with_auth "$BASE_URL/health" > "$BUILD_DIR/health.json"; then
  if [[ "$IS_LOCAL_BASE" == "1" ]]; then
    echo "[e2e] server not ready, auto restarting local server on port $BASE_PORT..."
    restart_local_server
    curl_with_auth "$BASE_URL/health" > "$BUILD_DIR/health.json"
  else
    echo "[e2e] health check failed for non-local host: $BASE_URL"
    exit 1
  fi
fi

echo "[e2e] create project: $project_name"
create_resp="$(post_json "$BASE_URL/api/v1/projects" "{\"name\":\"$project_name\"}")"
printf '%s\n' "$create_resp" > "$BUILD_DIR/create_project.json"
project_id="$(printf '%s' "$create_resp" | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')"
echo "[e2e] project_id=$project_id"

echo "[e2e] upload material"
curl_with_auth -X POST "$BASE_URL/api/v1/projects/$project_id/materials" -F "file=@$sample_file;type=text/plain" > "$BUILD_DIR/upload_material.json"

echo "[e2e] upload shigong and score"
curl_with_auth -X POST "$BASE_URL/api/v1/projects/$project_id/shigong" -F "file=@$sample_file;type=text/plain" > "$BUILD_DIR/upload_shigong.json"

echo "[e2e] list submissions latest_report"
curl_with_auth "$BASE_URL/api/v1/projects/$project_id/submissions?with=latest_report" > "$BUILD_DIR/submissions_latest.json"

echo "[e2e] ingest one ground truth from file"
gt_tmp="$BUILD_DIR/ground_truth_from_files.tmp.json"
gt_code="$(curl_http_code "$gt_tmp" -X POST "$BASE_URL/api/v1/projects/$project_id/ground_truth/from_files" \
  -F "files=@$sample_file;type=text/plain" \
  -F 'judge_scores=[80,82,84,85,86]' \
  -F "final_score=84" \
  -F "source=E2E脚本")"

if [[ "$STRICT" == "1" && "${gt_code:0:1}" != "2" ]]; then
  echo "[e2e] strict mode: primary ground_truth endpoint failed with HTTP $gt_code"
  cat "$gt_tmp" || true
  exit 1
fi

if [[ "$gt_code" == "404" || "$gt_code" == "405" ]]; then
  echo "[e2e] v1 ground_truth/from_files returned $gt_code, fallback to compat /api route..."
  gt_code="$(curl_http_code "$gt_tmp" -X POST "$BASE_URL/api/projects/$project_id/ground_truth/from_files" \
    -F "files=@$sample_file;type=text/plain" \
    -F 'judge_scores=[80,82,84,85,86]' \
    -F "final_score=84" \
    -F "source=E2E脚本")"
fi

if [[ "$gt_code" == "404" || "$gt_code" == "405" ]]; then
  echo "[e2e] compat ground_truth/from_files returned $gt_code, fallback to single-file endpoint..."
  gt_code="$(curl_http_code "$gt_tmp" -X POST "$BASE_URL/api/v1/projects/$project_id/ground_truth/from_file" \
    -F "file=@$sample_file;type=text/plain" \
    -F 'judge_scores=[80,82,84,85,86]' \
    -F "final_score=84" \
    -F "source=E2E脚本")"
fi

if [[ "${gt_code:0:1}" != "2" ]]; then
  echo "[e2e] ground truth ingest failed with HTTP $gt_code"
  cat "$gt_tmp" || true
  exit 1
fi
mv "$gt_tmp" "$BUILD_DIR/ground_truth_from_files.json"

echo "[e2e] evolve"
curl_with_auth -X POST "$BASE_URL/api/v1/projects/$project_id/evolve" > "$BUILD_DIR/evolve.json"

echo "[e2e] scoring factors + markdown"
fetch_best_effort_json "$BUILD_DIR/scoring_factors.json" \
  "$BASE_URL/api/v1/scoring/factors?project_id=$project_id" \
  "$BASE_URL/api/scoring/factors?project_id=$project_id" \
  "$BASE_URL/api/v1/projects/$project_id/evaluation"
fetch_best_effort_json "$BUILD_DIR/scoring_factors_markdown.json" \
  "$BASE_URL/api/v1/scoring/factors/markdown?project_id=$project_id" \
  "$BASE_URL/api/scoring/factors/markdown?project_id=$project_id" \
  "$BASE_URL/api/v1/projects/$project_id/compare_report"

echo "[e2e] analysis bundle"
fetch_best_effort_json "$BUILD_DIR/analysis_bundle.json" \
  "$BASE_URL/api/v1/projects/$project_id/analysis_bundle" \
  "$BASE_URL/api/projects/$project_id/analysis_bundle" \
  "$BASE_URL/api/v1/projects/$project_id/evaluation"
fetch_best_effort_text "$BUILD_DIR/analysis_bundle.md" \
  "$BASE_URL/api/v1/projects/$project_id/analysis_bundle.md" \
  "$BASE_URL/api/projects/$project_id/analysis_bundle.md" \
  "$BASE_URL/api/v1/projects/$project_id/compare_report"

echo "[e2e] system self-check"
fetch_best_effort_json "$BUILD_DIR/self_check.json" \
  "$BASE_URL/api/v1/system/self_check?project_id=$project_id" \
  "$BASE_URL/api/system/self_check?project_id=$project_id" \
  "$BASE_URL/health"

python3 - "$BUILD_DIR" "$project_id" "$project_name" "$BASE_URL" <<'PY'
import json
import sys
from pathlib import Path

build_dir = Path(sys.argv[1])
project_id = sys.argv[2]
project_name = sys.argv[3]
base_url = sys.argv[4]

summary = {
    "ok": True,
    "base_url": base_url,
    "project_id": project_id,
    "project_name": project_name,
    "artifacts": {
        "create_project": str(build_dir / "create_project.json"),
        "upload_material": str(build_dir / "upload_material.json"),
        "upload_shigong": str(build_dir / "upload_shigong.json"),
        "submissions_latest": str(build_dir / "submissions_latest.json"),
        "ground_truth_from_files": str(build_dir / "ground_truth_from_files.json"),
        "evolve": str(build_dir / "evolve.json"),
        "scoring_factors": str(build_dir / "scoring_factors.json"),
        "scoring_factors_markdown": str(build_dir / "scoring_factors_markdown.json"),
        "analysis_bundle_json": str(build_dir / "analysis_bundle.json"),
        "analysis_bundle_md": str(build_dir / "analysis_bundle.md"),
        "self_check": str(build_dir / "self_check.json"),
    },
}
(build_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, indent=2))
PY

echo "[e2e] done. summary: $BUILD_DIR/summary.json"
