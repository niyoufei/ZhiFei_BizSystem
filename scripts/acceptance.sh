#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

API_KEY="${API_KEY:-}"
PORT="${PORT:-8000}"
RUN_TESTS="${RUN_TESTS:-1}"
RUN_BROWSER_SMOKE="${RUN_BROWSER_SMOKE:-auto}"
SUMMARY_FILE="${SUMMARY_FILE:-$ROOT_DIR/build/acceptance_summary.json}"

if [[ -z "$API_KEY" ]]; then
  if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
  else
    PYTHON_BIN="python3"
  fi
  API_KEY="$("$PYTHON_BIN" "$ROOT_DIR/scripts/resolve_api_key.py" --preferred-role admin 2>/dev/null || true)"
fi

started_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
step_doctor=0
step_e2e=0
step_browser_smoke=0
step_mece=0
step_hygiene=0
step_coverage=0
step_tests=0
tests_skipped=0
browser_smoke_skipped=0
status="FAIL"
cleanup_retained_e2e_project_on_exit=0
git_is_repo=0
git_ref="unknown"
git_head="null"
git_has_commit=0
workspace_dirty=0
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git_is_repo=1
  git_ref="$(git symbolic-ref --short HEAD 2>/dev/null || echo "detached")"
  if git rev-parse --verify HEAD >/dev/null 2>&1; then
    git_has_commit=1
    git_head="$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")"
  fi
  if [[ -n "$(git status --porcelain 2>/dev/null || true)" ]]; then
    workspace_dirty=1
  fi
fi

write_summary() {
  local rc="$1"
  local ended_at
  ended_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  if [[ "$rc" -eq 0 ]]; then
    status="PASS"
  else
    status="FAIL"
  fi
  python3 - "$SUMMARY_FILE" "$status" "$rc" "$started_at" "$ended_at" "$step_doctor" "$step_e2e" "$step_browser_smoke" "$step_mece" "$step_hygiene" "$step_coverage" "$step_tests" "$RUN_TESTS" "$tests_skipped" "$browser_smoke_skipped" "$git_is_repo" "$git_ref" "$git_head" "$git_has_commit" "$workspace_dirty" <<'PY'
import json
import sys
from pathlib import Path

summary_file = Path(sys.argv[1])
summary = {
    "status": sys.argv[2],
    "exit_code": int(sys.argv[3]),
    "started_at": sys.argv[4],
    "ended_at": sys.argv[5],
    "steps": {
        "doctor_strict_ok": bool(int(sys.argv[6])),
        "e2e_strict_ok": bool(int(sys.argv[7])),
        "browser_button_smoke_ok": (None if bool(int(sys.argv[15])) else bool(int(sys.argv[8]))),
        "mece_audit_ok": bool(int(sys.argv[9])),
        "data_hygiene_ok": bool(int(sys.argv[10])),
        "spec_coverage_ok": bool(int(sys.argv[11])),
        "tests_ok": (None if bool(int(sys.argv[14])) else bool(int(sys.argv[12]))),
    },
    "tests_skipped": bool(int(sys.argv[14])),
    "browser_smoke_skipped": bool(int(sys.argv[15])),
    "run_tests": bool(int(sys.argv[13])),
    "git": {
        "is_repo": bool(int(sys.argv[16])),
        "ref": sys.argv[17],
        "head": (None if sys.argv[18] == "null" else sys.argv[18]),
        "has_commit": bool(int(sys.argv[19])),
        "workspace_dirty": bool(int(sys.argv[20])),
    },
    "artifacts": {
        "e2e_summary": "/Users/youfeini/Desktop/ZhiFei_BizSystem/build/e2e_flow/summary.json",
        "browser_button_smoke_json": "/Users/youfeini/Desktop/ZhiFei_BizSystem/build/browser_button_smoke.json",
        "browser_button_smoke_md": "/Users/youfeini/Desktop/ZhiFei_BizSystem/build/browser_button_smoke.md",
        "mece_audit_json": "/Users/youfeini/Desktop/ZhiFei_BizSystem/build/mece_audit_latest.json",
        "mece_audit_md": "/Users/youfeini/Desktop/ZhiFei_BizSystem/build/mece_audit_latest.md",
        "data_hygiene_json": "/Users/youfeini/Desktop/ZhiFei_BizSystem/build/data_hygiene_latest.json",
        "data_hygiene_md": "/Users/youfeini/Desktop/ZhiFei_BizSystem/build/data_hygiene_latest.md",
        "v2_spec_coverage_md": "/Users/youfeini/Desktop/ZhiFei_BizSystem/build/v2_spec_coverage.md",
        "web_button_contract_md": "/Users/youfeini/Desktop/ZhiFei_BizSystem/build/web_button_contract.md",
    },
}
summary_file.parent.mkdir(parents=True, exist_ok=True)
summary_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"[acceptance] summary: {summary_file}")
PY
}

