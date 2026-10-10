"""Paper processing pipeline: PDF → sections → metadata → chunks → embeddings →
vector store → scholarly ecosystem enrichment. Runs as a background task and
updates Paper.stages so the frontend can show live progress."""
from __future__ import annotations

import re
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.database.database import SessionLocal
from app.ingestion import metadata as meta
from app.ingestion.pdf_extractor import extract_pdf, looks_like_scanned
from app.ingestion.sectionizer import build_sections
from app.models.entities import Chunk, Paper, PaperSection
from app.services.chunker import chunk_text

STAGES = [
    ("extract", "Extracting paper"),
    ("metadata", "Identifying metadata"),
    ("sections", "Detecting structure"),
    ("embedding", "Generating embeddings"),
    ("ecosystem", "Searching research ecosystem"),
    ("network", "Building research network"),
    ("status", "Checking publication status"),
]

log = logging.getLogger("scholarscout.pipeline")


def _require_live_session(db: Session, paper: Paper) -> None:
    # An exit purge may race a queued background job. Stop before adding more
    # derived data or calling a remote research provider.
    from app.security.session import live_owner
    live_owner(db, paper.owner)


def _init_stages(paper: Paper) -> None:
    paper.stages = [{"key": k, "label": l, "state": "pending"} for k, l in STAGES]


def _set_stage(paper: Paper, key: str, state: str, detail: str | None = None) -> None:
    # rebuild the list/dicts so SQLAlchemy sees a changed JSON value
    stages = [dict(s) for s in (paper.stages or [])]
    for s in stages:
        if s["key"] == key:
            s["state"] = state
            if detail:
                s["detail"] = detail
    paper.stages = stages


ABSTRACT_JUNK = re.compile(
    r"arXiv:\d{4}\.\d{4,5}|\d+(st|nd|rd|th) (Conference|Workshop|Annual)|"
    r"Proceedings of the|Published as a conference paper|Long Beach, CA, USA|"
    r"^Provided proper attribution|Equal contribution", re.I)
ABSTRACT_FOOTNOTE_START = re.compile(
    r"(?:[\u2217*\u2020\u2021]\s*Equal contribution|Listing order is random|"
    r"Work performed while at|Corresponding author:)", re.I)


