from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.engine.evaluation import evaluate_project_variants  # noqa: E402
from app.storage import (  # noqa: E402
    ensure_data_dirs,
    load_projects,
    load_qingtian_results,
    load_score_reports,
    load_submissions,
)


def _render_markdown(project_rows: List[Dict[str, Any]]) -> str:
    lines = [
        "# Golden Dataset Evaluation",
        "",
        "| Project | Samples(QT) | V1 MAE | V2 MAE | Current MAE | V2+Calib MAE | V1 Spearman | V2 Spearman | Current Spearman | V2+Calib Spearman |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in project_rows:
        pid = row.get("project_id")
        variants = row.get("variants") or {}
        v1 = variants.get("v1") or {}
        v2 = variants.get("v2") or {}
        current = variants.get("current") or {}
        v2c = variants.get("v2_calib") or {}
        lines.append(
            f"| {pid} | {row.get('sample_count_qt', 0)} | "
            f"{v1.get('mae', 0)} | {v2.get('mae', 0)} | {current.get('mae', 0)} | {v2c.get('mae', 0)} | "
            f"{v1.get('spearman', 0)} | {v2.get('spearman', 0)} | {current.get('spearman', 0)} | {v2c.get('spearman', 0)} |"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate V1/V2/current/V2+Calib on golden dataset projects."
    )
    parser.add_argument(
        "--input", type=str, default="build/golden_dataset.json", help="Golden dataset path."
    )
    parser.add_argument(
        "--json-out", type=str, default="build/golden_evaluation.json", help="JSON output path."
    )
    parser.add_argument(
        "--md-out", type=str, default="build/golden_evaluation.md", help="Markdown output path."
    )
    args = parser.parse_args()

    ensure_data_dirs()
    projects = load_projects()
    submissions = load_submissions()
    reports = load_score_reports()
    qts = load_qingtian_results()

    inp = Path(args.input)
    if inp.exists():
        dataset = json.loads(inp.read_text(encoding="utf-8"))
        project_ids = [
            str(p.get("project_id")) for p in (dataset.get("projects") or []) if p.get("project_id")
        ]
    else:
        project_ids = [str(p.get("id")) for p in projects if p.get("id")]

    evaluations: List[Dict[str, Any]] = []
    for pid in project_ids:
        evaluations.append(
            evaluate_project_variants(
                project_id=pid,
                submissions=submissions,
                score_reports=reports,
                qingtian_results=qts,
            )
        )

    payload = {
        "project_count": len(evaluations),
        "projects": evaluations,
    }

    json_out = Path(args.json_out)
    json_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    md_out = Path(args.md_out)
    md_out.parent.mkdir(parents=True, exist_ok=True)
    md_out.write_text(_render_markdown(evaluations), encoding="utf-8")

    print(f"evaluation json: {json_out}")
    print(f"evaluation markdown: {md_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
