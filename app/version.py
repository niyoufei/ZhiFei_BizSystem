from __future__ import annotations

import re

__version__ = "1.1.0rc2"


def _release_version(version: str) -> str:
    match = re.fullmatch(r"(\d+\.\d+\.\d+)rc(\d+)", version)
    if match is None:
        raise RuntimeError(f"unsupported release-candidate version: {version}")
    return f"{match.group(1)}-rc.{match.group(2)}"


RELEASE_VERSION = _release_version(__version__)
RELEASE_TAG = f"v{RELEASE_VERSION}"
