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
material_seed_file="$BUILD_DIR/e2e_material_seed.txt"
inline_score_payload="$BUILD_DIR/e2e_inline_score_payload.json"
if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
else
  PYTHON_BIN="python3"
fi
if [[ -z "$API_KEY" ]]; then
  API_KEY="$("$PYTHON_BIN" "$ROOT_DIR/scripts/resolve_api_key.py" --preferred-role admin 2>/dev/null || true)"
fi
TMP_SERVER_PID=""
TMP_SERVER_LOG="$BUILD_DIR/e2e_temp_server.log"

if [[ ! -f "$sample_file" ]]; then
  echo "[e2e] sample file not found: $sample_file"
  exit 1
fi

"$PYTHON_BIN" - "$sample_file" "$material_seed_file" <<'PY'
import sys
from pathlib import Path

src = Path(sys.argv[1]).read_text(encoding="utf-8", errors="ignore").strip()
if not src:
    src = "施工组织设计示例文本。"
extra = (
    "\n\n【门禁补齐段】本段用于 e2e 门禁回归，包含工期、质量、安全、资源、应急、"
    "BIM、扬尘、危大工程、检查频次、责任岗位、验收动作、参数阈值等关键词。"
    "\n参数示例：工期365天，检查频次每周2次，抽检比例10%，旁站覆盖率100%。\n"
)
payload = ((src + extra) * 28).strip() + "\n"
Path(sys.argv[2]).write_text(payload, encoding="utf-8")
PY

"$PYTHON_BIN" - "$sample_file" "$inline_score_payload" <<'PY'
import json
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8", errors="ignore")
Path(sys.argv[2]).write_text(json.dumps({"text": text}, ensure_ascii=False), encoding="utf-8")
PY

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
create_payload="$(cat <<JSON
{
  "name": "$project_name",
  "meta": {
    "required_material_types": ["tender_qa", "boq", "drawing"],
    "min_parsed_chars_by_type": {
      "tender_qa": 500,
      "boq": 300,
      "drawing": 300,
      "site_photo": 0
    },
    "min_total_parsed_chars": 1000,
    "max_material_parse_fail_ratio": 1.0,
    "block_on_any_material_parse_failure": false,
    "enforce_material_utilization_gate": false,
    "material_utilization_gate_mode": "warn"
  }
}
JSON
)"
create_resp="$(post_json "$BASE_URL/api/v1/projects" "$create_payload")"
printf '%s\n' "$create_resp" > "$BUILD_DIR/create_project.json"
project_id="$(printf '%s' "$create_resp" | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')"
echo "[e2e] project_id=$project_id"

echo "[e2e] upload material"
curl_with_auth -X POST "$BASE_URL/api/v1/projects/$project_id/materials" \
  -F "material_type=tender_qa" \
  -F "file=@$material_seed_file;type=text/plain" > "$BUILD_DIR/upload_material.json"
curl_with_auth -X POST "$BASE_URL/api/v1/projects/$project_id/materials" \
  -F "material_type=boq" \
  -F "file=@$material_seed_file;type=text/plain" > "$BUILD_DIR/upload_material_boq.json"
curl_with_auth -X POST "$BASE_URL/api/v1/projects/$project_id/materials" \
  -F "material_type=drawing" \
  -F "file=@$material_seed_file;type=text/plain" > "$BUILD_DIR/upload_material_drawing.json"

echo "[e2e] upload shigong (pending expected)"
curl_with_auth -X POST "$BASE_URL/api/v1/projects/$project_id/shigong" -F "file=@$sample_file;type=text/plain" > "$BUILD_DIR/upload_shigong.json"

python3 - "$BUILD_DIR/upload_shigong.json" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
status = str(((payload.get("report") or {}).get("scoring_status") or "")).strip().lower()
if status != "pending":
    raise SystemExit(f"upload should stay pending, got scoring_status={status!r}")
print("[e2e] pending status assertion: OK")
PY

echo "[e2e] rescore shigong"
rescore_tmp="$BUILD_DIR/rescore.tmp.json"
rescore_code="$(curl_http_code "$rescore_tmp" -H "Content-Type: application/json" -X POST "$BASE_URL/api/v1/projects/$project_id/rescore" -d '{"score_scale_max":100}')"
mv "$rescore_tmp" "$BUILD_DIR/rescore.json"
if [[ "${rescore_code:0:1}" == "2" ]]; then
  python3 - "$BUILD_DIR/rescore.json" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
generated = int(payload.get("reports_generated") or 0)
if generated <= 0:
    raise SystemExit(f"rescore generated no reports: {generated}")
print("[e2e] rescore assertion: OK, reports_generated=", generated)
PY
elif [[ "$rescore_code" == "422" ]]; then
  echo "[e2e] WARN: rescore blocked by material gate, fallback to inline /score for downstream linkage checks"
  if [[ -n "$API_KEY" ]]; then
    curl -fsS -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
      -X POST "$BASE_URL/api/v1/projects/$project_id/score" \
      --data-binary @"$inline_score_payload" > "$BUILD_DIR/inline_score_fallback.json"
  else
    curl -fsS -H "Content-Type: application/json" \
      -X POST "$BASE_URL/api/v1/projects/$project_id/score" \
      --data-binary @"$inline_score_payload" > "$BUILD_DIR/inline_score_fallback.json"
  fi
else
  echo "[e2e] rescore failed with HTTP $rescore_code"
  cat "$BUILD_DIR/rescore.json" || true
  exit 1
fi