cleanup_retained_e2e_project() {
  if [[ "$cleanup_retained_e2e_project_on_exit" != "1" ]]; then
    return 0
  fi
  local summary_file="$ROOT_DIR/build/e2e_flow/summary.json"
  if [[ ! -f "$summary_file" ]]; then
    return 0
  fi
  local retained_project_id=""
  retained_project_id="$(python3 - "$summary_file" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    print("")
    raise SystemExit(0)
print(str(data.get("project_id") or "").strip())
PY
)"
  if [[ -z "$retained_project_id" ]]; then
    return 0
  fi
  local delete_url="http://127.0.0.1:${PORT}/api/v1/projects/${retained_project_id}"
  local code="000"
  if [[ -n "$API_KEY" ]]; then
    code="$(curl -sS -o /dev/null -w "%{http_code}" -H "X-API-Key: $API_KEY" -X DELETE "$delete_url" || true)"
  else
    code="$(curl -sS -o /dev/null -w "%{http_code}" -X DELETE "$delete_url" || true)"
  fi
  if [[ "${code:0:1}" == "2" || "$code" == "404" ]]; then
    echo "[acceptance] cleanup: retained browser-smoke project handled ($retained_project_id, http=$code)"
  else
    echo "[acceptance] cleanup: retained browser-smoke project delete failed ($retained_project_id, http=$code)"
  fi
}

run_strict_e2e_flow() {
  local keep_project="$1"
  local raw_log="$ROOT_DIR/build/e2e_flow/acceptance_e2e_raw.log"
  local clean_log="$ROOT_DIR/build/e2e_flow/acceptance_e2e.log"
  local rc=0
  mkdir -p "$ROOT_DIR/build/e2e_flow"
  rm -f "$raw_log" "$clean_log"
  set +e
  KEEP_E2E_PROJECT="$keep_project" STRICT=1 API_KEY="$API_KEY" BASE_URL="http://127.0.0.1:${PORT}" \
    bash -x "$ROOT_DIR/scripts/e2e_api_flow.sh" >"$raw_log" 2>&1
  rc=$?
  set -e
  python3 - "$raw_log" "$clean_log" <<'PY'
from pathlib import Path
import sys

raw_path = Path(sys.argv[1])
clean_path = Path(sys.argv[2])
text = raw_path.read_text(encoding="utf-8", errors="replace") if raw_path.exists() else ""
text = text.replace("\x04", "").replace("\x08", "")
clean_path.write_text(text, encoding="utf-8")
PY
  echo "[acceptance] e2e log: $clean_log"
  if [[ "$rc" -ne 0 ]]; then
    tail -n 120 "$clean_log" || true
  fi
  return "$rc"
}

trap 'rc=$?; cleanup_retained_e2e_project; write_summary "$rc"' EXIT

echo "[acceptance] step 1/7: strict doctor"
STRICT=1 API_KEY="$API_KEY" PORT="$PORT" ./scripts/doctor.sh
step_doctor=1

