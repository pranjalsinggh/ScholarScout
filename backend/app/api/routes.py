"""All API routes - owner-isolated. Mounted under /api by main.py.

Privacy model: every request carries an anonymous browser token
(X-Owner-Token). The middleware verifies presence and resolves the owner id
(a keyed hash - the token itself is never stored). Every query below is
scoped to that owner, and user content (uploads, chat) is encrypted with a
key derived from the token. See app/security/owner.py.
"""
from __future__ import annotations

import datetime as dt
import re
import secrets
import time
import threading
from pathlib import Path
from urllib.parse import quote

from fastapi import (APIRouter, BackgroundTasks, Depends, File, HTTPException,
                     Request, UploadFile)
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.config import DATA_DIR, UPLOAD_DIR, settings
from app.database.database import get_db
from app.models.entities import (ApiCache as ApiCacheRow, ChatMessage,
                                 Citation, Chunk as PaperChunk, Paper,
                                 OwnerSession, RetrievalProfile, WatchItem)
from app.rag.answerer import answer_question, stream_answer_events
from app.rag.llm_providers import cloud_ai_available
from app.schemas.api import (ChatRequest, ChatResponse, CloudAIRequest, CompareRequest,
                             ComplexityResponse, DebateResponse, EvidenceCard,
                             FeedbackRequest, FeedbackResponse, GraphResponse,
                             GapsResponse, HealthResponse, NewsResponse,
                             PaperOverview, PaperSummary, PeopleResponse,
                             ReplicationResponse, ReproductionResponse, ResearchLookupRequest,
                             SearchHit, SearchRequest, SearchResponse,
                             TimelineResponse, TopicsResponse, TrendsResponse,
                             WatchResponse, XRayResponse)
from app.security.owner import (decrypt_json, decrypt_text, encrypt_json,
                                encrypt_text)
from app.services.analysis import (eli5 as eli5_svc, gaps,
                                   graph as graph_svc, insights, news,
                                   similar, topics as topics_svc,
                                   timeline as timeline_svc, watchlist)
from app.services.analysis.complexity import build_complexity
from app.services.analysis.eli5 import build_eli5
from app.services.analysis.feedback import (apply_feedback, get_profile,
                                            profile_summary)
from app.services.analysis.xray import build_xray
from app.services.pipeline import process_paper
from app.services.scholarly import openalex
from app.services.vector_store import store

router = APIRouter()

# ----------------------------------------------------------- rate limiting --
_buckets: dict[str, list[float]] = {}
_rate_lock = threading.Lock()


def rate_limit(request: Request, scope: str) -> None:
    now = time.time()
    # Only Uvicorn's explicitly trusted proxy handling may normalize client IP.
    ip = request.client.host if request.client else "unknown"
    limits = (
        (f"owner:{scope}:{request.state.owner}", settings.rate_limit_per_minute),
        (f"ip:{scope}:{ip}", settings.rate_limit_per_minute * 5),
    )
    with _rate_lock:
        if len(_buckets) > 10000:
            for stale_key in list(_buckets):
                _buckets[stale_key] = [t for t in _buckets[stale_key] if now - t < 60]
                if not _buckets[stale_key]:
                    del _buckets[stale_key]
        for key, limit in limits:
            window = _buckets.setdefault(key, [])
            window[:] = [t for t in window if now - t < 60]
            if len(window) >= limit:
                raise HTTPException(429, detail="Too many requests - please wait a moment.",
                                    headers={"Retry-After": "60"})
        for key, _ in limits:
            _buckets[key].append(now)


# ------------------------------------------------------------------ helpers --
def _paper_or_404(db: Session, paper_id: str, owner: str) -> Paper:
    p = db.query(Paper).filter(Paper.id == paper_id, Paper.owner == owner).first()
    if not p:
        raise HTTPException(404, detail="Paper not found.")
    return p


def _summary(p: Paper) -> PaperSummary:
    n_citing = sum(1 for c in p.citations if c.direction == "citing")
    return PaperSummary(
        id=p.id, title=p.title or (p.filename or "Untitled"), filename=p.filename,
        authors=p.authors or [], year=p.year, venue=p.venue, doi=p.doi,
        status=p.status, stages=p.stages or [], page_count=p.page_count or 0,
        citation_count=n_citing,
        created_at=p.created_at.isoformat() if p.created_at else None,
        ecosystem_ok=bool(p.ecosystem_ok), ecosystem_note=p.ecosystem_note,
        error=p.error,
    )


def _store_upload(db: Session, owner: str, token: str, content: bytes,
                  filename: str, dedup_key: str | None = None,
                  arxiv_id: str | None = None, *, title: str | None = None) -> tuple[Paper, bool]:
    """Check quotas and publish an encrypted upload under the erasure lock."""
    from sqlalchemy import func
    from app.security.session import owner_write
    from app.security.owner import encrypt_bytes_with_key, server_wrap_file_key, wrap_file_key
    file_key = secrets.token_bytes(32)
    encrypted = encrypt_bytes_with_key(file_key, content)
    browser_wrap, server_wrap = wrap_file_key(file_key, token), server_wrap_file_key(file_key)
    enc_path = UPLOAD_DIR / f"{secrets.token_hex(16)}.enc"
    try:
        with owner_write(db, owner):
            existing = db.query(Paper).filter(Paper.owner == owner, Paper.dedup_key == dedup_key).first() if dedup_key else None
            if existing is None and arxiv_id:
                existing = db.query(Paper).filter(Paper.owner == owner, Paper.arxiv_id == arxiv_id).first()
            if existing is not None:
                return existing, False
            count, used = db.query(func.count(Paper.id), func.coalesce(func.sum(Paper.size_bytes), 0)).filter(Paper.owner == owner).one()
            if count >= settings.max_session_papers:
                raise HTTPException(429, detail=f"This session allows {settings.max_session_papers} papers. Remove a paper before uploading another.")
            if used + len(content) > settings.max_session_storage_mb * 1024 * 1024:
                raise HTTPException(413, detail=f"This session's {settings.max_session_storage_mb} MB storage limit would be exceeded.")
            total = db.query(func.coalesce(func.sum(Paper.size_bytes), 0)).scalar()
            if total + len(content) > settings.max_total_storage_mb * 1024 * 1024:
                raise HTTPException(503, detail="The service is at its storage capacity. Please retry later.", headers={"Retry-After": "60"})
            enc_path.write_bytes(encrypted)
            paper = Paper(filename=filename, file_path=str(enc_path), size_bytes=len(content),
                          status="queued", title=title or filename.rsplit(".", 1)[0], dedup_key=dedup_key,
                          arxiv_id=arxiv_id, owner=owner, file_key_w1=browser_wrap, file_key_w2=server_wrap)
            db.add(paper)
            db.flush()
        db.refresh(paper)
        return paper, True
    except Exception:
        enc_path.unlink(missing_ok=True)
        raise


