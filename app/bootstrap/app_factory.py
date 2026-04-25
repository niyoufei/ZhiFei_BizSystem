from __future__ import annotations

from fastapi import FastAPI

from app.interfaces.api.app import create_fastapi_app as _create_fastapi_app


def create_fastapi_app() -> FastAPI:
    return _create_fastapi_app()
