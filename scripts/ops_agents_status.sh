#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

PID_FILE="${PID_FILE:-$ROOT_DIR/build/ops_agents.pid}"
STATUS_JSON="${STATUS_JSON:-$ROOT_DIR/build/ops_agents_status.json}"
SCREEN_SESSION="${SCREEN_SESSION:-zhifei_ops_agents}"
AUTO_HEAL="${AUTO_HEAL:-1}"

running="no"
pid_value=""
payload_stale="no"
coverage_gap="no"
missing_agents=""

refresh_running_state() {
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
}

refresh_payload_state() {
  payload_stale="no"
  coverage_gap="no"
  missing_agents=""
  if [[ ! -f "$STATUS_JSON" ]]; then
    return 0
  fi

  while IFS='=' read -r key value; do
    case "$key" in
      payload_stale) payload_stale="$value" ;;
      coverage_gap) coverage_gap="$value" ;;
      missing_agents) missing_agents="$value" ;;
    esac
  done < <(
    python3 - "$STATUS_JSON" <<'PY'
import json
import sys
from pathlib import Path

from app.engine.ops_agents import OPS_AGENT_NAMES, ops_agents_snapshot_is_stale

path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
runtime = data.get("runtime") or {}
agents = data.get("agents") or {}
missing = [name for name in OPS_AGENT_NAMES if name not in agents]
print(
    "payload_stale="
    + ("yes" if ops_agents_snapshot_is_stale(data.get("generated_at"), interval_seconds=runtime.get("interval_seconds")) else "no")
)
print("coverage_gap=" + ("yes" if missing else "no"))
print("missing_agents=" + ",".join(missing))
PY
  )
}

refresh_running_state
refresh_payload_state

snapshot_stale="no"
if [[ "$payload_stale" == "yes" ]]; then
  snapshot_stale="yes"
elif [[ "$running" != "yes" && -f "$STATUS_JSON" ]]; then
  snapshot_stale="yes"
fi

auto_repaired="no"
if [[ "$AUTO_HEAL" == "1" ]] && { [[ "$running" != "yes" ]] || [[ "$snapshot_stale" == "yes" ]]; }; then
  "$ROOT_DIR/scripts/start_ops_agents.sh" >/dev/null
  sleep 2
  auto_repaired="yes"
  refresh_running_state
  refresh_payload_state
  snapshot_stale="no"
  if [[ "$payload_stale" == "yes" ]]; then
    snapshot_stale="yes"
  elif [[ "$running" != "yes" && -f "$STATUS_JSON" ]]; then
    snapshot_stale="yes"
  fi
fi

echo "ops_agents_running=$running"
if [[ -n "${pid_value}" ]]; then
  echo "ops_agents_pid=$pid_value"
fi
echo "status_snapshot_stale=$snapshot_stale"
echo "auto_repaired=$auto_repaired"

if [[ -f "$STATUS_JSON" ]]; then
  python3 - "$STATUS_JSON" "$snapshot_stale" "$running" "$coverage_gap" "$missing_agents" <<'PY'
import json
import sys
from pathlib import Path

from app.engine.ops_agents import OPS_AGENT_NAMES

path = Path(sys.argv[1])
snapshot_stale = sys.argv[2] == "yes"
running = sys.argv[3] == "yes"
coverage_gap = sys.argv[4] == "yes"
missing_agents = [item for item in sys.argv[5].split(",") if item]
data = json.loads(path.read_text(encoding="utf-8"))
overall = data.get("overall") or {}
effective = overall.get("status")
if coverage_gap:
    effective = "fail"
elif snapshot_stale:
    effective = "stale" if running else "stopped"
print(f"generated_at={data.get('generated_at')}")
print(f"overall_status={overall.get('status')}")
print(f"effective_overall_status={effective}")
print(f"overall_duration_ms={overall.get('duration_ms')}")
if coverage_gap:
    print("coverage_gap=yes")
    print("missing_agents=" + ",".join(missing_agents))
for key in OPS_AGENT_NAMES:
    row = (data.get("agents") or {}).get(key) or {}
    print(f"{key}={row.get('status', 'unknown')}")
PY
else
  echo "status_file_missing=$STATUS_JSON"
  if [[ "$running" != "yes" ]]; then
    echo "effective_overall_status=stopped"
  fi
fi
