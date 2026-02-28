#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
API_KEY="${API_KEY:-}"
STRICT="${STRICT:-0}"
OUT_JSON="${OUT_JSON:-$ROOT_DIR/build/mece_audit_latest.json}"
OUT_MD="${OUT_MD:-$ROOT_DIR/build/mece_audit_latest.md}"

if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
else
  PYTHON_BIN="python3"
fi

curl_with_auth() {
  if [[ -n "$API_KEY" ]]; then
    curl -fsS -H "X-API-Key: $API_KEY" "$@"
  else
    curl -fsS "$@"
  fi
}

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

echo "[mece] health check..."
curl_with_auth "$BASE_URL/health" >"$tmp_dir/health.json"

echo "[mece] load projects..."
curl_with_auth "$BASE_URL/api/v1/projects" >"$tmp_dir/projects.json"

"$PYTHON_BIN" - "$BASE_URL" "$tmp_dir/projects.json" "$tmp_dir/project_ids.txt" <<'PY'
import json
import sys
from pathlib import Path

base_url = sys.argv[1]
projects_path = Path(sys.argv[2])
out_path = Path(sys.argv[3])

projects = json.loads(projects_path.read_text(encoding="utf-8"))
ids = []
if isinstance(projects, list):
    for row in projects:
        if isinstance(row, dict):
            pid = str(row.get("id") or "").strip()
            if pid:
                ids.append(pid)
out_path.write_text("\n".join(ids), encoding="utf-8")
print(f"[mece] discovered projects: {len(ids)} @ {base_url}")
PY

project_count=0
api_fail_count=0
while IFS= read -r pid; do
  [[ -z "$pid" ]] && continue
  project_count=$((project_count + 1))
  out_file="$tmp_dir/mece_${pid}.json"
  if ! curl_with_auth "$BASE_URL/api/v1/projects/$pid/mece_audit" >"$out_file"; then
    echo "[mece] WARN: audit failed for project_id=$pid"
    api_fail_count=$((api_fail_count + 1))
  fi
done <"$tmp_dir/project_ids.txt"

"$PYTHON_BIN" - "$BASE_URL" "$tmp_dir" "$OUT_JSON" "$OUT_MD" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

base_url = sys.argv[1]
tmp_dir = Path(sys.argv[2])
out_json = Path(sys.argv[3])
out_md = Path(sys.argv[4])

audits = []
for fp in sorted(tmp_dir.glob("mece_*.json")):
    try:
        payload = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        continue
    if isinstance(payload, dict):
        audits.append(payload)

summary = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "base_url": base_url,
    "project_count": len(audits),
    "critical_count": 0,
    "watch_count": 0,
    "good_count": 0,
    "avg_health_score": 0.0,
    "projects": [],
}

scores = []
for a in audits:
    pid = str(a.get("project_id") or "")
    overall = a.get("overall") if isinstance(a.get("overall"), dict) else {}
    level = str(overall.get("level") or "unknown")
    score = float(overall.get("health_score") or 0.0)
    scores.append(score)
    if level == "critical":
        summary["critical_count"] += 1
    elif level == "watch":
        summary["watch_count"] += 1
    elif level == "good":
        summary["good_count"] += 1
    summary["projects"].append(
        {
            "project_id": pid,
            "health_score": score,
            "level": level,
            "pass_count": int(overall.get("pass_count") or 0),
            "warn_count": int(overall.get("warn_count") or 0),
            "fail_count": int(overall.get("fail_count") or 0),
            "recommendations": list(a.get("recommendations") or [])[:5],
        }
    )

if scores:
    summary["avg_health_score"] = round(sum(scores) / len(scores), 2)

out_json.parent.mkdir(parents=True, exist_ok=True)
out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

lines = [
    "# MECE 审计汇总",
    "",
    f"- 生成时间: `{summary['generated_at']}`",
    f"- 服务地址: `{base_url}`",
    f"- 项目数: `{summary['project_count']}`",
    f"- 平均健康分: `{summary['avg_health_score']}`",
    f"- good/watch/critical: `{summary['good_count']}/{summary['watch_count']}/{summary['critical_count']}`",
    "",
    "## 项目列表",
]
if summary["projects"]:
    for row in summary["projects"]:
        lines.append(
            "- "
            + f"`{row['project_id']}`: level=`{row['level']}`, "
            + f"health=`{row['health_score']}`, "
            + f"pass/warn/fail=`{row['pass_count']}/{row['warn_count']}/{row['fail_count']}`"
        )
        for rec in row["recommendations"]:
            lines.append(f"  - 建议: {rec}")
else:
    lines.append("- 当前无项目可审计。")
lines.append("")
out_md.parent.mkdir(parents=True, exist_ok=True)
out_md.write_text("\n".join(lines), encoding="utf-8")

print(f"[mece] json: {out_json}")
print(f"[mece] markdown: {out_md}")
PY

if [[ "$api_fail_count" -gt 0 ]]; then
  echo "[mece] WARN: ${api_fail_count} project(s) audit request failed."
  if [[ "$STRICT" == "1" ]]; then
    echo "[mece] strict mode enabled, treat audit request failure as error."
    exit 1
  fi
fi

echo "[mece] PASS"
