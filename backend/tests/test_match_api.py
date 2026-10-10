"""The cautious match decision must reach the dossier UI."""
from __future__ import annotations

import secrets

from fastapi.testclient import TestClient

from app.database.database import SessionLocal
from app.main import app
from app.models.entities import Paper
from app.security.owner import owner_hash


def test_weak_match_is_exposed_without_attaching_external_record():
    token = "ss_" + secrets.token_urlsafe(20)
    owner = owner_hash(token)
    paper_id = secrets.token_hex(6)
    with SessionLocal() as db:
        db.add(Paper(id=paper_id, owner=owner, status="ready", title="Policy memo",
                     filename="memo.pdf", meta={"match_confidence": "weak",
                                                "match_status": "rejected",
                                                "match_source": "openalex_title"}))
        db.commit()
    try:
        response = TestClient(app, headers={"X-Owner-Token": token}).get(
            f"/api/papers/{paper_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["match_confidence"] == "weak"
        assert body["openalex_id"] is None
    finally:
        TestClient(app, headers={"X-Owner-Token": token}).delete("/api/me/purge")