def _fingerprint_upload(owner: str, content: bytes) -> str | None:
    """Prepare plaintext only while the session is live, and always remove it."""
    import tempfile
    from app.database.database import SessionLocal
    from app.security.session import owner_write
    from app.ingestion.fingerprint import fingerprint_pdf
    with tempfile.TemporaryDirectory(prefix=f"parse-{owner}-", dir=str(UPLOAD_DIR)) as temporary:
        path = Path(temporary) / "source.pdf"
        with SessionLocal() as guard_db:
            with owner_write(guard_db, owner):
                path.write_bytes(content)
        return fingerprint_pdf(str(path), owner=owner)


# ------------------------------------------------------------------- papers --
@router.post("/papers/upload")
async def upload_papers(request: Request, background: BackgroundTasks,
                        files: list[UploadFile] = File(...),
                        db: Session = Depends(get_db)):
    """Upload PDFs. Duplicate papers (same arXiv id / DOI / title) are detected
    and reported instead of silently re-analyzed. Files are stored encrypted
    with a key derived from the owner's browser token."""
    owner = request.state.owner
    token = request.state.token
    rate_limit(request, "upload")
    if not files:
        raise HTTPException(400, detail="No files received.")
    batch_limit = min(5, settings.max_pending_analyses_per_owner)
    if len(files) > batch_limit:
        raise HTTPException(400, detail=f"Upload at most {batch_limit} PDFs per request. Send larger selections in separate requests.")
    from app.services.jobs import CapacityError, reserve_analysis, submit_analysis
    permits = []
    try:
        for _ in files:
            permits.append(reserve_analysis(owner))
    except CapacityError as exc:
        for permit in permits:
            permit.release()
        raise HTTPException(429, detail=str(exc), headers={"Retry-After": "10"}) from exc
    created: list[Paper] = []
    duplicates: list[dict] = []
    transferred = []
    try:
      for f, permit in zip(files, permits):
        name = f.filename or "paper.pdf"
        ext = Path(name).suffix.lower()
        if ext not in settings.allowed_extensions:
            raise HTTPException(400, detail=f"“{name}” is not a PDF. Please upload PDF files only.")
        content = await f.read(settings.max_upload_mb * 1024 * 1024 + 1)
        if len(content) > settings.max_upload_mb * 1024 * 1024:
            raise HTTPException(413, detail=f"“{name}” exceeds the {settings.max_upload_mb} MB limit.")
        if not content.startswith(b"%PDF"):
            raise HTTPException(400, detail=f"“{name}” doesn't look like a valid PDF file.")
        safe_name = re.sub(r"[^\w.\- ]", "_", name)[:120]
        # Full parsing runs in a killable isolated worker, off the API loop.
        from starlette.concurrency import run_in_threadpool
        try:
            key = await run_in_threadpool(_fingerprint_upload, owner, content)
        except ValueError as exc:
            raise HTTPException(422, detail=str(exc)) from exc
        match = re.match(r"^arxiv:(\d{4}\.\d{4,5})", key or "")
        paper, is_new = await run_in_threadpool(_store_upload, db, owner, token, content,
                                              safe_name, key, match.group(1) if match else None)
        if not is_new:
            permit.release()
            duplicates.append({
                "filename": safe_name,
                "dedup_key": key,
                "existing_id": paper.id,
                "existing_title": paper.title,
            })
            continue
        created.append(paper)
        submit_analysis(paper.id, token, owner=owner, permit=permit)
        transferred.append(permit)
    finally:
        for permit in permits:
            # A transferred permit is released by its worker, not here.
            if permit not in transferred:
                permit.release()
        for file in files:
            await file.close()
    return {"created": [_summary(p) for p in created], "duplicates": duplicates}


# ------------------------------------------------------- ingest by identifier --
ARXIV_ID_RE = re.compile(r"(?:arxiv\.org/(?:abs|pdf)/)?(\d{4}\.\d{4,5})(v\d+)?", re.I)
DOI_RE2 = re.compile(r"(?:doi\.org/)?(10\.\d{4,9}/\S+)", re.I)


