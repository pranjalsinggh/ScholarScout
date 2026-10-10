"""The built React app must survive direct navigation to client routes."""
from __future__ import annotations

import json
import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_spa_fallback_serves_index_for_client_route(tmp_path):
    from app.main import SPAStaticFiles

    (tmp_path / "index.html").write_text("<html>ScholarScout</html>", encoding="utf-8")
    app = FastAPI()
    app.mount("/", SPAStaticFiles(directory=str(tmp_path), html=True))
    with TestClient(app) as client:
        assert "ScholarScout" in client.get("/library").text
        assert client.get("/missing.js").status_code == 404


def test_api_request_log_has_route_status_and_no_bearer_token(caplog):
    from app.main import app

    token = "ss_please_do_not_log_this_token"
    with caplog.at_level(logging.INFO, logger="scholarscout.requests"):
        response = TestClient(app).get("/api/me/privacy", headers={"X-Owner-Token": token})
    assert response.status_code == 200
    records = [json.loads(r.message) for r in caplog.records
               if r.name == "scholarscout.requests" and r.message.startswith("{")]
    assert any(r.get("route") == "/api/me/privacy" and r.get("status") == 200
               and r.get("owner_prefix") for r in records)
    assert token not in " ".join(r.message for r in caplog.records)


def test_rejected_request_still_gets_security_headers_and_log(caplog):
    from app.main import app

    with caplog.at_level(logging.INFO, logger="scholarscout.requests"):
        response = TestClient(app).get("/api/papers")
    assert response.status_code == 401
    assert response.headers["X-Frame-Options"] == "DENY"
    assert any('"route": "/api/papers"' in r.message
               for r in caplog.records if r.name == "scholarscout.requests")


def test_unexpected_failure_logs_do_not_expose_private_exception_text(caplog):
    from app.database.database import get_db
    from app.main import app
    private_text = "private manuscript and secret bearer must never be logged"
    def fail():
        raise RuntimeError(private_text)
        yield  # make this a dependency generator
    app.dependency_overrides[get_db] = fail
    try:
        with caplog.at_level(logging.ERROR):
            response = TestClient(app).get("/api/papers", headers={"X-Owner-Token": "ss_redacted_log_fixture"})
        assert response.status_code == 500
        assert private_text not in response.text
        assert private_text not in " ".join(record.message for record in caplog.records)
        assert all(record.exc_info is None for record in caplog.records)
    finally:
        app.dependency_overrides.pop(get_db, None)
