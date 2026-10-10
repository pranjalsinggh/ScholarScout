"""Upload and PDF failures return actionable errors without leaking internals."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.ingestion import fingerprint
from app.main import app
from app.database.database import Base, SessionLocal, engine
from app.models.entities import OwnerSession, Paper
from app.security.owner import owner_hash
from app.security import session as privacy_session
from app.services import pipeline

TOKEN = "ss_fault_injection_token"


@pytest.fixture
def api_client(monkeypatch, tmp_path):
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    owner = owner_hash(TOKEN)
    db.add(OwnerSession(owner=owner))
    db.commit()
    app.dependency_overrides[routes.get_db] = lambda: db
    monkeypatch.setattr(privacy_session, "touch_session", lambda owner: None)
    monkeypatch.setattr(routes, "rate_limit", lambda *a, **kw: None)
    monkeypatch.setattr(routes, "UPLOAD_DIR", tmp_path)
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client, db, tmp_path
    finally:
        client.close()
        app.dependency_overrides.clear()
        db.rollback()
        for paper in db.query(Paper).filter(Paper.owner == owner).all():
            db.delete(paper)
        db.query(OwnerSession).filter(OwnerSession.owner == owner).delete()
        db.commit()
        db.close()


@pytest.mark.parametrize("filename,content,status,detail", [
    ("notes.txt", b"%PDF-1.4\n", 400, "PDF"),
    ("paper.pdf", b"not a PDF", 400, "valid PDF"),
])
def test_invalid_upload_is_actionable(api_client, filename, content, status, detail):
    client, _, _ = api_client
    response = client.post("/api/papers/upload", headers={"X-Owner-Token": TOKEN},
                           files={"files": (filename, content, "application/pdf")})
    assert response.status_code == status
    assert detail in response.json()["detail"]


def test_fingerprint_pdf_parser_failure_still_queues_upload(api_client, monkeypatch):
    client, db, temp_dir = api_client
    submitted = []
    monkeypatch.setattr(fingerprint, "extract_pdf", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        RuntimeError("parser failed while fingerprinting")))
    from app.services import jobs
    def submit(paper_id, token, **kwargs):
        submitted.append((paper_id, token))
        kwargs["permit"].release()  # the fake job completes immediately
        return True
    monkeypatch.setattr(jobs, "submit_analysis", submit)
    response = client.post("/api/papers/upload", headers={"X-Owner-Token": TOKEN},
                           files={"files": ("paper.pdf", b"%PDF-1.4\ncontents", "application/pdf")})
    assert response.status_code == 200, response.text
    assert response.json()["created"] and response.json()["created"][0]["status"] == "queued"
    paper_id = response.json()["created"][0]["id"]
    assert submitted == [(paper_id, TOKEN)]
    assert Path(db.get(Paper, paper_id).file_path).suffix == ".enc"
    assert not list(temp_dir.glob("parse-*"))


def test_upload_unexpected_exception_is_friendly(api_client, monkeypatch):
    client, _, temp_dir = api_client
    monkeypatch.setattr(fingerprint, "fingerprint_pdf", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        RuntimeError("C:/private/user/file.pdf internal failure")))
    response = client.post("/api/papers/upload", headers={"X-Owner-Token": TOKEN},
                           files={"files": ("paper.pdf", b"%PDF-1.4\ncontents", "application/pdf")})
    assert response.status_code == 500
    detail = response.json().get("detail", "")
    assert detail.strip() and "private/user" not in detail and "Traceback" not in detail
    assert not list(temp_dir.glob("*.plain"))


def test_database_dependency_failure_is_friendly_http_error(api_client):
    client, _, _ = api_client
    app.dependency_overrides[routes.get_db] = lambda: (_ for _ in ()).throw(
        RuntimeError("database password or private path"))
    response = client.post("/api/chat", headers={"X-Owner-Token": TOKEN},
                           json={"paper_id": "paper-a", "message": "What is it about?"})
    assert response.status_code == 500
    detail = response.json().get("detail", "")
    assert detail.strip() and "database password" not in detail


def test_background_pdf_parser_failure_sets_honest_status(monkeypatch, tmp_path):
    import uuid
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF-1.4\n")
    owner, paper_id = uuid.uuid4().hex, uuid.uuid4().hex
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        db.add(OwnerSession(owner=owner))
        db.add(Paper(id=paper_id, owner=owner, filename="paper.pdf", file_path=str(path), status="queued"))
        db.commit()
    monkeypatch.setattr(pipeline, "extract_pdf", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        RuntimeError("C:/private/user/secret.pdf parser traceback")))
    try:
        pipeline.process_paper(paper_id, TOKEN)
        with SessionLocal() as db:
            paper = db.get(Paper, paper_id)
            assert paper.status == "failed"
            assert paper.error.strip() and "private/user" not in paper.error
            assert "try another file" in paper.error.lower()
    finally:
        with SessionLocal() as db:
            db.delete(db.get(Paper, paper_id))
            db.query(OwnerSession).filter(OwnerSession.owner == owner).delete()
            db.commit()