@router.post("/papers/ingest", response_model=PaperSummary)
def ingest_by_identifier(request: Request, background: BackgroundTasks,
                         identifier: str, db: Session = Depends(get_db)):
    """Paste an arXiv ID, arXiv URL or DOI - the PDF is fetched and analyzed.
    No manual download needed."""
    owner = request.state.owner
    token = request.state.token
    rate_limit(request, "ingest")
    ident = (identifier or "").strip()
    m = ARXIV_ID_RE.search(ident)
    if m:
        arxiv_id = m.group(1)
        dedup_key = f"arxiv:{arxiv_id}"
        existing = db.query(Paper).filter(Paper.owner == owner, Paper.dedup_key == dedup_key).first()
        if existing:
            return _summary(existing)
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"
        source = f"arXiv:{arxiv_id}"
    else:
        m2 = DOI_RE2.search(ident)
        if not m2:
            raise HTTPException(400, detail="Paste an arXiv ID/URL or a DOI - e.g. 1706.03762 or 10.5555/... ")
        doi = m2.group(1).rstrip(".")
        dedup_key = f"doi:{doi.lower()}"
        existing = db.query(Paper).filter(Paper.owner == owner, Paper.dedup_key == dedup_key).first()
        if existing:
            return _summary(existing)
        pdf_url = None
        source = f"doi:{doi}"

    from app.services.jobs import CapacityError, reserve_analysis, submit_analysis
    try:
        permit = reserve_analysis(owner)
    except CapacityError as exc:
        raise HTTPException(429, detail=str(exc), headers={"Retry-After": "10"}) from exc
    from app.security.outbound import download_public_pdf
    submitted = False
    try:
        if not m:
            resolved = openalex.find_work_by_doi(doi)
            pdf_url = ((resolved or {}).get("open_access") or {}).get("oa_url") if resolved else None
            if not pdf_url:
                raise HTTPException(404, detail="No open-access PDF found for this DOI. Try uploading it instead.")
        try:
            content = download_public_pdf(pdf_url, settings.max_upload_mb * 1024 * 1024)
        except ValueError as exc:
            raise HTTPException(422, detail=str(exc)) from exc
        except Exception:
            raise HTTPException(502, detail=f"Couldn't download the PDF from {source}. Try uploading the file instead.")
        if not content.startswith(b"%PDF"):
            raise HTTPException(422, detail="The source didn't return a valid PDF.")
        safe_name = re.sub(r"[^\w.\- ]", "_", source)[:100] + ".pdf"
        paper, is_new = _store_upload(db, owner, token, content, safe_name, dedup_key,
                                     m.group(1) if m else None)
        if is_new:
            submit_analysis(paper.id, token, owner=owner, permit=permit)
            submitted = True
        return _summary(paper)
    finally:
        if not submitted:
            permit.release()


# ---------------------------------------------------------------- demo paper --
_DEMO_PDF_PATH = Path(__file__).resolve().parents[2] / "demo_assets" / "attention-is-all-you-need.pdf"
_DEMO_PDF_URL = "https://arxiv.org/pdf/1706.03762"


def _demo_pdf_content() -> bytes:
    """Use a local copy when available; otherwise fetch a bounded public PDF."""
    from app.security.outbound import download_public_pdf
    max_bytes = settings.max_upload_mb * 1024 * 1024
    try:
        if _DEMO_PDF_PATH.is_file():
            with _DEMO_PDF_PATH.open("rb") as source:
                content = source.read(max_bytes + 1)
        else:
            content = download_public_pdf(_DEMO_PDF_URL, max_bytes)
    except Exception as exc:
        raise HTTPException(502, detail="The demo paper is temporarily unavailable. Please retry or upload a research PDF.") from exc
    if len(content) > max_bytes or not content.startswith(b"%PDF"):
        raise HTTPException(502, detail="The demo source did not return a PDF within the upload limit. Please retry or upload a research PDF.")
    return content


@router.post("/papers/demo", response_model=PaperSummary)
def load_demo_paper(request: Request, background: BackgroundTasks,
                    db: Session = Depends(get_db)):
    """Load Attention Is All You Need into this owner's temporary library."""
    owner = request.state.owner
    token = request.state.token
    rate_limit(request, "demo")
    existing = db.query(Paper).filter(
        Paper.owner == owner, Paper.dedup_key == "arxiv:1706.03762").first()
    if not existing:
        existing = db.query(Paper).filter(
            Paper.owner == owner, Paper.arxiv_id == "1706.03762").first()
    if existing:
        return _summary(existing)
    safe_name = "attention-is-all-you-need.pdf"
    from app.services.jobs import CapacityError, reserve_analysis, submit_analysis
    try:
        permit = reserve_analysis(owner)
    except CapacityError as exc:
        raise HTTPException(429, detail=str(exc), headers={"Retry-After": "10"}) from exc
    submitted = False
    try:
        content = _demo_pdf_content()
        paper, is_new = _store_upload(db, owner, token, content, safe_name,
                                     "arxiv:1706.03762", "1706.03762", title="Attention Is All You Need")
        if is_new:
            submit_analysis(paper.id, token, owner=owner, permit=permit)
            submitted = True
        return _summary(paper)
    finally:
        if not submitted:
            permit.release()


@router.get("/papers", response_model=list[PaperSummary])
def list_papers(request: Request, db: Session = Depends(get_db)):
    papers = db.query(Paper).filter(Paper.owner == request.state.owner) \
        .order_by(Paper.created_at.desc()).all()
    if not papers and settings.demo_autoload:
        load_demo_paper(request, BackgroundTasks(), db)
        papers = db.query(Paper).filter(Paper.owner == request.state.owner) \
            .order_by(Paper.created_at.desc()).all()
    return [_summary(p) for p in papers]


@router.get("/papers/{paper_id}", response_model=PaperOverview)
def get_paper(request: Request, paper_id: str, db: Session = Depends(get_db)):
    p = _paper_or_404(db, paper_id, request.state.owner)
    meta = p.meta or {}
    base = _summary(p)
    return PaperOverview(
        **base.model_dump(),
        abstract=p.abstract or "",
        keywords=p.keywords or [],
        methods=meta.get("methods") or [],
        datasets=meta.get("datasets") or [],
        metrics=meta.get("metrics") or [],
        figures=meta.get("figures") or [],
        tables=meta.get("tables") or [],
        funding=p.funding or [],
        openalex_id=p.openalex_id,
        match_confidence=meta.get("match_confidence"),
        match_status=meta.get("match_status"),
        match_source=meta.get("match_source"),
        embedding_backend=store.backend or settings.embedding_backend,
    )


