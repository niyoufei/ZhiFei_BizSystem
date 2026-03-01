#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


REQUIRED_FILES = [
    "app/engine/v2_scorer.py",
    "app/engine/anchors.py",
    "app/engine/reflection.py",
    "app/engine/calibrator.py",
    "app/engine/evolution.py",
    "app/engine/evidence_units.py",
    "scripts/migrate_v2_p0.py",
    "scripts/build_golden_dataset.py",
    "scripts/evaluate_golden_dataset.py",
    "scripts/e2e_api_flow.sh",
    "scripts/mece_audit.sh",
    "scripts/data_hygiene.sh",
    "scripts/doctor.sh",
]

REQUIRED_PATHS_METHODS = [
    ("/api/v1/projects/{project_id}/expert-profile", "GET"),
    ("/api/v1/projects/{project_id}/expert-profile", "PUT"),
    ("/api/v1/projects/{project_id}/rescore", "POST"),
    ("/api/v1/projects/{project_id}/ground_truth/from_files", "POST"),
    ("/api/v1/projects/{project_id}/analysis_bundle", "GET"),
    ("/api/v1/projects/{project_id}/mece_audit", "GET"),
    ("/api/v1/scoring/factors", "GET"),
    ("/api/v1/scoring/factors/markdown", "GET"),
    ("/api/v1/system/self_check", "GET"),
    ("/api/v1/system/data_hygiene", "GET"),
]


def collect_route_methods() -> Dict[str, List[str]]:
    from app.main import app

    routes: Dict[str, set[str]] = {}
    for route in app.routes:
        path = getattr(route, "path", None)
        if not path:
            continue
        methods = set(getattr(route, "methods", set()) or set())
        if path not in routes:
            routes[path] = set()
        routes[path] |= methods
    return {k: sorted(v) for k, v in sorted(routes.items())}


def build_report() -> Dict[str, object]:
    files = []
    for rel in REQUIRED_FILES:
        path = ROOT / rel
        files.append(
            {
                "path": rel,
                "exists": path.exists(),
            }
        )

    routes = collect_route_methods()
    apis = []
    for path, method in REQUIRED_PATHS_METHODS:
        methods = routes.get(path, [])
        apis.append(
            {
                "path": path,
                "method": method,
                "exists": method in methods,
                "registered_methods": methods,
            }
        )

    missing_files = [f["path"] for f in files if not f["exists"]]
    missing_apis = [f"{a['method']} {a['path']}" for a in apis if not a["exists"]]

    return {
        "ok": not missing_files and not missing_apis,
        "required_files": files,
        "required_apis": apis,
        "missing_files": missing_files,
        "missing_apis": missing_apis,
    }


def to_markdown(report: Dict[str, object]) -> str:
    lines: List[str] = []
    lines.append("# V2 Spec Coverage")
    lines.append("")
    lines.append(f"- ok: `{report['ok']}`")
    lines.append("")
    lines.append("## Files")
    for item in report["required_files"]:
        flag = "OK" if item["exists"] else "MISS"
        lines.append(f"- [{flag}] `{item['path']}`")
    lines.append("")
    lines.append("## APIs")
    for item in report["required_apis"]:
        flag = "OK" if item["exists"] else "MISS"
        methods = ", ".join(item["registered_methods"]) if item["registered_methods"] else "-"
        lines.append(f"- [{flag}] `{item['method']} {item['path']}` (registered: {methods})")
    lines.append("")
    if report["missing_files"] or report["missing_apis"]:
        lines.append("## Missing")
        for item in report["missing_files"]:
            lines.append(f"- file: `{item}`")
        for item in report["missing_apis"]:
            lines.append(f"- api: `{item}`")
    else:
        lines.append("## Result")
        lines.append("- All required files and APIs are present.")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check V2 spec key coverage (files + APIs).")
    parser.add_argument(
        "--output-json",
        default=str(ROOT / "build" / "v2_spec_coverage.json"),
        help="Path to write JSON report.",
    )
    parser.add_argument(
        "--output-md",
        default=str(ROOT / "build" / "v2_spec_coverage.md"),
        help="Path to write Markdown report.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when missing items exist.",
    )
    args = parser.parse_args()

    report = build_report()

    json_path = Path(args.output_json)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    md_path = Path(args.output_md)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(to_markdown(report), encoding="utf-8")

    print(f"json: {json_path}")
    print(f"markdown: {md_path}")
    print(f"ok: {report['ok']}")
    if args.strict and not report["ok"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
