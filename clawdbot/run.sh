#!/usr/bin/env bash
set -u

ROOT="$(pwd)"
STATE_DIR="$ROOT/build/clawdbot"
PROMPT_FILE="$ROOT/clawdbot/supervisor_prompt.txt"
STOP_FLAG="$STATE_DIR/STOP"
DONE_FLAG="$STATE_DIR/DONE"

mkdir -p "$STATE_DIR"
STATUS_MD="$STATE_DIR/status.md"
AUDIT_LOG="$STATE_DIR/audit.log"
BLOCKERS_MD="$STATE_DIR/blockers.md"

ts() { date +"%Y-%m-%dT%H:%M:%S%z"; }
log() { echo "[$(ts)] $*" | tee -a "$AUDIT_LOG"; }

AGENT_BIN=""
for c in agent cursor-agent cursor_agent; do
  if command -v "$c" >/dev/null 2>&1; then AGENT_BIN="$c"; break; fi
done
if [[ -z "$AGENT_BIN" ]]; then
  echo "ERROR: 找不到 Cursor CLI（agent/cursor-agent）。"
  exit 1
fi

HELP="$("$AGENT_BIN" --help 2>/dev/null || true)"
PRINT_FLAG="--print"; echo "$HELP" | grep -q -- '--print' || PRINT_FLAG="-p"
OUTFMT_FLAG="--output-format"; echo "$HELP" | grep -q -- '--output-format' || OUTFMT_FLAG="--output"

[[ -f "$STATUS_MD" ]] || cat > "$STATUS_MD" <<'MD'
# clawdbot status
- last_run: never
- last_result: none
- last_action: none
- next_step: init
MD
[[ -f "$BLOCKERS_MD" ]] || echo "# blockers" > "$BLOCKERS_MD"

# git 分支（失败不阻塞）
if command -v git >/dev/null 2>&1; then
  if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git checkout -B clawdbot/autopilot >/dev/null 2>&1 || true
  else
    git init >/dev/null 2>&1 || true
    git checkout -B clawdbot/autopilot >/dev/null 2>&1 || true
  fi
fi

log "clawdbot started. CLI=$AGENT_BIN (stop: touch $STOP_FLAG) (done: $DONE_FLAG)"

while true; do
  [[ -f "$STOP_FLAG" ]] && { log "STOP detected. Exit."; exit 0; }
# (disabled) DONE exit removed

  log "=== ITERATION START ==="
  OUT_TMP="$(mktemp)"

  "$AGENT_BIN" "$PRINT_FLAG" --force "$OUTFMT_FLAG" text "$(cat "$PROMPT_FILE")" \
    2>&1 | tee -a "$AUDIT_LOG" | tee "$OUT_TMP"

  # 只有 DONE 文件真实存在才退出（防止误判）
# (disabled) DONE text exit removed

  rm -f "$OUT_TMP"
  log "Continue in 2s..."
  sleep 2
done
