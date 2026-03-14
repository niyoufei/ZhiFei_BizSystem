from __future__ import annotations

import logging
from unittest.mock import MagicMock

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient


def test_configure_observability_injects_request_id_header():
    from app.observability import REQUEST_ID_HEADER, configure_observability

    app = FastAPI()
    configure_observability(app, logging.getLogger("test-observability"))

    @app.get("/ping")
    def ping(request: Request) -> dict:
        return {"request_id": request.state.request_id}

    client = TestClient(app)
    response = client.get("/ping")

    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER]
    assert response.json()["request_id"] == response.headers[REQUEST_ID_HEADER]


def test_configure_observability_preserves_incoming_request_id():
    from app.observability import REQUEST_ID_HEADER, configure_observability

    app = FastAPI()
    configure_observability(app, logging.getLogger("test-observability"))

    @app.get("/ping")
    def ping(request: Request) -> dict:
        return {"request_id": request.state.request_id}

    client = TestClient(app)
    response = client.get("/ping", headers={REQUEST_ID_HEADER: "req-123"})

    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER] == "req-123"
    assert response.json()["request_id"] == "req-123"


def test_configure_observability_logs_slow_requests(monkeypatch):
    from app.observability import configure_observability

    monkeypatch.setenv("ZHIFEI_SLOW_REQUEST_WARN_MS", "0")
    logger = MagicMock()
    app = FastAPI()
    configure_observability(app, logger)

    @app.get("/slow")
    def slow() -> dict:
        return {"ok": True}

    client = TestClient(app)
    response = client.get("/slow")

    assert response.status_code == 200
    logger.warning.assert_called_once()
    assert "slow_request" in logger.warning.call_args[0][0]


def test_configure_observability_records_metrics_with_route_template(monkeypatch):
    from app.observability import configure_observability

    recorded = []
    monkeypatch.setattr(
        "app.observability.record_request",
        lambda method, endpoint, status_code, duration: recorded.append(
            {
                "method": method,
                "endpoint": endpoint,
                "status_code": status_code,
                "duration": duration,
            }
        ),
    )
    app = FastAPI()
    configure_observability(app, logging.getLogger("test-observability"))

    @app.get("/items/{item_id}")
    def get_item(item_id: str, request: Request) -> dict:
        return {"item_id": item_id, "request_id": request.state.request_id}

    client = TestClient(app)
    response = client.get("/items/abc")

    assert response.status_code == 200
    assert len(recorded) == 1
    assert recorded[0]["method"] == "GET"
    assert recorded[0]["endpoint"] == "/items/{item_id}"
    assert recorded[0]["status_code"] == 200
    assert recorded[0]["duration"] >= 0


def test_configure_observability_records_failed_requests(monkeypatch):
    from app.observability import configure_observability

    recorded = []
    monkeypatch.setattr(
        "app.observability.record_request",
        lambda method, endpoint, status_code, duration: recorded.append(
            {
                "method": method,
                "endpoint": endpoint,
                "status_code": status_code,
                "duration": duration,
            }
        ),
    )
    app = FastAPI()
    configure_observability(app, logging.getLogger("test-observability"))

    @app.get("/boom")
    def boom() -> dict:
        raise RuntimeError("boom")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/boom")

    assert response.status_code == 500
    assert len(recorded) == 1
    assert recorded[0]["endpoint"] == "/boom"
    assert recorded[0]["status_code"] == 500
