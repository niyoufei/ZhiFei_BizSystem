from __future__ import annotations

import sys
from importlib import import_module

sys.modules[__name__] = import_module("app.contracts.legacy")
