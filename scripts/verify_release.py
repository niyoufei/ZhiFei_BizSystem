from __future__ import annotations

import argparse

from app.version import RELEASE_TAG, RELEASE_VERSION, __version__


def verify_release_tag(tag: str) -> dict[str, str]:
    if tag != RELEASE_TAG:
        raise ValueError(f"release tag mismatch: expected={RELEASE_TAG} actual={tag}")
    return {
        "package_version": __version__,
        "release_version": RELEASE_VERSION,
        "release_tag": RELEASE_TAG,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify QingTian release metadata")
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()
    report = verify_release_tag(args.tag)
    for key, value in report.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