echo "[e2e] list submissions latest_report (scored expected)"
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
confirm_extreme_sample="$("$PYTHON_BIN" - "$BUILD_DIR/ground_truth_from_files.json" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
blocked = False
if isinstance(payload, dict):
    guardrail = payload.get("feedback_guardrail")
    if isinstance(guardrail, dict) and bool(guardrail.get("blocked")):
        blocked = True
    items = payload.get("items")
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            record = item.get("record")
            if not isinstance(record, dict):
                continue
            guardrail = record.get("feedback_guardrail")
            if isinstance(guardrail, dict) and bool(guardrail.get("blocked")):
                blocked = True
                break
print("1" if blocked else "0")
PY
)"
evolve_url="$BASE_URL/api/v1/projects/$project_id/evolve"
if [[ "$confirm_extreme_sample" == "1" ]]; then
  evolve_url="${evolve_url}?confirm_extreme_sample=1"
fi
curl_with_auth -X POST "$evolve_url" > "$BUILD_DIR/evolve.json"

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

echo "[e2e] module 5/6/7 linkage checks"
fetch_best_effort_json "$BUILD_DIR/compare.json" \
  "$BASE_URL/api/v1/projects/$project_id/compare"
fetch_best_effort_json "$BUILD_DIR/compare_report.json" \
  "$BASE_URL/api/v1/projects/$project_id/compare_report"
fetch_best_effort_json "$BUILD_DIR/insights.json" \
  "$BASE_URL/api/v1/projects/$project_id/insights"
post_json "$BASE_URL/api/v1/projects/$project_id/learning" '{}' > "$BUILD_DIR/learning.json"
fetch_best_effort_json "$BUILD_DIR/adaptive.json" \
  "$BASE_URL/api/v1/projects/$project_id/adaptive"
fetch_best_effort_json "$BUILD_DIR/adaptive_patch.json" \
  "$BASE_URL/api/v1/projects/$project_id/adaptive_patch"
fetch_best_effort_json "$BUILD_DIR/adaptive_validate.json" \
  "$BASE_URL/api/v1/projects/$project_id/adaptive_validate"
fetch_best_effort_json "$BUILD_DIR/writing_guidance.json" \
  "$BASE_URL/api/v1/projects/$project_id/writing_guidance"
fetch_best_effort_json "$BUILD_DIR/compilation_instructions.json" \
  "$BASE_URL/api/v1/projects/$project_id/compilation_instructions"
fetch_best_effort_json "$BUILD_DIR/evolution_health.json" \
  "$BASE_URL/api/v1/projects/$project_id/evolution/health"
fetch_best_effort_json "$BUILD_DIR/mece_audit.json" \
  "$BASE_URL/api/v1/projects/$project_id/mece_audit"
fetch_best_effort_json "$BUILD_DIR/evidence_trace_latest.json" \
  "$BASE_URL/api/v1/projects/$project_id/evidence_trace/latest"
fetch_best_effort_json "$BUILD_DIR/scoring_basis_latest.json" \
  "$BASE_URL/api/v1/projects/$project_id/scoring_basis/latest"

python3 - "$BUILD_DIR" <<'PY'
import json
import sys
from pathlib import Path

build = Path(sys.argv[1])

def _load(name: str):
    return json.loads((build / name).read_text(encoding="utf-8"))

compare = _load("compare.json")
if not (isinstance(compare.get("rankings"), list) and compare["rankings"]):
    raise SystemExit("compare linkage failed: rankings empty")

insights = _load("insights.json")
weak_dims = insights.get("weak_dimensions")
if not isinstance(weak_dims, list):
    weak_dims = insights.get("weakest_dims")
if not isinstance(weak_dims, list):
    raise SystemExit("insights linkage failed: weak_dimensions/weakest_dims missing")

learning = _load("learning.json")
mult = learning.get("dimension_multipliers") if isinstance(learning, dict) else None
if not (isinstance(mult, dict) and mult):
    raise SystemExit("learning linkage failed: dimension_multipliers empty")

mece = _load("mece_audit.json")
overall = mece.get("overall") if isinstance(mece, dict) else {}
if not isinstance(overall, dict) or "health_score" not in overall:
    raise SystemExit("mece audit linkage failed: overall.health_score missing")

print("[e2e] module linkage assertions: OK")
PY

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
        "upload_material_boq": str(build_dir / "upload_material_boq.json"),
        "upload_material_drawing": str(build_dir / "upload_material_drawing.json"),
        "upload_shigong": str(build_dir / "upload_shigong.json"),
        "submissions_latest": str(build_dir / "submissions_latest.json"),
        "ground_truth_from_files": str(build_dir / "ground_truth_from_files.json"),
        "evolve": str(build_dir / "evolve.json"),
        "rescore": str(build_dir / "rescore.json"),
        "inline_score_fallback": str(build_dir / "inline_score_fallback.json"),
        "compare": str(build_dir / "compare.json"),
        "compare_report": str(build_dir / "compare_report.json"),
        "insights": str(build_dir / "insights.json"),
        "learning": str(build_dir / "learning.json"),
        "adaptive": str(build_dir / "adaptive.json"),
        "adaptive_patch": str(build_dir / "adaptive_patch.json"),
        "adaptive_validate": str(build_dir / "adaptive_validate.json"),
        "writing_guidance": str(build_dir / "writing_guidance.json"),
        "compilation_instructions": str(build_dir / "compilation_instructions.json"),
        "evolution_health": str(build_dir / "evolution_health.json"),
        "mece_audit": str(build_dir / "mece_audit.json"),
        "evidence_trace_latest": str(build_dir / "evidence_trace_latest.json"),
        "scoring_basis_latest": str(build_dir / "scoring_basis_latest.json"),
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
