#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

PID_FILE="${PID_FILE:-$ROOT_DIR/build/ops_agents.pid}"
STATUS_JSON="${STATUS_JSON:-$ROOT_DIR/build/ops_agents_status.json}"
SCREEN_SESSION="${SCREEN_SESSION:-zhifei_ops_agents}"

running="no"
pid_value=""
if [[ -f "$PID_FILE" ]]; then
  pid_value="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "${pid_value}" ]] && kill -0 "$pid_value" >/dev/null 2>&1; then
    running="yes"
  fi
fi

if command -v screen >/dev/null 2>&1; then
  if screen -ls 2>/dev/null | grep -q "[.]${SCREEN_SESSION}[[:space:]]"; then
    running="yes"
  fi
fi

snapshot_stale="no"
if [[ "$running" != "yes" && -f "$STATUS_JSON" ]]; then
  snapshot_stale="yes"
fi

echo "ops_agents_running=$running"
if [[ -n "${pid_value}" ]]; then
  echo "ops_agents_pid=$pid_value"
fi
echo "status_snapshot_stale=$snapshot_stale"

if [[ -f "$STATUS_JSON" ]]; then
  python3 - "$STATUS_JSON" "$snapshot_stale" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
snapshot_stale = sys.argv[2] == "yes"
data = json.loads(path.read_text(encoding="utf-8"))
overall = data.get("overall") or {}
print(f"generated_at={data.get('generated_at')}")
print(f"overall_status={overall.get('status')}")
print(f"effective_overall_status={'stopped' if snapshot_stale else overall.get('status')}")
print(f"overall_duration_ms={overall.get('duration_ms')}")
for key in ("sre_watchdog", "data_hygiene", "project_flow", "scoring_quality", "evolution"):
    row = (data.get("agents") or {}).get(key) or {}
    print(f"{key}={row.get('status', 'unknown')}")
PY
else
  echo "status_file_missing=$STATUS_JSON"
  if [[ "$running" != "yes" ]]; then
    echo "effective_overall_status=stopped"
  fi
fi
