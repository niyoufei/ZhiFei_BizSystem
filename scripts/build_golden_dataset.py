from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.storage import (  # noqa: E402
    ensure_data_dirs,
    load_projects,
    load_qingtian_results,
    load_score_reports,
    load_submissions,
)


def _latest_by_submission(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        sid = str(row.get("submission_id") or "")
        if not sid:
            continue
        prev = latest.get(sid)
        if prev is None or str(row.get("created_at", "")) >= str(prev.get("created_at", "")):
            latest[sid] = row
    return latest


def main() -> int:
    parser = argparse.ArgumentParser(description="Build golden dataset from current storage data.")
    parser.add_argument("--min-samples", type=int, default=5, help="Minimum samples per project.")
    parser.add_argument(
        "--output", type=str, default="build/golden_dataset.json", help="Output path."
    )
    args = parser.parse_args()

    ensure_data_dirs()
    projects = load_projects()
    submissions = load_submissions()
    reports = load_score_reports()
    qts = load_qingtian_results()

    qt_latest = _latest_by_submission(qts)
    reports_by_sid: Dict[str, List[Dict[str, Any]]] = {}
    for r in reports:
        sid = str(r.get("submission_id") or "")
        if not sid:
            continue
        reports_by_sid.setdefault(sid, []).append(r)

    project_cases: Dict[str, List[Dict[str, Any]]] = {}
    for sub in submissions:
        sid = str(sub.get("id") or "")
        pid = str(sub.get("project_id") or "")
        if not sid or not pid:
            continue
        qt = qt_latest.get(sid)
        if not qt:
            continue
        has_v1 = False
        has_v2 = False
        has_v2_calib = False
        for r in reports_by_sid.get(sid, []):
            ev = str(r.get("scoring_engine_version") or "").lower()
            if ev.startswith("v1"):
                has_v1 = True
            if ev.startswith("v2"):
                has_v2 = True
                if r.get("pred_total_score") is not None:
                    has_v2_calib = True
        project_cases.setdefault(pid, []).append(
            {
                "submission_id": sid,
                "qt_total_score": qt.get("qt_total_score"),
                "has_v1": has_v1,
                "has_v2": has_v2,
                "has_v2_calib": has_v2_calib,
            }
        )

    selected: List[Dict[str, Any]] = []
    for p in projects:
        pid = str(p.get("id") or "")
        cases = project_cases.get(pid, [])
        if len(cases) >= args.min_samples:
            selected.append(
                {
                    "project_id": pid,
                    "project_name": p.get("name"),
                    "sample_count": len(cases),
                    "cases": cases,
                }
            )

    if not selected:
        # fallback: pick top-3 projects by available sample count
        top = sorted(project_cases.items(), key=lambda x: len(x[1]), reverse=True)[:3]
        proj_name = {str(p.get("id")): p.get("name") for p in projects}
        for pid, cases in top:
            selected.append(
                {
                    "project_id": pid,
                    "project_name": proj_name.get(pid),
                    "sample_count": len(cases),
                    "cases": cases,
                }
            )

    payload = {
        "min_samples": int(args.min_samples),
        "project_count": len(selected),
        "projects": selected,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"golden dataset saved: {out}")
    print(f"projects: {payload['project_count']}")
    for item in selected:
        print(f"- {item['project_id']} ({item.get('project_name')}) samples={item['sample_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
