#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

PORT="${PORT:-8000}"
BASE_URL="${BASE_URL:-http://127.0.0.1:${PORT}}"
API_KEY="${API_KEY:-}"
APPLY="${APPLY:-0}"
STRICT="${STRICT:-0}"
FAIL_ON_ORPHAN="${FAIL_ON_ORPHAN:-0}"
OUT_JSON="${OUT_JSON:-$ROOT_DIR/build/data_hygiene_latest.json}"
OUT_MD="${OUT_MD:-$ROOT_DIR/build/data_hygiene_latest.md}"

if [[ -z "$API_KEY" ]]; then
  if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
  else
    PYTHON_BIN="python3"
  fi
  API_KEY="$("$PYTHON_BIN" "$ROOT_DIR/scripts/resolve_api_key.py" --preferred-role ops --fallback-role admin 2>/dev/null || true)"
fi

curl_with_auth() {
  if [[ -n "$API_KEY" ]]; then
    curl -fsS -H "X-API-Key: $API_KEY" "$@"
  else
    curl -fsS "$@"
  fi
}

echo "[hygiene] health check..."
curl_with_auth "$BASE_URL/health" >/dev/null

if [[ "$APPLY" == "1" ]]; then
  echo "[hygiene] repair mode enabled: POST /api/v1/system/data_hygiene/repair"
  curl_with_auth -X POST "$BASE_URL/api/v1/system/data_hygiene/repair" >/dev/null
fi

echo "[hygiene] audit mode: GET /api/v1/system/data_hygiene"
payload="$(curl_with_auth "$BASE_URL/api/v1/system/data_hygiene")"

mkdir -p "$(dirname "$OUT_JSON")"
printf '%s' "$payload" >"$OUT_JSON"

python3 - "$OUT_JSON" "$OUT_MD" "$STRICT" "$FAIL_ON_ORPHAN" <<'PY'
import json
import sys
from pathlib import Path

json_path = Path(sys.argv[1])
md_path = Path(sys.argv[2])
strict = bool(int(sys.argv[3]))
fail_on_orphan = bool(int(sys.argv[4]))

data = json.loads(json_path.read_text(encoding="utf-8"))

lines = [
    "# Data Hygiene Audit",
    "",
    f"- generated_at: `{data.get('generated_at', '-')}`",
    f"- apply_mode: `{bool(data.get('apply_mode'))}`",
    f"- valid_project_count: `{int(data.get('valid_project_count') or 0)}`",
    f"- orphan_records_total: `{int(data.get('orphan_records_total') or 0)}`",
    f"- cleaned_records_total: `{int(data.get('cleaned_records_total') or 0)}`",
    "",
    "## Datasets",
]

for row in data.get("datasets") or []:
    name = row.get("name", "-")
    total = int(row.get("total") or 0)
    orphan = int(row.get("orphan_count") or 0)
    cleaned = int(row.get("cleaned_count") or 0)
    mode = row.get("mode", "-")
    lines.append(
        f"- `{name}`: total={total}, orphan={orphan}, cleaned={cleaned}, mode={mode}"
    )

lines.append("")
lines.append("## Recommendations")
for item in data.get("recommendations") or []:
    lines.append(f"- {item}")
lines.append("")

md_path.parent.mkdir(parents=True, exist_ok=True)
md_path.write_text("\n".join(lines), encoding="utf-8")

orphan_total = int(data.get("orphan_records_total") or 0)
print(f"json: {json_path}")
print(f"markdown: {md_path}")
print(f"orphan_records_total: {orphan_total}")
if strict and fail_on_orphan and orphan_total > 0:
    raise SystemExit(1)
PY

echo "[hygiene] PASS"