@router.delete("/papers/{paper_id}")
def delete_paper(request: Request, paper_id: str, db: Session = Depends(get_db)):
    from app.security.session import owner_write
    with owner_write(db, request.state.owner):
        p = _paper_or_404(db, paper_id, request.state.owner)
        try:
            Path(p.file_path).unlink(missing_ok=True)
        except Exception:
            pass
        # purge derived data: Gemini embedding vectors of this paper's chunks and
        # any owner-scoped cached answers about it
        from app.services.embeddings import purge_embedding_cache_by_hash, emb_hash
        hashes = [emb_hash(c.text or "") for c in db.query(PaperChunk)
                  .filter(PaperChunk.paper_id == paper_id).all()]
        purge_embedding_cache_by_hash(hashes)
        oh = request.state.owner
        # Answer caches are owner-scoped; removing all of this session's answers
        # avoids retaining answers that combined evidence from multiple papers.
        db.query(ApiCacheRow).filter(ApiCacheRow.key.like(f"synth::{oh}::%")) \
            .delete(synchronize_session=False)
        db.query(ApiCacheRow).filter(ApiCacheRow.key.like(f"eli5::v4::{oh}::%")) \
            .delete(synchronize_session=False)
        db.query(ChatMessage).filter(ChatMessage.paper_id == paper_id,
                                     ChatMessage.owner == oh).delete()
        db.query(WatchItem).filter(WatchItem.paper_id == paper_id,
                                   WatchItem.owner == oh).delete()
        db.query(RetrievalProfile).filter(RetrievalProfile.paper_id == paper_id).delete()
        db.delete(p)
    from app.services.scoped_index import forget_owner
    forget_owner(request.state.owner)
    store.rebuild()
    return {"ok": True}


# -------------------------------------------------------------- privacy zone --
@router.get("/me/cloud-ai")
def cloud_ai_preference(request: Request, db: Session = Depends(get_db)):
    row = db.get(OwnerSession, request.state.owner)
    available = cloud_ai_available()
    return {"enabled": bool(available and row and row.cloud_ai_opt_in), "available": available}


@router.post("/me/cloud-ai")
def set_cloud_ai_preference(request: Request, pref: CloudAIRequest,
                            db: Session = Depends(get_db)):
    if pref.enabled and not cloud_ai_available():
        raise HTTPException(409, detail="Cloud AI is unavailable on this instance. Local evidence answers remain available.")
    from app.security.session import owner_write
    with owner_write(db, request.state.owner):
        row = db.get(OwnerSession, request.state.owner)
        row.cloud_ai_opt_in = pref.enabled
    return {"enabled": pref.enabled}


@router.get("/me/research-lookups")
def research_lookup_preference(request: Request, db: Session = Depends(get_db)):
    row = db.get(OwnerSession, request.state.owner)
    return {"enabled": bool(row and row.research_lookup_opt_in)}


@router.post("/me/research-lookups")
def set_research_lookup_preference(request: Request, pref: ResearchLookupRequest,
                                   db: Session = Depends(get_db)):
    from app.security.session import owner_write
    with owner_write(db, request.state.owner):
        row = db.get(OwnerSession, request.state.owner)
        was_enabled = bool(row.research_lookup_opt_in)
        row.research_lookup_opt_in = pref.enabled
    queued = 0
    deferred = 0
    if pref.enabled and not was_enabled:
        from app.services.jobs import CapacityError, mark_context_stage, submit_enrichment
        ids = [p[0] for p in db.query(Paper.id).filter(Paper.owner == request.state.owner, Paper.status == "ready").all()]
        for paper_id in ids:
            with owner_write(db, request.state.owner):
                paper = _paper_or_404(db, paper_id, request.state.owner)
                mark_context_stage(paper, "active", "Refreshing connected research")
            try:
                queued += int(submit_enrichment(paper_id, owner=request.state.owner))
            except CapacityError:
                deferred += 1
                with owner_write(db, request.state.owner):
                    paper = _paper_or_404(db, paper_id, request.state.owner)
                    mark_context_stage(paper, "skipped", "Analysis slots are full; refresh this paper when a slot is free")
    return {"enabled": pref.enabled, "refreshing": bool(queued), "papers_queued": queued, "papers_deferred": deferred}


@router.post("/papers/{paper_id}/refresh-context")
def refresh_paper_context(request: Request, paper_id: str, db: Session = Depends(get_db)):
    paper = _paper_or_404(db, paper_id, request.state.owner)
    from app.security.session import research_lookup_allowed
    if not research_lookup_allowed(request.state.owner):
        raise HTTPException(409, detail="Enable connected research before refreshing external context.")
    if paper.status != "ready":
        raise HTTPException(409, detail="Wait for the document analysis to finish before refreshing context.")
    rate_limit(request, "context-refresh")
    from app.services.jobs import CapacityError, mark_context_stage, submit_enrichment
    from app.security.session import owner_write
    with owner_write(db, request.state.owner):
        paper = _paper_or_404(db, paper_id, request.state.owner)
        mark_context_stage(paper, "active", "Refreshing connected research")
    try:
        queued = submit_enrichment(paper_id, owner=request.state.owner)
    except CapacityError as exc:
        with owner_write(db, request.state.owner):
            paper = _paper_or_404(db, paper_id, request.state.owner)
            mark_context_stage(paper, "skipped", "Analysis slots are full; retry shortly")
        raise HTTPException(429, detail=str(exc), headers={"Retry-After": "10"}) from exc
    return {"paper_id": paper.id, "refreshing": True, "queued": queued}


@router.delete("/me/purge")
def purge_my_data(request: Request, db: Session = Depends(get_db)):
    """Erase EVERYTHING belonging to this owner: papers, encrypted files,
    chats, watchlist, learned profiles. Irreversible."""
    from app.security.session import purge_owner
    counts = purge_owner(db, request.state.owner)
    from app.security.owner import forget_token
    forget_token(request.state.token)
    return {"ok": True, **counts}


