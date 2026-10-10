from types import SimpleNamespace
import uuid
import pytest

from app.database.database import Base, SessionLocal, engine
from app.ingestion.pdf_extractor import ExtractedDoc, ExtractedPage
from app.models.entities import OwnerSession, Paper, PaperSection, Chunk
from app.services import pipeline


def test_pipeline_keeps_full_section_and_each_passages_source_page(monkeypatch, tmp_path):
    first = ("The first page describes public infrastructure inspection. " * 230).strip()
    second = ("The second page explains repair priorities and funding constraints. " * 230).strip()
    doc = ExtractedDoc(pages=[ExtractedPage(3, first), ExtractedPage(4, second)],
                       title="Infrastructure review")
    section = SimpleNamespace(name="findings", position=0, page_start=3,
                              text=first + "\n" + second,
                              page_texts=[(3, first), (4, second)])
    monkeypatch.setattr(pipeline, "extract_pdf", lambda *_args, **_kwargs: doc)
    monkeypatch.setattr(pipeline, "build_sections", lambda _doc: [section])
    from app.services import scoped_index
    invalidated = []
    monkeypatch.setattr(scoped_index, "forget_owner", lambda owner: invalidated.append(owner))
    from app.security import session
    monkeypatch.setattr(session, "research_lookup_allowed", lambda _owner: False)
    file = tmp_path / "source.pdf"
    file.write_bytes(b"%PDF-test")
    owner, pid = uuid.uuid4().hex, uuid.uuid4().hex
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        db.add(OwnerSession(owner=owner))
        db.add(Paper(id=pid, owner=owner, title="Report", filename="source.pdf",
                     file_path=str(file), status="queued"))
        db.commit()
    try:
        pipeline.process_paper(pid)
        assert invalidated == [owner]
        with SessionLocal() as db:
            paper = db.get(Paper, pid)
            assert paper.status == "ready", paper.error
            saved = db.query(PaperSection).filter(PaperSection.paper_id == pid).one()
            assert saved.text == section.text
            pages = {row.page for row in db.query(Chunk).filter(Chunk.paper_id == pid).all()}
            assert pages == {3, 4}
    finally:
        with SessionLocal() as db:
            paper = db.get(Paper, pid)
            if paper:
                db.delete(paper)
            db.query(OwnerSession).filter(OwnerSession.owner == owner).delete()
            db.commit()


@pytest.mark.parametrize("setting", ["max_pdf_chunks", "max_session_chunks", "max_total_extracted_storage_mb"])
def test_pipeline_resource_rejection_never_inserts_partial_document(monkeypatch, tmp_path, setting):
    text = ("A source passage explains research observations and their context. " * 70).strip()
    section = SimpleNamespace(name="findings", position=0, page_start=1,
                              text=text, page_texts=[(1, text)])
    document = ExtractedDoc(pages=[ExtractedPage(1, text)], title="Bounded analysis")
    monkeypatch.setattr(pipeline, "extract_pdf", lambda *_args, **_kwargs: document)
    monkeypatch.setattr(pipeline, "build_sections", lambda _doc: [section])
    monkeypatch.setattr(pipeline.settings, setting, 0 if setting == "max_total_extracted_storage_mb" else 1)
    file = tmp_path / "source.pdf"
    file.write_bytes(b"%PDF-test")
    owner, paper_id = uuid.uuid4().hex, uuid.uuid4().hex
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        db.add(OwnerSession(owner=owner))
        db.add(Paper(id=paper_id, owner=owner, title="Bounded analysis", filename="source.pdf",
                     file_path=str(file), status="queued"))
        db.commit()
    try:
        pipeline.process_paper(paper_id)
        with SessionLocal() as db:
            paper = db.get(Paper, paper_id)
            assert paper.status == "failed"
            assert "limit" in paper.error or "capacity" in paper.error
            assert db.query(PaperSection).filter(PaperSection.paper_id == paper_id).count() == 0
            assert db.query(Chunk).filter(Chunk.paper_id == paper_id).count() == 0
    finally:
        with SessionLocal() as db:
            db.delete(db.get(Paper, paper_id))
            db.query(OwnerSession).filter(OwnerSession.owner == owner).delete()
            db.commit()


def test_pending_storage_cost_matches_real_unicode_ciphertext():
    from app.security.owner import server_encrypt
    for text in ("", "abc", "Research ∑ evidence नमस्ते"):
        assert pipeline._encrypted_text_bytes(text) == len(server_encrypt(text))
