from __future__ import annotations

from importlib import import_module
from typing import Any

_RUNTIME = import_module("app.application.runtime")

app = _RUNTIME.app
create_app = _RUNTIME.create_app


def __getattr__(name: str) -> Any:
    return getattr(_RUNTIME, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_RUNTIME)))