@router.get("/me/privacy")
def privacy_info():
    """What the server keeps and how it's protected - machine-readable honesty."""
    return {
        "identity": "anonymous - a random key kept only for this browser tab; no account, no email",
        "uploads": "stored encrypted (AES-256-GCM) with a per-file key; the running server "
                   "can decrypt them to process the paper, but does not store your browser token",
        "chats": "stored encrypted with a key derived from the tab's anonymous token",
        "paper_text": "titles, abstracts, structured metadata, sections and search chunks are encrypted at rest "
                      "(AES-256-GCM, server-held key)",
        "readable_without_your_browser": "The running server can decrypt uploads and paper text "
                                         "for analysis. Chat content requires your tab token. "
                                         "Other visitors cannot access your session data.",
        "purge": "DELETE /api/me/purge erases session data immediately; browser exit sends a best-effort purge",
        "exit_fallback": "Abandoned sessions expire after the configured idle period",
        "cloud_ai": "off by default; turn on per session to send questions and relevant passages to configured providers",
        "research_lookups": "off by default; turn on before upload to send title/keywords to public research services",
        "auto_retention_hours": settings.retention_hours,
        "limits": {"upload_mb": settings.max_upload_mb, "pdf_pages": settings.max_pdf_pages,
                   "papers": settings.max_session_papers, "storage_mb": settings.max_session_storage_mb,
                   "concurrent_analyses": settings.max_pending_analyses_per_owner},
        "provider_erasure": "Session deletion erases application data, not copies retained by external providers. Do not enable cloud AI for confidential or personal research with unpaid Gemini.",
        "revocation": "A content-free, irreversible session hash may remain to prevent an ended key from recreating data; no document or chat content is retained in that record.",
        "cloud_ai_available": cloud_ai_available(),
        "external_processors": [
            {"service": "OpenAlex / Crossref", "sent": "your paper's title (to match it in research databases)",
             "receives_user_content": True},
            {"service": "arXiv", "sent": "your paper's keywords/title (to find recent preprints)",
             "receives_user_content": True},
            {"service": "Google Gemini", "sent": "relevant paper passages and chat questions for answers when cloud AI is enabled",
             "receives_user_content": True},
            {"service": "Groq", "sent": "passages of your paper + your chat questions (fallback answer synthesis)",
             "receives_user_content": True},
            {"service": "GDELT", "sent": "your paper's distinctive topic words (news search)",
             "receives_user_content": True},
            {"service": "NewsAPI (optional)", "sent": "your paper's topic words (only if NEWS_API_KEY is set)",
             "receives_user_content": True},
        ],
        "not_required_for_access": ["email", "name", "password", "payment info"],
    }


@router.get("/me/usage")
def my_provider_usage(request: Request):
    from app.services.usage import usage_report
    return usage_report(request.state.owner)


# ------------------------------------------------------------------ search --
@router.post("/search", response_model=SearchResponse)
def search(request: Request, req: SearchRequest, db: Session = Depends(get_db)):
    rate_limit(request, "search")
    from app.rag.retriever import retrieve
    owner = request.state.owner
    my_papers = [row[0] for row in db.query(Paper.id).filter(Paper.owner == owner).all()]
    wanted = req.paper_ids or my_papers
    wanted = [pid for pid in wanted if pid in set(my_papers)]  # isolation guard
    hits = retrieve(req.query, k=10, paper_ids=wanted or ["__none__"])
    results = [
        SearchHit(
            paper_id=h.paper_id, paper_title=h.paper_title, section=h.section,
            page=h.page, snippet=h.text[:500], score=round(h.score, 3),
            explanation=f"Retrieved from the {h.section or 'document'} section; score reflects ranking, not factual confidence",
        ) for h in hits
    ]
    return SearchResponse(query=req.query, backend=store.backend, results=results)


# -------------------------------------------------------------------- chat --
def _check_answer_mode(req: ChatRequest, owner: str) -> None:
    """Reject a cloud choice before answering or opening an SSE response."""
    if req.answer_mode != "cloud":
        return
    if not cloud_ai_available():
        raise HTTPException(409, detail="Cloud AI is unavailable on this instance. Choose Local Evidence to continue.")
    from app.security.session import cloud_ai_allowed
    if not cloud_ai_allowed(owner):
        raise HTTPException(403, detail="Enable Cloud AI for this research session before choosing cloud answers. Local Evidence remains available.")


@router.post("/chat", response_model=ChatResponse)
def chat(request: Request, req: ChatRequest, db: Session = Depends(get_db)):
    rate_limit(request, "chat")
    owner = request.state.owner
    token = request.state.token
    _paper_or_404(db, req.paper_id, owner)
    _check_answer_mode(req, owner)
    db.rollback()  # Do not retain a read transaction across slow synthesis.
    ans = answer_question(req.message, paper_ids=[req.paper_id],
                          owner=owner, token=token, answer_mode=req.answer_mode)
    from app.security.session import owner_write
    with owner_write(db, owner):
        _paper_or_404(db, req.paper_id, owner)
        assistant = ChatMessage(
            paper_id=req.paper_id, role="assistant", owner=owner, mode=ans.mode,
            content=encrypt_text(token, ans.text),
            evidence=encrypt_json(token, [{
                "paper_id": e.paper_id, "paper_title": e.paper_title, "section": e.section,
                "page": e.page, "snippet": e.text[:400], "score": e.score,
            } for e in ans.evidence]),
        )
        db.add(ChatMessage(paper_id=req.paper_id, role="user", owner=owner,
                           content=encrypt_text(token, req.message)))
        db.add(assistant)
    db.refresh(assistant)
    return ChatResponse(
        message_id=assistant.id,
        answer=ans.text,
        evidence=[EvidenceCard(
            paper_id=e.paper_id, paper_title=e.paper_title, section=e.section,
            page=e.page, snippet=e.text[:400], score=e.score,
        ) for e in ans.evidence],
        mode=ans.mode, cached=ans.cached, insufficient=ans.insufficient,
        suggestions=ans.suggestions,
    )


@router.post("/chat/stream")
def chat_stream(request: Request, req: ChatRequest):
    """Server-sent events: evidence cards immediately, then streamed answer
    tokens (Gemini/Groq) with extractive fallback. Same persistence as /chat."""
    from fastapi.responses import StreamingResponse
    rate_limit(request, "chat")
    owner = request.state.owner
    token = request.state.token

    # verify ownership before opening the stream
    db = next(get_db())
    try:
        _paper_or_404(db, req.paper_id, owner)
        _check_answer_mode(req, owner)
    finally:
        db.close()

    def gen():
        yield 'data: {"type": "start"}\n\n'
        try:
            for event in stream_answer_events(req.message, paper_ids=[req.paper_id],
                                              owner=owner, token=token, answer_mode=req.answer_mode):
                yield event
        except Exception:
            # stream_answer_events handles its own errors; this guards the pipe
            import json as _json
            import logging
            logging.getLogger("scholarscout.chat").exception("chat stream pipe failed")
            yield "data: " + _json.dumps({
                "type": "error",
                "message": "Something interrupted this answer. Please send the question again.",
            }) + "\n\n"
            yield 'data: {"type": "done", "message_id": null, "mode": "error", "insufficient": true, "suggestions": []}\n\n'

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/chat/{paper_id}/history")
def chat_history(request: Request, paper_id: str, db: Session = Depends(get_db)):
    owner = request.state.owner
    token = request.state.token
    _paper_or_404(db, paper_id, owner)
    msgs = db.query(ChatMessage).filter(
        ChatMessage.paper_id == paper_id, ChatMessage.owner == owner) \
        .order_by(ChatMessage.created_at).limit(60).all()
    return [{"id": m.id, "role": m.role,
             "content": decrypt_text(token, m.content),
             "mode": m.mode, "evidence": decrypt_json(token, m.evidence)}
            for m in msgs]


