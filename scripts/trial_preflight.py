#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

trial_preflight_module = importlib.import_module("app.trial_preflight")
build_trial_preflight_report = trial_preflight_module.build_trial_preflight_report
render_trial_preflight_markdown = trial_preflight_module.render_trial_preflight_markdown


def _resolve_api_key() -> str:
    resolver = ROOT / "scripts" / "resolve_api_key.py"
    if not resolver.exists():
        return ""
    python_bin = ROOT / ".venv" / "bin" / "python"
    cmd = [
        str(python_bin if python_bin.exists() else "python3"),
        str(resolver),
        "--preferred-role",
        "ops",
        "--fallback-role",
        "admin",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except Exception:
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def _get_json(
    base_url: str, path: str, *, api_key: str = "", query: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    suffix = path
    if query:
        suffix = f"{path}?{urlencode(query)}"
    request = Request(base_url.rstrip("/") + suffix)
    if api_key:
        request.add_header("X-API-Key", api_key)
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a project trial preflight report.")
    parser.add_argument("--project-id", required=True, help="Project id to audit before trial run.")
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="Base URL of the running service.",
    )
    parser.add_argument(
        "--output-json",
        default=str(ROOT / "build" / "trial_preflight_latest.json"),
        help="Path to write JSON report.",
    )
    parser.add_argument(
        "--output-md",
        default=str(ROOT / "build" / "trial_preflight_latest.md"),
        help="Path to write Markdown report.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when the project is not ready for a trial run.",
    )
    args = parser.parse_args()

    api_key = _resolve_api_key()
    project_id = str(args.project_id or "").strip()
    base_url = str(args.base_url or "").strip().rstrip("/")
    if not project_id:
        raise SystemExit("missing --project-id")

    try:
        projects = _get_json(base_url, "/api/v1/projects", api_key=api_key)
        self_check = _get_json(
            base_url,
            "/api/v1/system/self_check",
            api_key=api_key,
            query={"project_id": project_id},
        )
        scoring_readiness = _get_json(
            base_url,
            f"/api/v1/projects/{project_id}/scoring_readiness",
            api_key=api_key,
        )
        mece_audit = _get_json(
            base_url,
            f"/api/v1/projects/{project_id}/mece_audit",
            api_key=api_key,
        )
        evolution_health = _get_json(
            base_url,
            f"/api/v1/projects/{project_id}/evolution/health",
            api_key=api_key,
        )
        scoring_diagnostic = _get_json(
            base_url,
            f"/api/v1/projects/{project_id}/scoring_diagnostic/latest",
            api_key=api_key,
        )
        evaluation_summary = _get_json(base_url, "/api/v1/evaluation/summary", api_key=api_key)
        data_hygiene = _get_json(base_url, "/api/v1/system/data_hygiene", api_key=api_key)
    except HTTPError as exc:
        raise SystemExit(f"request failed: {exc.code} {exc.reason}") from exc
    except URLError as exc:
        raise SystemExit(f"request failed: {exc.reason}") from exc

    project_name = project_id
    if isinstance(projects, list):
        for item in projects:
            if isinstance(item, dict) and str(item.get("id") or "").strip() == project_id:
                project_name = str(item.get("name") or "").strip() or project_id
                break

    report = build_trial_preflight_report(
        base_url=base_url,
        project_id=project_id,
        project_name=project_name,
        self_check=self_check,
        scoring_readiness=scoring_readiness,
        mece_audit=mece_audit,
        evolution_health=evolution_health,
        scoring_diagnostic=scoring_diagnostic,
        evaluation_summary=evaluation_summary,
        data_hygiene=data_hygiene,
    )

    json_path = Path(args.output_json)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    md_path = Path(args.output_md)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_trial_preflight_markdown(report), encoding="utf-8")

    print(f"json: {json_path}")
    print(f"markdown: {md_path}")
    print(f"trial_run_ready: {report.get('trial_run_ready')}")
    print(f"status: {report.get('status')}")
    print(f"status_label: {report.get('status_label')}")

    if args.strict and not bool(report.get("trial_run_ready")):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
