from __future__ import annotations

from fastapi import FastAPI


def create_fastapi_app() -> FastAPI:
    from app.application import runtime

    return runtime.create_app()