# ---------------------------------------------------------------- feedback --
@router.post("/chat/feedback", response_model=FeedbackResponse)
def chat_feedback(request: Request, req: FeedbackRequest, db: Session = Depends(get_db)):
    from app.security.session import owner_write
    with owner_write(db, request.state.owner):
        """Rate an assistant answer; the retrieval engine learns from it."""
        owner = request.state.owner
        token = request.state.token
        msg = db.get(ChatMessage, req.message_id)
        if not msg or msg.role != "assistant" or msg.owner != owner:
            raise HTTPException(404, detail="Message not found.")
        question = (
            db.query(ChatMessage).filter(
                ChatMessage.paper_id == msg.paper_id,
                ChatMessage.owner == owner,
                ChatMessage.role == "user",
                ChatMessage.created_at <= msg.created_at,
            ).order_by(ChatMessage.created_at.desc()).first()
        )
        evidence = [
            {"section": e.get("section"), "text": e.get("snippet") or ""}
            for e in decrypt_json(token, msg.evidence) if isinstance(e, dict)
        ]
        profile = apply_feedback(db, msg.paper_id, evidence,
                                 decrypt_text(token, question.content) if question else "",
                                 req.rating, owner=owner)
        return FeedbackResponse(ok=True, profile=profile_summary(profile))


@router.get("/papers/{paper_id}/retrieval-profile")
def retrieval_profile(request: Request, paper_id: str, db: Session = Depends(get_db)):
    """Inspect what the engine learned from your feedback."""
    _paper_or_404(db, paper_id, request.state.owner)
    profile = get_profile(db, paper_id, owner=request.state.owner)
    return profile_summary(profile)


# -------------------------------------------------------------------- eli5 --
@router.get("/papers/{paper_id}/eli5")
def eli5(request: Request, paper_id: str, db: Session = Depends(get_db)):
    """Kid-friendly dual-mode explanation of the paper's key findings."""
    return eli5_svc.build_eli5(db, _paper_or_404(db, paper_id, request.state.owner),
                               owner=request.state.owner)


# ------------------------------------------------------------------- xray --
@router.get("/papers/{paper_id}/xray", response_model=XRayResponse)
def xray(request: Request, paper_id: str, db: Session = Depends(get_db)):
    return build_xray(db, _paper_or_404(db, paper_id, request.state.owner))


@router.get("/papers/{paper_id}/complexity", response_model=ComplexityResponse)
def complexity(request: Request, paper_id: str, db: Session = Depends(get_db)):
    return build_complexity(db, _paper_or_404(db, paper_id, request.state.owner))


@router.get("/papers/{paper_id}/reproduction", response_model=ReproductionResponse)
def reproduction(request: Request, paper_id: str, db: Session = Depends(get_db)):
    return gaps.build_reproduction(db, _paper_or_404(db, paper_id, request.state.owner))


# --------------------------------------------------------------- literature --
@router.get("/papers/{paper_id}/similar")
def similar_papers(request: Request, paper_id: str, db: Session = Depends(get_db)):
    owner = request.state.owner
    p = _paper_or_404(db, paper_id, owner)
    return {"results": similar.similar_papers(db, p, owner=owner)}


@router.get("/papers/{paper_id}/citations")
def citations(request: Request, paper_id: str, db: Session = Depends(get_db)):
    p = _paper_or_404(db, paper_id, request.state.owner)
    rows = db.query(Citation).filter(Citation.paper_id == paper_id).all()
    return {"citations": [{
        "id": c.id, "direction": c.direction, "title": c.title,
        "authors": (c.authors or [])[:6], "year": c.year, "venue": c.venue,
        "cited_by_count": c.cited_by_count,
        "url": (f"https://doi.org/{c.doi}" if c.doi else
                f"https://openalex.org/{c.openalex_id}" if c.openalex_id else None),
        "is_replication": bool(c.is_replication), "is_contradictory": bool(c.is_contradictory),
        "is_correction": bool(c.is_correction), "is_extension": bool(c.is_extension),
        "snippet": c.snippet,
    } for c in rows]}


# ---------------------------------------------------------------- citations --
@router.get("/papers/{paper_id}/cite")
def cite_paper(request: Request, paper_id: str, format: str = "bibtex",
               db: Session = Depends(get_db)):
    """BibTeX / RIS citation export for the paper."""
    p = _paper_or_404(db, paper_id, request.state.owner)
    from app.services.analysis.cite import bibtex_for, ris_for
    fmt = (format or "bibtex").lower()
    if fmt in ("bibtex", "bib", "tex"):
        return PlainTextResponse(bibtex_for(p), media_type="application/x-bibtex")
    if fmt == "ris":
        return PlainTextResponse(ris_for(p), media_type="application/x-research-info-systems")
    raise HTTPException(400, detail="Unsupported format - use bibtex or ris.")


# ----------------------------------------------------------------- timeline --
@router.get("/papers/{paper_id}/timeline", response_model=TimelineResponse)
def timeline(request: Request, paper_id: str, db: Session = Depends(get_db)):
    return timeline_svc.get_timeline(db, _paper_or_404(db, paper_id, request.state.owner))


# ------------------------------------------------------------------- debate --
@router.get("/papers/{paper_id}/debate", response_model=DebateResponse)
def debate(request: Request, paper_id: str, db: Session = Depends(get_db)):
    return insights.get_debate(db, _paper_or_404(db, paper_id, request.state.owner))


