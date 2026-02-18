#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

PORT="${PORT:-8000}"
API_KEY="${API_KEY:-}"
STRICT="${STRICT:-0}"
HEALTH_URL="http://127.0.0.1:${PORT}/health"
SELF_CHECK_URL="http://127.0.0.1:${PORT}/api/v1/system/self_check"
SELF_CHECK_COMPAT_URL="http://127.0.0.1:${PORT}/api/system/self_check"
OPENAPI_URL="http://127.0.0.1:${PORT}/openapi.json"

curl_with_auth() {
  if [[ -n "$API_KEY" ]]; then
    curl -fsS -H "X-API-Key: $API_KEY" "$@"
  else
    curl -fsS "$@"
  fi
}

echo "[doctor] checking server status..."
./scripts/server_status.sh || true

if ! curl_with_auth "$HEALTH_URL" >/dev/null 2>&1; then
  echo "[doctor] server health failed, trying restart..."
  ./scripts/restart_server.sh
fi

echo "[doctor] running backend self-check..."
payload=""
self_check_degraded=0
if payload="$(curl_with_auth "$SELF_CHECK_URL" 2>/dev/null)"; then
  :
elif payload="$(curl_with_auth "$SELF_CHECK_COMPAT_URL" 2>/dev/null)"; then
  SELF_CHECK_URL="$SELF_CHECK_COMPAT_URL"
else
  echo "[doctor] self_check endpoint unavailable, fallback to health payload."
  if payload="$(curl_with_auth "$HEALTH_URL" 2>/dev/null)"; then
    echo "[doctor] WARN: self_check not found; health is OK."
    echo "[doctor] health payload:"
    echo "$payload"
    self_check_degraded=1
  fi
  if [[ "$self_check_degraded" != "1" ]]; then
    echo "[doctor] FAIL: health endpoint also unavailable."
    exit 1
  fi
fi

if [[ "$self_check_degraded" != "1" ]]; then
  ok="$(printf '%s' "$payload" | grep -o '"ok":[^,}]*' | head -n 1 | cut -d: -f2 | tr -d ' ' || true)"
  echo "[doctor] self_check endpoint: $SELF_CHECK_URL"
  echo "[doctor] self_check payload:"
  echo "$payload"

  if [[ "$ok" == "true" ]]; then
    :
  elif [[ -n "$ok" ]]; then
    echo "[doctor] FAIL: self_check returned ok=$ok"
    exit 1
  else
    echo "[doctor] WARN: self_check payload has no ok field."
  fi
fi

echo "[doctor] checking API surface from openapi..."
if openapi_payload="$(curl_with_auth "$OPENAPI_URL" 2>/dev/null)"; then
  required_paths=(
    "/api/v1/projects/{project_id}/ground_truth/from_files"
    "/api/v1/projects/{project_id}/expert-profile"
    "/api/v1/projects/{project_id}/rescore"
    "/api/v1/scoring/factors"
    "/api/v1/system/self_check"
  )
  missing=()
  for p in "${required_paths[@]}"; do
    if ! printf '%s' "$openapi_payload" | grep -Fq "\"$p\""; then
      missing+=("$p")
    fi
  done
  if [[ "${#missing[@]}" -gt 0 ]]; then
    echo "[doctor] WARN: missing endpoints in running server:"
    for p in "${missing[@]}"; do
      echo "  - $p"
    done
    if [[ "$STRICT" == "1" ]]; then
      echo "[doctor] strict mode enabled, treat missing endpoints as failure."
      exit 1
    fi
  else
    echo "[doctor] endpoint coverage: OK"
  fi
else
  echo "[doctor] WARN: openapi endpoint unavailable, skipped API surface check."
fi

echo "[doctor] PASS"
exit 0
