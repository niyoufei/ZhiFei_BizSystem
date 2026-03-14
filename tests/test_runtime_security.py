from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_build_fastapi_runtime_kwargs_disables_docs_in_production(monkeypatch):
    from app.runtime_security import build_fastapi_runtime_kwargs

    monkeypatch.setenv("ZHIFEI_PRODUCTION_MODE", "1")
    monkeypatch.delenv("ZHIFEI_ENABLE_API_DOCS", raising=False)

    kwargs = build_fastapi_runtime_kwargs()

    assert kwargs["docs_url"] is None
    assert kwargs["redoc_url"] is None
    assert kwargs["openapi_url"] is None


def test_validate_runtime_security_settings_requires_api_keys_in_production(monkeypatch):
    from app.runtime_security import validate_runtime_security_settings

    monkeypatch.setenv("ZHIFEI_PRODUCTION_MODE", "1")
    monkeypatch.delenv("API_KEYS", raising=False)
    monkeypatch.delenv("ZHIFEI_REQUIRE_API_KEYS", raising=False)

    try:
        validate_runtime_security_settings()
    except RuntimeError as exc:
        assert "requires_api_keys" in str(exc)
    else:
        raise AssertionError("expected RuntimeError when API_KEYS is missing in production mode")


def test_validate_runtime_security_settings_accepts_configured_api_keys(monkeypatch):
    from app.runtime_security import validate_runtime_security_settings

    monkeypatch.setenv("ZHIFEI_PRODUCTION_MODE", "1")
    monkeypatch.setenv("API_KEYS", "secret-key")

    validate_runtime_security_settings()


def test_configure_runtime_security_blocks_untrusted_hosts(monkeypatch):
    from app.runtime_security import configure_runtime_security

    monkeypatch.delenv("ZHIFEI_MAX_UPLOAD_MB", raising=False)
    monkeypatch.setenv("ZHIFEI_ALLOWED_HOSTS", "testserver,localhost,127.0.0.1")

    app = FastAPI()

    @app.get("/ping")
    def ping():
        return {"ok": True}

    configure_runtime_security(app)

    client = TestClient(app)

    ok_resp = client.get("/ping")
    blocked_resp = client.get("/ping", headers={"host": "evil.example.com"})

    assert ok_resp.status_code == 200
    assert blocked_resp.status_code == 400


def test_configure_runtime_security_rejects_large_requests(monkeypatch):
    from app.runtime_security import configure_runtime_security

    monkeypatch.delenv("ZHIFEI_ALLOWED_HOSTS", raising=False)
    monkeypatch.setenv("ZHIFEI_MAX_UPLOAD_MB", "0.001")

    app = FastAPI()

    @app.post("/echo")
    async def echo():
        return {"ok": True}

    configure_runtime_security(app)

    client = TestClient(app)

    response = client.post(
        "/echo",
        content=("x" * 4096).encode("utf-8"),
        headers={"content-type": "text/plain"},
    )

    assert response.status_code == 413
    assert "请求体过大" in response.json()["detail"]
