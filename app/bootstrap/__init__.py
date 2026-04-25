from __future__ import annotations

from typing import Any

__all__ = ["create_fastapi_app", "get_application_services"]


def __getattr__(name: str) -> Any:
    if name == "create_fastapi_app":
        from app.bootstrap.app_factory import create_fastapi_app

        return create_fastapi_app
    if name == "get_application_services":
        from app.bootstrap.dependencies import get_application_services

        return get_application_services
    raise AttributeError(name)
