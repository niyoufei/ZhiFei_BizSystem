from __future__ import annotations

import sys
from importlib import import_module


def _runtime_module():
    return import_module("app.interfaces.cli.runtime")


if __name__ == "__main__":
    from app.bootstrap.entrypoints import run_cli

    run_cli()
else:
    sys.modules[__name__] = _runtime_module()
