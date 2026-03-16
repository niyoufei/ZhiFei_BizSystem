#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    from app.runtime_logs import rotate_runtime_file

    parser = argparse.ArgumentParser(
        description="Rotate runtime log/status files into build/log_archive."
    )
    parser.add_argument(
        "paths", nargs="+", help="One or more runtime files to archive before restart."
    )
    parser.add_argument(
        "--keep", type=int, default=12, help="How many archives to keep per file stem."
    )
    args = parser.parse_args()

    payload = [rotate_runtime_file(path, keep=max(1, int(args.keep))) for path in args.paths]
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