echo "[acceptance] step 2/7: strict e2e flow"
if [[ "$RUN_BROWSER_SMOKE" == "0" ]]; then
  run_strict_e2e_flow 0
else
  cleanup_retained_e2e_project_on_exit=1
  run_strict_e2e_flow 1
fi
step_e2e=1

echo "[acceptance] step 3/7: browser button smoke"
if [[ "$RUN_BROWSER_SMOKE" == "0" ]]; then
  echo "[acceptance] browser smoke disabled (RUN_BROWSER_SMOKE=$RUN_BROWSER_SMOKE)"
  browser_smoke_skipped=1
else
  smoke_rc=0
  if python3 "$ROOT_DIR/scripts/browser_button_smoke.py" \
    --base-url "http://127.0.0.1:${PORT}" \
    --summary-file "$ROOT_DIR/build/e2e_flow/summary.json" \
    --output-json "$ROOT_DIR/build/browser_button_smoke.json" \
    --output-md "$ROOT_DIR/build/browser_button_smoke.md" \
    --artifact-dir "$ROOT_DIR/output/playwright/browser_button_smoke"; then
    step_browser_smoke=1
  else
    smoke_rc=$?
    if [[ "$RUN_BROWSER_SMOKE" == "auto" && "$smoke_rc" == "2" ]]; then
      echo "[acceptance] browser smoke skipped: browser runtime unavailable"
      browser_smoke_skipped=1
    else
      exit "$smoke_rc"
    fi
  fi
fi

echo "[acceptance] step 4/7: mece audit"
STRICT=1 API_KEY="$API_KEY" BASE_URL="http://127.0.0.1:${PORT}" ./scripts/mece_audit.sh
step_mece=1

echo "[acceptance] step 5/7: data hygiene (auto-repair)"
APPLY=1 STRICT=1 FAIL_ON_ORPHAN=0 API_KEY="$API_KEY" BASE_URL="http://127.0.0.1:${PORT}" ./scripts/data_hygiene.sh
step_hygiene=1

echo "[acceptance] step 6/7: v2 spec coverage"
if [[ -x ".venv/bin/python" ]]; then
  .venv/bin/python scripts/check_v2_spec_coverage.py --strict
else
  python3 scripts/check_v2_spec_coverage.py --strict
fi
step_coverage=1

if [[ "$RUN_TESTS" == "1" ]]; then
  echo "[acceptance] step 7/7: pytest"
  if [[ -x ".venv/bin/pytest" ]]; then
    .venv/bin/pytest -q
  else
    python3 -m pytest -q
  fi
  step_tests=1
else
  echo "[acceptance] step 7/7: skipped tests (RUN_TESTS=$RUN_TESTS)"
  tests_skipped=1
fi

echo "[acceptance] final cleanup: data hygiene repair"
APPLY=1 STRICT=1 FAIL_ON_ORPHAN=0 API_KEY="$API_KEY" BASE_URL="http://127.0.0.1:${PORT}" ./scripts/data_hygiene.sh >/dev/null

echo "[acceptance] PASS"
echo "[acceptance] artifacts:"
echo "  - /Users/youfeini/Desktop/ZhiFei_BizSystem/build/e2e_flow/summary.json"
echo "  - /Users/youfeini/Desktop/ZhiFei_BizSystem/build/e2e_flow/acceptance_e2e.log"
echo "  - /Users/youfeini/Desktop/ZhiFei_BizSystem/build/browser_button_smoke.md"
echo "  - /Users/youfeini/Desktop/ZhiFei_BizSystem/build/mece_audit_latest.md"
echo "  - /Users/youfeini/Desktop/ZhiFei_BizSystem/build/data_hygiene_latest.md"
echo "  - /Users/youfeini/Desktop/ZhiFei_BizSystem/build/v2_spec_coverage.md"
echo "  - /Users/youfeini/Desktop/ZhiFei_BizSystem/build/web_button_contract.md"