def _clean_abstract(text: str) -> str:
    """Strip arXiv sidebanner / proceedings footer lines from extracted abstracts."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    keep = []
    for line in lines:
        if ABSTRACT_FOOTNOTE_START.search(line):
            break
        if not ABSTRACT_JUNK.search(line):
            keep.append(line)
    return " ".join(keep).strip()[:4000]


def _resolve_year(extracted: int | None, arxiv_id: str | None) -> int | None:
    from app.services.scholarly.aggregator import _arxiv_year
    arxiv_year = _arxiv_year(arxiv_id)
    if arxiv_year and (not extracted or abs(extracted - arxiv_year) > 1):
        return arxiv_year
    return extracted


def _section_chunks(section):
    """Keep each passage attached to the PDF page its text came from."""
    page_texts = getattr(section, "page_texts", None) or [(section.page_start, section.text)]
    for page, text in page_texts:
        for piece in chunk_text(text, settings.chunk_size, settings.chunk_overlap):
            yield page, piece


def _write_paper(db: Session, paper_id: str, owner: str, change):
    """Every derived write validates the live owner under the erasure lock."""
    from app.security.session import SessionEnded, owner_write
    with owner_write(db, owner):
        paper = db.get(Paper, paper_id)
        if paper is None or paper.owner != owner:
            raise SessionEnded("This research session has ended.")
        change(paper)
    return paper


def _stage(db, paper_id, owner, key, state, detail=None):
    return _write_paper(db, paper_id, owner,
                        lambda paper: _set_stage(paper, key, state, detail))


def _bounded_passages(sections, tables):
    """Build bounded candidates before any chunks are written."""
    passages = []
    has_content = any(s.name not in ("front matter", "references", "acknowledgements")
                      for s in sections)
    for section in sections:
        if section.name in ("references", "acknowledgements"):
            continue
        if section.name == "front matter" and has_content:
            continue
        for page, piece in _section_chunks(section):
            passages.append((section.name, page, f"[{section.name}] {piece}"))
            if len(passages) > settings.max_pdf_chunks:
                raise ValueError("This PDF exceeds the passage limit. Split it into smaller documents.")
    for table in tables:
        for piece in chunk_text(table["text"], settings.chunk_size, settings.chunk_overlap):
            passages.append(("table", table.get("page"), f"[table] {piece}"))
            if len(passages) > settings.max_pdf_chunks:
                raise ValueError("This PDF exceeds the passage limit. Split it into smaller documents.")
    return passages


def _encrypted_text_bytes(text: str) -> int:
    """AES-GCM nonce/tag + padded base64 + srv:v1 prefix, matching storage."""
    return 0 if not text else 7 + 4 * ((len(text.encode("utf-8")) + 28 + 2) // 3)


def process_paper(paper_id: str, owner_token: str = "") -> None:
    """Bound native parsing and serialize derived writes against session erasure."""
    from app.security.session import SessionEnded, owner_write, research_lookup_allowed
    db = SessionLocal()
    owner = ""
    plain_path = None
    stored_path = None
    try:
        paper = db.get(Paper, paper_id)
        if paper is None:
            return
        owner = paper.owner
        def begin(current):
            current.status = "processing"
            _init_stages(current)
            _set_stage(current, "extract", "active")
        paper = _write_paper(db, paper_id, owner, begin)
        stored_path = paper.file_path
        plain_path = stored_path + ".plain" if stored_path.endswith(".enc") else stored_path
        try:
            if stored_path.endswith(".enc"):
                from app.security.owner import decrypt_paper_file
                # Creating plaintext is a short lifecycle transaction. Purge
                # cannot return while another process recreates this file.
                with owner_write(db, owner):
                    paper = db.get(Paper, paper_id)
                    if paper is None:
                        raise SessionEnded("This research session has ended.")
                    decrypt_paper_file(owner_token, paper.file_key_w1, paper.file_key_w2,
                                       stored_path, plain_path)
            doc = extract_pdf(plain_path, source_name=paper.filename, owner=owner)
        finally:
            if plain_path and plain_path != stored_path:
                Path(plain_path).unlink(missing_ok=True)
        if looks_like_scanned(doc):
            raise ValueError("We couldn't read text from this PDF - it looks scanned or image-only. "
                             "Try uploading a text-based (digital) PDF.")
        from app.ingestion.fingerprint import fingerprint_of_doc
        def extracted(current):
            current.page_count = doc.page_count
            current.title = doc.title or current.filename or "Untitled paper"
            current.dedup_key = current.dedup_key or fingerprint_of_doc(doc)
            _set_stage(current, "extract", "done", f"{doc.page_count} pages")
            _set_stage(current, "sections", "active")
        paper = _write_paper(db, paper_id, owner, extracted)
        sections = build_sections(doc)
        passages = _bounded_passages(sections, doc.tables)
        authors = meta.extract_authors(doc, sections)
        abstract = _clean_abstract(meta.get_section(sections, "abstract").text
                                   if meta.get_section(sections, "abstract") else "")
        extracted_meta = {
            "extraction_version": 3,
            "methods": meta.extract_methods(doc, sections),
            "datasets": meta.extract_datasets(doc, sections),
            "metrics": meta.extract_metrics(doc, sections),
            "figures": [c for c in doc.captions if c["kind"] == "figure"][:12],
            "tables": [c for c in doc.captions if c["kind"] == "table"][:12],
            "conflicts": meta.extract_conflict_statements(doc, sections),
            "equation_density": doc.equation_density,
        }
        doi, arxiv = meta.extract_doi(doc), meta.extract_arxiv_id(doc)
        year = meta.extract_year(doc, sections)
        keywords, funding = meta.extract_keywords(doc, sections), meta.extract_funding(doc, sections)
        def persist_document(current):
            # Quotas and all content rows share one transaction. A rejected
            # document never leaves a partially inserted section/chunk corpus.
            used = db.query(Chunk).join(Paper, Chunk.paper_id == Paper.id).filter(
                Paper.owner == owner, Paper.id != paper_id).count()
            if used + len(passages) > settings.max_session_chunks:
                raise ValueError("This session exceeds the passage limit. Remove a document or upload a shorter PDF.")
            from sqlalchemy import func
            # Uploaded byte quotas alone cannot bound highly compressed PDFs.
            # Count ciphertext already on disk and the exact pending field
            # encoding while holding the same global write/admission lock.
            section_bytes = db.query(func.coalesce(func.sum(func.length(PaperSection.text)), 0)).filter(
                PaperSection.paper_id != paper_id).scalar()
            chunk_bytes = db.query(func.coalesce(func.sum(
                func.coalesce(func.length(Chunk.text), 0) +
                func.coalesce(func.length(Chunk.section_name), 0)), 0)).filter(
                    Chunk.paper_id != paper_id).scalar()
            added_bytes = sum(_encrypted_text_bytes(section.text) for section in sections)
            added_bytes += sum(_encrypted_text_bytes(text) + _encrypted_text_bytes(section)
                               for section, _, text in passages)
            if section_bytes + chunk_bytes + added_bytes > settings.max_total_extracted_storage_mb * 1024 * 1024:
                raise ValueError("The service is at its extracted document storage capacity. Please retry later.")
            db.query(Chunk).filter(Chunk.paper_id == paper_id).delete()
            db.query(PaperSection).filter(PaperSection.paper_id == paper_id).delete()
            for section in sections:
                db.add(PaperSection(paper_id=paper_id, name=section.name, position=section.position,
                                    page_start=section.page_start, text=section.text))
            for section, page, text in passages:
                db.add(Chunk(paper_id=paper_id, section_name=section, page=page, text=text))
            current.abstract, current.authors, current.doi = abstract, authors, doi
            current.arxiv_id = arxiv or (current.dedup_key.removeprefix("arxiv:")
                                         if (current.dedup_key or "").startswith("arxiv:") else None)
            current.year = _resolve_year(year, current.arxiv_id)
            current.keywords, current.funding, current.meta = keywords, funding, extracted_meta
            _set_stage(current, "sections", "done", f"{len(sections)} sections")
            _set_stage(current, "metadata", "done", f"{len(authors)} authors")
            _set_stage(current, "embedding", "active")
        paper = _write_paper(db, paper_id, owner, persist_document)
        # Owner-scoped retrieval builds lazily; no global corpus is re-encoded
        # because another visitor uploaded a document.
        from app.services.scoped_index import forget_owner
        forget_owner(owner)
        _stage(db, paper_id, owner, "embedding", "done", f"{len(passages)} passages")
        paper = db.get(Paper, paper_id)
        if research_lookup_allowed(owner):
            paper = _stage(db, paper_id, owner, "ecosystem", "active")
            try:
                from app.services.scholarly.aggregator import enrich_ecosystem
                enrich_ecosystem(db, paper)
                _stage(db, paper_id, owner, "ecosystem", "done")
            except SessionEnded:
                raise
            except Exception:
                db.rollback()
                def offline(current):
                    current.ecosystem_ok = False
                    current.ecosystem_note = "Connected research providers could not be reached. The document remains available."
                    _set_stage(current, "ecosystem", "skipped", "external databases unavailable")
                _write_paper(db, paper_id, owner, offline)
        else:
            def private(current):
                current.ecosystem_ok = False
                current.ecosystem_note = "Connected research lookups are off for this session."
                _set_stage(current, "ecosystem", "skipped", "private analysis mode")
            _write_paper(db, paper_id, owner, private)
        paper = db.get(Paper, paper_id)
        try:
            from app.services.analysis import timeline
            timeline.build_timeline(db, paper)
            state = "done"
        except SessionEnded:
            raise
        except Exception:
            db.rollback()
            state = "skipped"
        def ready(current):
            _set_stage(current, "network", state)
            _set_stage(current, "status", state)
            current.status = "ready"
        _write_paper(db, paper_id, owner, ready)
    except Exception as error:
        db.rollback()
        if plain_path and plain_path != stored_path:
            Path(plain_path).unlink(missing_ok=True)
        if owner:
            try:
                message = (str(error)[:300] if isinstance(error, ValueError) else
                           "We couldn't process this PDF. It may be scanned, corrupted, or in an "
                           "unsupported format - please try another file.")
                def failed(current):
                    current.status = "failed"
                    current.error = message
                    stages = [dict(stage) for stage in (current.stages or [])]
                    for stage in stages:
                        if stage["state"] == "active":
                            stage.update(state="failed", detail=None)
                    current.stages = stages
                _write_paper(db, paper_id, owner, failed)
            except SessionEnded:
                pass
            except Exception:
                log.warning("Could not persist analysis failure state")
        log.warning("Paper analysis failed (%s)", type(error).__name__)
    finally:
        db.close()
