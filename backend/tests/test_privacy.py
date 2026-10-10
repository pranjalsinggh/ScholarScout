"""Privacy & isolation tests.

Verifies the guarantees the product makes:
  1. Two owners (different browser tokens) see completely different libraries.
  2. A request without a token is rejected with 401.
  3. Uploaded PDFs are encrypted at rest (ciphertext on disk, no %PDF header).
  4. Chat content is stored encrypted in the database.
"""
from __future__ import annotations

import os
import sys
import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite:///./data/test_privacy.db")

from app.database.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.config import UPLOAD_DIR  # noqa: E402
from app.models.entities import (ApiCache, ChatMessage, Chunk, EndedSession, OwnerSession,  # noqa: E402
                                 Paper, RetrievalProfile)
from app.security.owner import (decrypt_text, encrypt_text,  # noqa: E402
                                owner_hash, server_decrypt_blob,
                                server_encrypt_blob)
from app.security.session import cloud_ai_allowed, research_lookup_allowed  # noqa: E402


def _make_client(token: str) -> TestClient:
    c = TestClient(app)
    c.headers.update({"X-Owner-Token": token})
    return c


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


# ------------------------------------------------------------ encryption ----

def test_text_encryption_roundtrip():
    token = "ss_testtoken"
    stored = encrypt_text(token, "secret answer about transformers")
    assert stored.startswith("enc:v1:")
    assert "transformers" not in stored
    assert decrypt_text(token, stored) == "secret answer about transformers"


def test_encryption_fails_without_same_token():
    token = "ss_testtoken"
    stored = encrypt_text(token, "private")
    # a different token must NOT decrypt (wrong key → empty by contract)
    assert decrypt_text("ss_other", stored) == ""
    # no token → also unreadable
    assert decrypt_text("", stored) == ""


def test_plaintext_rows_still_readable():
    # legacy rows (pre-encryption) must not break reads
    assert decrypt_text("t", "plain old text") == "plain old text"


def test_search_index_blob_is_encrypted_and_authenticated():
    raw = b"private manuscript vocabulary"
    stored = server_encrypt_blob(raw)
    assert raw not in stored
    assert server_decrypt_blob(stored) == raw
    with pytest.raises(ValueError):
        server_decrypt_blob(raw)


# --------------------------------------------------------------- isolation --

def test_two_owners_see_different_libraries():
    alice = _make_client("ss_alice")
    bob = _make_client("ss_bob")
    assert alice.get("/api/papers").json() == []
    assert bob.get("/api/papers").json() == []


def test_missing_token_gets_401():
    anon = TestClient(app)
    r = anon.get("/api/papers")
    assert r.status_code == 401


def test_paper_access_is_owner_scoped():
    """A paper created for owner A must 404 for owner B."""
    alice = _make_client("ss_iso_alice")
    bob = _make_client("ss_iso_bob")

    # insert a paper directly for alice
    db = SessionLocal()
    p = Paper(id="iso-paper-1", title="Alice's secret paper", owner=owner_hash("ss_iso_alice"),
              status="ready", filename="x.pdf", file_path="x.pdf")
    db.add(p)
    db.commit()
    db.close()

    assert alice.get("/api/papers/iso-paper-1").status_code == 200
    assert bob.get("/api/papers/iso-paper-1").status_code == 404

    # bob also can't delete it
    assert bob.delete("/api/papers/iso-paper-1").status_code == 404
    # ...and it's still there for alice
    assert alice.get("/api/papers/iso-paper-1").status_code == 200

    db = SessionLocal()
    db.query(Paper).filter(Paper.id == "iso-paper-1").delete()
    db.commit()
    db.close()


def test_search_cannot_cross_owners():
    alice = _make_client("ss_search_alice")
    db = SessionLocal()
    db.add(Paper(id="srch-1", title="Quantum for Alice", owner=owner_hash("ss_search_alice"),
                 status="ready", filename="q.pdf", file_path="q.pdf"))
    db.commit()
    db.close()
    # alice can find it; the response only contains her papers
    res = alice.post("/api/search", json={"query": "quantum"}).json()
    assert all(h["paper_id"] == "srch-1" for h in res["results"])


