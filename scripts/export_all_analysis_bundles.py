from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.engine.evaluation import evaluate_project_variants  # noqa: E402
from app.main import (  # noqa: E402
    _build_scoring_factors_overview,
    _render_project_analysis_bundle_markdown,
)
from app.storage import (  # noqa: E402
    ensure_data_dirs,
    load_projects,
    load_qingtian_results,
    load_score_reports,
    load_submissions,
)


def main() -> int:
    ensure_data_dirs()
    projects = load_projects()
    if not projects:
        print("no projects found")
        return 2

    submissions = load_submissions()
    reports = load_score_reports()
    qts = load_qingtian_results()

    out_dir = ROOT / "build" / "analysis_bundles"
    out_dir.mkdir(parents=True, exist_ok=True)

    index_lines = ["# Analysis Bundles Index", ""]
    for p in projects:
        pid = str(p.get("id") or "")
        if not pid:
            continue
        factors = _build_scoring_factors_overview(pid)
        evaluation = evaluate_project_variants(
            project_id=pid,
            submissions=submissions,
            score_reports=reports,
            qingtian_results=qts,
        )
        markdown = _render_project_analysis_bundle_markdown(
            project=p,
            factors_payload=factors,
            evaluation_payload=evaluation,
        )
        out = out_dir / f"analysis_bundle_{pid}.md"
        out.write_text(markdown, encoding="utf-8")
        print(f"saved: {out}")
        index_lines.append(f"- {pid}: `{out}`")

    index = out_dir / "_index.md"
    index.write_text("\n".join(index_lines).strip() + "\n", encoding="utf-8")
    print(f"index: {index}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
