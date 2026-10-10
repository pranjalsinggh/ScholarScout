"""Backend tests — run from backend/ with:
    .venv/Scripts/python.exe -m pytest tests/ -v

Covers PDF extraction, sectionizing, metadata parsing, chunking, retrieval,
chat answering, and API endpoints (offline; no external calls).
"""
from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# use a throwaway DB for the test run
os.environ.setdefault("DATABASE_URL", "sqlite:///./data/test_scholarscout.db")

from app.database.database import Base, engine, SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.ingestion.pdf_extractor import extract_pdf, _guess_title  # noqa: E402
from app.ingestion.sectionizer import build_sections  # noqa: E402
from app.ingestion import metadata as meta  # noqa: E402
from app.services.chunker import chunk_text  # noqa: E402
from app.services.pipeline import _clean_abstract, _resolve_year  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _setup_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    SessionLocal().close()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        c.headers.update({"X-Owner-Token": "ss_test-owner-token"})
        yield c


# ------------------------------------------------------------------ units --

def test_chunker_respects_size_and_overlap():
    text = ". ".join(f"Sentence number {i} talks about transformers and attention." for i in range(60))
    chunks = chunk_text(text, size=400, overlap=60)
    assert 2 <= len(chunks) <= 40
    assert all(len(c) <= 900 for c in chunks)
    assert all(len(c) > 40 for c in chunks)


def test_chunker_empty():
    assert chunk_text("") == []
    assert chunk_text("short") == ["short"]


def test_abstract_discards_author_footnote():
    raw = "A grounded finding.\nAnother result.\n∗Equal contribution.\nAlice built the model."
    assert _clean_abstract(raw) == "A grounded finding. Another result."


def test_arxiv_year_overrides_benchmark_year():
    assert _resolve_year(2014, "1706.03762") == 2017


def test_metadata_doi_extraction():
    class FakeDoc:
        pages = []
        full_text = ""

    class P:
        text = "Available at https://doi.org/10.1234/abc.def.55"

    doc = FakeDoc()
    doc.pages = [P()]
    assert meta.extract_doi(doc) == "10.1234/abc.def.55"


def test_metadata_arxiv_extraction():
    class FakeDoc:
        pages = []
        full_text = ""

    class P:
        text = "arXiv:1706.03762v7 [cs.CL] 2 Aug 2023"

    doc = FakeDoc()
    doc.pages = [P()]
    assert meta.extract_arxiv_id(doc) == "1706.03762"


def test_metadata_year_most_common():
    class FakeDoc:
        pages = []
        full_text = ""

    class P:
        text = "2017 was a year. In 2017 NIPS happened, not 2014 or 2023."

    doc = FakeDoc()
    doc.pages = [P()]
    assert meta.extract_year(doc, []) == 2017


def test_funding_extract_not_crash():
    class FakeDoc:
        pages = []
        full_text = "This work was supported by the NSF grant 2200123. We thank reviewers."

    doc = FakeDoc()
    out = meta.extract_funding(doc, [])
    assert isinstance(out, list)


# ------------------------------------------------------------- pipeline ----

@pytest.fixture(scope="module")
def real_pdf_path():
    """Reuse the attention paper if it was downloaded, else skip extraction tests."""
    import glob
    candidates = glob.glob(os.path.join(os.environ.get("TEMP", "/tmp"), "attention.pdf"))
    uploads = glob.glob(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                     "data", "uploads", "*attention.pdf"))
    path = (candidates + uploads)
    if not path:
        from pathlib import Path
        bundled = Path(__file__).resolve().parents[1] / "demo_assets" / "attention-is-all-you-need.pdf"
        if bundled.exists():
            path = [str(bundled)]
    if not path:
        pytest.skip("real arXiv PDF not available for integration test")
    return path[0]


def test_pdf_extraction_title(real_pdf_path):
    doc = extract_pdf(real_pdf_path)
    assert doc.page_count > 5
    assert doc.has_text_layer
    assert "Attention" in (doc.title or "") or len(doc.title) > 10


def test_pdf_sections(real_pdf_path):
    doc = extract_pdf(real_pdf_path)
    sections = build_sections(doc)
    names = {s.name for s in sections}
    assert "abstract" in names or "introduction" in names


def test_pdf_metadata_methods(real_pdf_path):
    doc = extract_pdf(real_pdf_path)
    sections = build_sections(doc)
    methods = meta.extract_methods(doc, sections)
    assert "transformer" in [m.lower() for m in methods]


# ------------------------------------------------------------------ api ----

def test_health_endpoint(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_upload_rejects_non_pdf(client):
    r = client.post("/api/papers/upload", files={"files": ("x.txt", b"not a pdf", "text/plain")})
    assert r.status_code == 400
    assert "PDF" in r.json()["detail"]


def test_upload_rejects_fake_pdf(client):
    r = client.post("/api/papers/upload", files={"files": ("fake.pdf", b"%PDF-broken", "application/pdf")})
    # Isolated fingerprint parsing rejects an unreadable PDF before storage.
    assert r.status_code == 422
    assert "detail" in r.json()


def test_search_requires_query(client):
    r = client.post("/api/search", json={"query": "a"})
    assert r.status_code == 422


def test_chat_nonexistent_paper(client):
    r = client.post("/api/chat", json={"paper_id": "nope", "message": "hello there"})
    assert r.status_code == 404


def test_friendly_error_format(client):
    r = client.get("/api/papers/does-not-exist")
    assert r.status_code == 404
    assert "detail" in r.json()
