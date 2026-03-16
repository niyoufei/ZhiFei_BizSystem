#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_env_api_keys(root: Path) -> str:
    env_value = os.environ.get("API_KEYS", "")
    if env_value.strip():
        return env_value

    env_path = root / ".env"
    if not env_path.exists():
        return ""
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("API_KEYS="):
            return stripped.split("=", 1)[1].strip()
    return ""


def main() -> int:
    from app.auth import DEFAULT_API_KEY_ROLE, resolve_api_key_for_role

    parser = argparse.ArgumentParser(description="Resolve API key by preferred role.")
    parser.add_argument(
        "--preferred-role",
        default=DEFAULT_API_KEY_ROLE,
        choices=["admin", "ops", "readonly"],
    )
    parser.add_argument(
        "--fallback-role",
        action="append",
        default=[],
        choices=["admin", "ops", "readonly"],
    )
    args = parser.parse_args()

    key = resolve_api_key_for_role(
        args.preferred_role,
        api_keys_value=_load_env_api_keys(ROOT),
        fallback_roles=tuple(args.fallback_role),
    )
    if key:
        sys.stdout.write(key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