@router.get("/papers/{paper_id}/replication", response_model=ReplicationResponse)
def replication(request: Request, paper_id: str, db: Session = Depends(get_db)):
    return insights.get_replication(db, _paper_or_404(db, paper_id, request.state.owner))


@router.get("/papers/{paper_id}/health", response_model=HealthResponse)
def health(request: Request, paper_id: str, db: Session = Depends(get_db)):
    return insights.get_health(db, _paper_or_404(db, paper_id, request.state.owner))


# ------------------------------------------------------------------- people --
@router.get("/papers/{paper_id}/people", response_model=PeopleResponse)
def people(request: Request, paper_id: str, db: Session = Depends(get_db)):
    return insights.get_people(db, _paper_or_404(db, paper_id, request.state.owner))


@router.get("/papers/{paper_id}/institutions")
def institutions(request: Request, paper_id: str, db: Session = Depends(get_db)):
    return {"institutions": insights.get_institutions(db, _paper_or_404(db, paper_id, request.state.owner))}


@router.get("/papers/{paper_id}/funding")
def funding(request: Request, paper_id: str, db: Session = Depends(get_db)):
    p = _paper_or_404(db, paper_id, request.state.owner)
    return {"funding": p.funding or [],
            "conflicts": (p.meta or {}).get("conflicts") or [],
            "note": "Extracted from the paper's acknowledgement/funding text. Contextual "
                    "information only - not an indication of bias or misconduct."}


# ------------------------------------------------------------- suggestions --
@router.get("/papers/{paper_id}/suggestions")
def paper_suggestions(request: Request, paper_id: str, db: Session = Depends(get_db)):
    """Dynamic follow-up questions tailored to what this paper contains."""
    from app.rag.answerer import build_suggestions
    p = _paper_or_404(db, paper_id, request.state.owner)
    return {"suggestions": build_suggestions(db, p)}


# -------------------------------------------------------------------- graph --
@router.get("/graph", response_model=GraphResponse)
def research_graph(request: Request, paper_ids: str | None = None,
                   db: Session = Depends(get_db)):
    owner = request.state.owner
    ids = paper_ids.split(",") if paper_ids else None
    return graph_svc.build_graph(db, ids, owner=owner)


# ----------------------------------------------------- topics / trends/ news --
@router.get("/topics", response_model=TopicsResponse)
def topics(request: Request, db: Session = Depends(get_db)):
    return topics_svc.build_topics(db, owner=request.state.owner)


@router.get("/trends", response_model=TrendsResponse)
def trends(request: Request, db: Session = Depends(get_db)):
    return topics_svc.build_trends(db, owner=request.state.owner)


@router.get("/papers/{paper_id}/news", response_model=NewsResponse)
def latest_news(request: Request, paper_id: str, db: Session = Depends(get_db)):
    paper = _paper_or_404(db, paper_id, request.state.owner)
    from app.security.session import research_lookup_allowed
    if not research_lookup_allowed(request.state.owner):
        return NewsResponse(academic=[], news=[], commentary=[], status="disabled",
                            note="Connected research lookups are off for this session.")
    return news.get_news(db, paper)


# --------------------------------------------------------------------- gaps --
@router.get("/papers/{paper_id}/gaps", response_model=GapsResponse)
def research_gaps(request: Request, paper_id: str, db: Session = Depends(get_db)):
    return gaps.build_gaps(db, _paper_or_404(db, paper_id, request.state.owner))


@router.get("/papers/{paper_id}/ideas", response_model=GapsResponse)
def research_ideas(request: Request, paper_id: str, db: Session = Depends(get_db)):
    return gaps.generate_ideas(db, _paper_or_404(db, paper_id, request.state.owner))


# ---------------------------------------------------------------- comparison --
@router.post("/compare")
def compare(request: Request, req: CompareRequest, db: Session = Depends(get_db)):
    owner = request.state.owner
    papers = db.query(Paper).filter(Paper.id.in_(req.paper_ids), Paper.owner == owner).all()
    if len(papers) < 2:
        raise HTTPException(400, detail="Select at least two processed papers to compare.")
    return {"papers": [{
        "id": p.id, "title": p.title, "year": p.year, "venue": p.venue,
        "authors": [a.get("name") for a in (p.authors or [])[:5]],
        "question": (p.abstract or "")[:250],
        "methods": (p.meta or {}).get("methods") or [],
        "datasets": (p.meta or {}).get("datasets") or [],
        "metrics": (p.meta or {}).get("metrics") or [],
        "limitations": _limit_snippet(db, p),
        "citations": (p.meta or {}).get("openalex_cited_by"),
    } for p in papers]}


def _limit_snippet(db: Session, p: Paper) -> str:
    from app.models.entities import PaperSection
    sec = db.query(PaperSection).filter(PaperSection.paper_id == p.id,
                                        PaperSection.name == "limitations").first()
    if sec:
        return sec.text[:250]
    return "No explicit limitations section detected."


# ---------------------------------------------------------------- watchlist --
@router.get("/watchlist", response_model=list[WatchResponse])
def get_watchlist(request: Request, db: Session = Depends(get_db)):
    items = db.query(WatchItem).filter(WatchItem.owner == request.state.owner) \
        .order_by(WatchItem.created_at.desc()).all()
    return [watchlist._to_response(i, 0) for i in items]


@router.post("/watchlist/{paper_id}", response_model=WatchResponse)
def watch_paper(request: Request, paper_id: str, db: Session = Depends(get_db)):
    from app.security.session import owner_write
    with owner_write(db, request.state.owner):
        paper = _paper_or_404(db, paper_id, request.state.owner)
        item = db.query(WatchItem).filter(WatchItem.paper_id == paper_id, WatchItem.owner == request.state.owner).first()
        if item is None:
            item = WatchItem(paper_id=paper_id, owner=request.state.owner, label=paper.title or "", last_count=0)
            db.add(item)
    db.refresh(item)
    return watchlist._to_response(item, 0)


