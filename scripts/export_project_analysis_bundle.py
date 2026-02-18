from __future__ import annotations

import argparse
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


def _choose_project(project_id: str | None) -> dict | None:
    projects = load_projects()
    if not projects:
        return None
    if project_id:
        for p in projects:
            if str(p.get("id")) == project_id:
                return p
        return None
    # 默认取最近更新项目
    projects = sorted(
        projects, key=lambda x: str(x.get("updated_at") or x.get("created_at") or ""), reverse=True
    )
    return projects[0]


def main() -> int:
    parser = argparse.ArgumentParser(description="Export project analysis bundle markdown.")
    parser.add_argument(
        "--project-id", type=str, default=None, help="Project ID. If omitted, use latest project."
    )
    parser.add_argument("--output", type=str, default=None, help="Output markdown path.")
    args = parser.parse_args()

    ensure_data_dirs()
    project = _choose_project(args.project_id)
    if project is None:
        print("no project found, cannot export analysis bundle")
        return 2

    project_id = str(project.get("id"))
    factors_payload = _build_scoring_factors_overview(project_id)
    eval_payload = evaluate_project_variants(
        project_id=project_id,
        submissions=load_submissions(),
        score_reports=load_score_reports(),
        qingtian_results=load_qingtian_results(),
    )
    markdown = _render_project_analysis_bundle_markdown(
        project=project,
        factors_payload=factors_payload,
        evaluation_payload=eval_payload,
    )

    if args.output:
        out = Path(args.output)
    else:
        out = ROOT / "build" / f"analysis_bundle_{project_id}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(markdown, encoding="utf-8")

    print(f"analysis bundle saved: {out}")
    print(f"project_id: {project_id}")
    print(f"sample_count_qt: {eval_payload.get('sample_count_qt', 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
