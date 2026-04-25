from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Sequence

from app.runtime_security import is_secure_desktop_mode_enabled


@dataclass(frozen=True)
class ServerBinding:
    host: str
    port: int
    open_browser: bool


def resolve_server_binding(argv: Sequence[str] | None = None) -> ServerBinding:
    args = list(argv or [])
    port = int(os.environ.get("PORT", "8000"))
    host = (
        "127.0.0.1"
        if is_secure_desktop_mode_enabled()
        else str(os.environ.get("HOST") or "0.0.0.0")
    )
    return ServerBinding(
        host=host,
        port=port,
        open_browser="--no-browser" not in args,
    )