@router.delete("/watchlist/{watch_id}")
def unwatch(request: Request, watch_id: int, db: Session = Depends(get_db)):
    from app.security.session import owner_write
    with owner_write(db, request.state.owner):
        n = db.query(WatchItem).filter(WatchItem.id == watch_id,
                                       WatchItem.owner == request.state.owner).delete()
    if not n:
        raise HTTPException(404, detail="Watch item not found.")
    return {"ok": True}


@router.post("/watchlist/{watch_id}/check", response_model=WatchResponse)
def check_watch(request: Request, watch_id: int, db: Session = Depends(get_db)):
    rate_limit(request, "watch-check")
    from app.security.session import research_lookup_allowed
    if not research_lookup_allowed(request.state.owner):
        raise HTTPException(403, detail="Enable connected research lookups before checking for updates.")
    item = db.query(WatchItem).filter(WatchItem.id == watch_id,
                                      WatchItem.owner == request.state.owner).first()
    if not item:
        raise HTTPException(404, detail="Watch item not found.")
    added = watchlist.check_updates(db, item)
    return watchlist._to_response(item, added)


# ------------------------------------------------------- file & export --
@router.get("/papers/{paper_id}/file")
def get_paper_file(request: Request, paper_id: str, db: Session = Depends(get_db)):
    """The owner's decrypted PDF (for the built-in viewer / download)."""
    p = _paper_or_404(db, paper_id, request.state.owner)
    token = request.state.token
    path = Path(p.file_path or "")
    if not path.exists():
        raise HTTPException(404, detail="The uploaded file is no longer on disk.")
    from fastapi.responses import Response
    from app.security.owner import decrypt_bytes, decrypt_bytes_from_raw, unwrap_file_key
    try:
        if path.suffix == ".enc":
            # current scheme: per-file key (dual-wrapped); legacy: owner-token key
            file_key = unwrap_file_key(token, p.file_key_w1, p.file_key_w2)
            if file_key is not None:
                content = decrypt_bytes_from_raw(file_key, str(path))
            else:
                content = decrypt_bytes(token, path.read_bytes())
        else:
            content = path.read_bytes()
    except Exception:
        raise HTTPException(403, detail="This file was encrypted with a different browser key and "
                                        "cannot be opened here.")
    return Response(content, media_type="application/pdf",
                    headers={"Content-Disposition": "inline; filename=\"paper.pdf\"; filename*=UTF-8''" +
                             quote(p.filename or "paper.pdf", safe="")})


@router.get("/papers/{paper_id}/export")
def export_dossier(request: Request, paper_id: str, db: Session = Depends(get_db)):
    """Whole dossier as Markdown - for notes, sharing, or attaching to assignments."""
    owner = request.state.owner
    p = _paper_or_404(db, paper_id, owner)
    meta = p.meta or {}
    lines: list[str] = [f"# {p.title or 'Untitled'}", ""]
    sub = [str(p.year or ""), p.venue or ""]
    sub = [s for s in sub if s]
    authors = ", ".join(a.get("name") for a in (p.authors or []) if a.get("name"))
    if authors:
        lines.append(f"**Authors:** {authors}  ")
    if sub:
        lines.append("**" + " · ".join(sub) + "**  ")
    if p.doi:
        lines.append(f"**DOI:** {p.doi}  ")
    lines.append("")

    xray = build_xray(db, p)
    sections = [("Research Question", xray.research_question), ("Main Claim", xray.main_claim),
                ("Methodology", xray.methodology), ("Dataset", xray.dataset),
                ("Evaluation", xray.evaluation)]
    lines += ["## Research X-Ray", ""]
    for label, f in sections:
        if f:
            lines += [f"### {label}", f.text, f"*(source: {f.evidence_section}"
                      + (f", page {f.evidence_page}" if f.evidence_page else "") + ")*", ""]
    if xray.main_results:
        lines += ["### Main Results", ""]
        for r in xray.main_results:
            lines.append(f"- {r.text}")
        lines.append("")

    try:
        from app.services.analysis.gaps import build_gaps
        gaps = build_gaps(db, p)
        lines += ["## Research Gaps", ""]
        for g in gaps.gaps:
            lines.append(f"- **{g.title}** - {g.why}")
        lines.append("")
        lines.append(f"> {gaps.disclaimer}")
        lines.append("")
    except Exception:
        pass

    try:
        from app.services.analysis.insights import get_debate
        debate = get_debate(db, p)
        if debate.internal_tensions:
            lines += ["## Open Questions (from the paper itself)", ""]
            for t in debate.internal_tensions:
                lines.append(f"- {t.text} *({t.section})*")
            lines.append("")
    except Exception:
        pass

    lines += ["---", f"Generated by ScholarScout on {dt.date.today().isoformat()}.",
              "Fields quote the paper; external context comes from OpenAlex/Crossref/arXiv."]
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse("\n".join(lines), media_type="text/markdown",
                             headers={"Content-Disposition": f'attachment; filename="dossier-{p.id}.md"'})


# ------------------------------------------------------------------- system --
@router.get("/health")
def system_health():
    from sqlalchemy import text as sql_text
    from app.database.database import SessionLocal

    db_ok = False
    try:
        with SessionLocal() as db:
            db.execute(sql_text("SELECT 1"))
        db_ok = True
    except Exception:
        pass
    index = store.integrity_status()
    if settings.production:
        return {"status": "ok" if db_ok and index["ok"] else "degraded"}
    configured = {"gemini": bool(settings.gemini_api_key),
                  "groq": bool(settings.groq_api_key),
                  "openai": bool(settings.openai_api_key)}
    return {"status": "ok" if db_ok and index["ok"] else "degraded",
            "database_reachable": db_ok,
            "index_consistent": bool(index["ok"]),
            "index_issue": index["issue"],
            "embedding_backend": index["backend"] or "not loaded yet",
            "index_chunk_count": index["chunk_count"],
            "provider_keys_present": configured,
            "llm_providers": [name for name, present in configured.items() if present],
            "llm_chain_order": [s.strip() for s in settings.llm_provider_chain.split(",") if s.strip()],
            "time": dt.datetime.utcnow().isoformat()}