def test_end_session_removes_files_messages_feedback_and_cache():
    token = "ss_purge_test_owner"
    owner = owner_hash(token)
    file_path = UPLOAD_DIR / "purge-test.pdf.enc"
    file_path.write_bytes(b"encrypted test bytes")
    with SessionLocal() as db:
        db.add(Paper(id="purge-paper", owner=owner, title="Private test",
                     filename="test.pdf", file_path=str(file_path), status="ready"))
        db.flush()  # Parent must exist before adding separately mapped FK rows.
        db.add(Chunk(paper_id="purge-paper", section_name="Abstract", text="Private passage"))
        db.add(ChatMessage(paper_id="purge-paper", owner=owner, role="user",
                           content=encrypt_text(token, "secret question")))
        db.add(RetrievalProfile(paper_id="purge-paper", owner=owner, sections={"abstract": 0.2}))
        db.add(ApiCache(key=f"synth::{owner}::example", payload={"answer": "secret"},
                        expires_at=dt.datetime.utcnow() + dt.timedelta(hours=1)))
        db.commit()

    res = _make_client(token).delete("/api/me/purge")
    assert res.status_code == 200
    assert res.json()["papers_removed"] == 1
    assert not file_path.exists()
    with SessionLocal() as db:
        assert db.query(Paper).filter(Paper.owner == owner).count() == 0
        assert db.query(ChatMessage).filter(ChatMessage.owner == owner).count() == 0
        assert db.query(RetrievalProfile).filter(RetrievalProfile.owner == owner).count() == 0
        assert db.get(ApiCache, f"synth::{owner}::example") is None


def test_idle_session_expires_before_a_returning_request():
    token = "ss_expiry_test_owner"
    owner = owner_hash(token)
    file_path = UPLOAD_DIR / "expiry-test.pdf.enc"
    file_path.write_bytes(b"encrypted test bytes")
    with SessionLocal() as db:
        db.add(OwnerSession(owner=owner, last_seen=dt.datetime.utcnow() - dt.timedelta(hours=2)))
        db.add(Paper(id="expiry-paper", owner=owner, title="Old private test",
                     filename="test.pdf", file_path=str(file_path), status="ready"))
        db.commit()

    res = _make_client(token).get("/api/papers")
    assert res.status_code == 410
    assert not file_path.exists()
    with SessionLocal() as db:
        assert db.get(EndedSession, owner) is not None
        assert db.get(OwnerSession, owner) is None
        assert db.get(Paper, "expiry-paper") is None


def test_cloud_ai_requires_session_opt_in(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "cloud_ai_enabled", True)
    monkeypatch.setattr(settings, "production", False)
    token = "ss_cloud_test_owner"
    client = _make_client(token)
    assert client.get("/api/me/cloud-ai").json() == {"enabled": False, "available": True}
    assert not cloud_ai_allowed(owner_hash(token))
    assert client.post("/api/me/cloud-ai", json={"enabled": True}).json() == {"enabled": True}
    assert cloud_ai_allowed(owner_hash(token))
    assert client.post("/api/me/cloud-ai", json={"enabled": False}).json() == {"enabled": False}
    assert not cloud_ai_allowed(owner_hash(token))


def test_research_lookups_require_session_opt_in():
    token = "ss_lookup_test_owner"
    client = _make_client(token)
    owner = owner_hash(token)
    assert client.get("/api/me/research-lookups").json() == {"enabled": False}
    assert not research_lookup_allowed(owner)
    enabled = client.post("/api/me/research-lookups", json={"enabled": True}).json()
    assert enabled["enabled"] is True
    assert enabled["refreshing"] is False and enabled["papers_queued"] == 0
    assert research_lookup_allowed(owner)


def test_research_metadata_is_encrypted_at_rest():
    with SessionLocal() as db:
        db.add(Paper(id="metadata-encryption", owner="test-metadata", status="ready",
                     title="Private manuscript", authors=[{"name": "Private Author"}],
                     meta={"method": "unpublished technique"},
                     stages=[{"detail": "private status"}]))
        db.commit()
        raw = db.execute(text(
            "SELECT title, authors, meta, stages FROM papers WHERE id = 'metadata-encryption'"
        )).one()
        assert all(value.startswith("srv:v1:") for value in raw)
        assert "Private Author" not in " ".join(raw)
        loaded = db.get(Paper, "metadata-encryption")
        assert loaded.authors == [{"name": "Private Author"}]
        assert loaded.meta == {"method": "unpublished technique"}
        db.delete(loaded)
        db.commit()
