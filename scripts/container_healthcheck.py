from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict
from urllib.request import urlopen


def _load_json(url: str, opener: Callable[..., Any]) -> Dict[str, Any]:
    with opener(url, timeout=3) as response:
        if int(getattr(response, "status", 200)) != 200:
            raise RuntimeError(f"health endpoint returned HTTP {response.status}")
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("health endpoint did not return an object")
    return payload


def probe(base_url: str, *, opener: Callable[..., Any] = urlopen) -> Dict[str, object]:
    health = _load_json(f"{base_url}/health", opener)
    ready = _load_json(f"{base_url}/ready", opener)
    checks = ready.get("checks") if isinstance(ready.get("checks"), dict) else {}
    passed = (
        health.get("status") == "healthy"
        and ready.get("status") == "ready"
        and bool(checks)
        and all(bool(value) for value in checks.values())
    )
    return {
        "passed": passed,
        "liveness": health.get("status"),
        "readiness": ready.get("status"),
        "checks": checks,
    }


def main() -> int:
    port = os.environ.get("PORT", "8000")
    try:
        report = probe(f"http://127.0.0.1:{port}")
    except BaseException as exc:
        print(
            json.dumps(
                {"passed": False, "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            )
        )
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
