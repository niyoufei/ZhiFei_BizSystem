#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def to_markdown(payload: Dict[str, Any]) -> str:
    overall = payload.get("overall") or {}
    settings = payload.get("settings") or {}
    runtime = payload.get("runtime") or {}
    agent_names = payload.get("expected_agent_names") or []
    lines = [
        "# Ops Agents Status",
        "",
        f"- generated_at: `{payload.get('generated_at', '-')}`",
        f"- base_url: `{payload.get('base_url', '-')}`",
        f"- agent_count: `{payload.get('agent_count', '-')}`",
        f"- overall_status: `{overall.get('status', '-')}`",
        f"- duration_ms: `{overall.get('duration_ms', '-')}`",
        "",
        "## Settings",
        f"- auto_repair: `{settings.get('auto_repair')}`",
        f"- auto_evolve: `{settings.get('auto_evolve')}`",
        f"- min_evolve_samples: `{settings.get('min_evolve_samples')}`",
        f"- timeout_seconds: `{settings.get('timeout_seconds')}`",
        "",
        "## Runtime",
        f"- cycle: `{runtime.get('cycle')}`",
        f"- interval_seconds: `{runtime.get('interval_seconds')}`",
        f"- launcher: `{runtime.get('launcher')}`",
        f"- pid: `{runtime.get('pid')}`",
        "",
        "## Agents",
    ]

    agents = payload.get("agents") or {}
    for name in agent_names:
        row = agents.get(name) or {}
        lines.append(
            f"- `{name}`: status={row.get('status', '-')}, duration_ms={row.get('duration_ms', '-')}"
        )
        for rec in (row.get("recommendations") or [])[:3]:
            lines.append(f"  - {rec}")

    lines.append("")
    lines.append("## Recommendations")
    for item in payload.get("recommendations") or []:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def write_outputs(payload: Dict[str, Any], *, output_json: Path, output_md: Path) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    output_md.write_text(to_markdown(payload), encoding="utf-8")


def main() -> int:
    from app.engine.ops_agents import run_ops_agents_cycle

    parser = argparse.ArgumentParser(description="Run multi-agent ops cycle for system self-heal.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--auto-repair", type=int, default=1, choices=[0, 1])
    parser.add_argument("--auto-evolve", type=int, default=1, choices=[0, 1])
    parser.add_argument("--min-evolve-samples", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=float, default=8.0)
    parser.add_argument("--max-workers", type=int, default=3)
    parser.add_argument("--interval-seconds", type=float, default=0.0)
    parser.add_argument("--max-cycles", type=int, default=1)
    parser.add_argument(
        "--strict", action="store_true", help="Exit non-zero when overall status is fail."
    )
    parser.add_argument(
        "--output-json",
        default=str(ROOT / "build" / "ops_agents_status.json"),
    )
    parser.add_argument(
        "--output-md",
        default=str(ROOT / "build" / "ops_agents_status.md"),
    )
    args = parser.parse_args()

    output_json = Path(args.output_json)
    output_md = Path(args.output_md)
    cycles = 0
    while True:
        cycles += 1
        payload = run_ops_agents_cycle(
            base_url=args.base_url,
            api_key=(args.api_key or None),
            auto_repair=bool(args.auto_repair),
            auto_evolve=bool(args.auto_evolve),
            min_evolve_samples=max(1, int(args.min_evolve_samples)),
            timeout=max(2.0, float(args.timeout_seconds)),
            max_workers=max(1, int(args.max_workers)),
        )
        payload["runtime"] = {
            "cycle": cycles,
            "interval_seconds": float(args.interval_seconds),
            "max_cycles": int(args.max_cycles),
            "pid": os.getpid(),
            "launcher": os.environ.get("OPS_AGENTS_LAUNCHER", "direct"),
        }
        write_outputs(payload, output_json=output_json, output_md=output_md)
        overall_status = str((payload.get("overall") or {}).get("status") or "fail")
        print(
            f"[ops_agents] cycle={cycles} overall={overall_status} "
            f"json={output_json} md={output_md}",
            flush=True,
        )
        if args.strict and overall_status == "fail":
            return 1
        if args.interval_seconds <= 0:
            return 0
        if args.max_cycles > 0 and cycles >= args.max_cycles:
            return 0
        time.sleep(max(1.0, float(args.interval_seconds)))


if __name__ == "__main__":
    raise SystemExit(main())
