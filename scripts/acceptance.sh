#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

API_KEY="${API_KEY:-}"
PORT="${PORT:-8000}"
RUN_TESTS="${RUN_TESTS:-1}"
SUMMARY_FILE="${SUMMARY_FILE:-$ROOT_DIR/build/acceptance_summary.json}"

started_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
step_doctor=0
step_e2e=0
step_mece=0
step_hygiene=0
step_coverage=0
step_tests=0
tests_skipped=0
status="FAIL"
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
  python3 - "$SUMMARY_FILE" "$status" "$rc" "$started_at" "$ended_at" "$step_doctor" "$step_e2e" "$step_mece" "$step_hygiene" "$step_coverage" "$step_tests" "$RUN_TESTS" "$tests_skipped" "$git_is_repo" "$git_ref" "$git_head" "$git_has_commit" "$workspace_dirty" <<'PY'
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
        "mece_audit_ok": bool(int(sys.argv[8])),
        "data_hygiene_ok": bool(int(sys.argv[9])),
        "spec_coverage_ok": bool(int(sys.argv[10])),
        "tests_ok": (None if bool(int(sys.argv[13])) else bool(int(sys.argv[11]))),
    },
    "tests_skipped": bool(int(sys.argv[13])),
    "run_tests": bool(int(sys.argv[12])),
    "git": {
        "is_repo": bool(int(sys.argv[14])),
        "ref": sys.argv[15],
        "head": (None if sys.argv[16] == "null" else sys.argv[16]),
        "has_commit": bool(int(sys.argv[17])),
        "workspace_dirty": bool(int(sys.argv[18])),
    },
    "artifacts": {
        "e2e_summary": "/Users/youfeini/Desktop/ZhiFei_BizSystem/build/e2e_flow/summary.json",
        "mece_audit_json": "/Users/youfeini/Desktop/ZhiFei_BizSystem/build/mece_audit_latest.json",
        "mece_audit_md": "/Users/youfeini/Desktop/ZhiFei_BizSystem/build/mece_audit_latest.md",
        "data_hygiene_json": "/Users/youfeini/Desktop/ZhiFei_BizSystem/build/data_hygiene_latest.json",
        "data_hygiene_md": "/Users/youfeini/Desktop/ZhiFei_BizSystem/build/data_hygiene_latest.md",
        "v2_spec_coverage_md": "/Users/youfeini/Desktop/ZhiFei_BizSystem/build/v2_spec_coverage.md",
    },
}
summary_file.parent.mkdir(parents=True, exist_ok=True)
summary_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"[acceptance] summary: {summary_file}")
PY
}

trap 'write_summary "$?"' EXIT

echo "[acceptance] step 1/6: strict doctor"
STRICT=1 API_KEY="$API_KEY" PORT="$PORT" ./scripts/doctor.sh
step_doctor=1

echo "[acceptance] step 2/6: strict e2e flow"
STRICT=1 API_KEY="$API_KEY" BASE_URL="http://127.0.0.1:${PORT}" ./scripts/e2e_api_flow.sh
step_e2e=1

echo "[acceptance] step 3/6: mece audit"
STRICT=1 API_KEY="$API_KEY" BASE_URL="http://127.0.0.1:${PORT}" ./scripts/mece_audit.sh
step_mece=1

echo "[acceptance] step 4/6: data hygiene (auto-repair)"
APPLY=1 STRICT=1 FAIL_ON_ORPHAN=0 API_KEY="$API_KEY" BASE_URL="http://127.0.0.1:${PORT}" ./scripts/data_hygiene.sh
step_hygiene=1

echo "[acceptance] step 5/6: v2 spec coverage"
if [[ -x ".venv/bin/python" ]]; then
  .venv/bin/python scripts/check_v2_spec_coverage.py --strict
else
  python3 scripts/check_v2_spec_coverage.py --strict
fi
step_coverage=1

if [[ "$RUN_TESTS" == "1" ]]; then
  echo "[acceptance] step 6/6: pytest"
  if [[ -x ".venv/bin/pytest" ]]; then
    .venv/bin/pytest -q
  else
    python3 -m pytest -q
  fi
  step_tests=1
else
  echo "[acceptance] step 6/6: skipped tests (RUN_TESTS=$RUN_TESTS)"
  tests_skipped=1
fi

echo "[acceptance] PASS"
echo "[acceptance] artifacts:"
echo "  - /Users/youfeini/Desktop/ZhiFei_BizSystem/build/e2e_flow/summary.json"
echo "  - /Users/youfeini/Desktop/ZhiFei_BizSystem/build/mece_audit_latest.md"
echo "  - /Users/youfeini/Desktop/ZhiFei_BizSystem/build/data_hygiene_latest.md"
echo "  - /Users/youfeini/Desktop/ZhiFei_BizSystem/build/v2_spec_coverage.md"
