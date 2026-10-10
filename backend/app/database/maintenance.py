"""SQLite startup migrations and crash reconciliation."""
from __future__ import annotations

import logging

from sqlalchemy import func, text

import datetime as dt
import hashlib
from pathlib import Path
import re
import secrets
import shutil
from types import SimpleNamespace

from app.config import DATA_DIR, UPLOAD_DIR
from app.database.database import SessionLocal, engine
from app.models.entities import ApiCache, OwnerSession, Paper

log = logging.getLogger("scholarscout.maintenance")


# Constant identifiers only: these names are never populated from a request.
_ENCRYPTED_COLUMNS = {
    "papers": ("filename", "title", "authors", "abstract", "venue", "keywords",
               "funding", "stages", "error", "ecosystem_note", "meta"),
    "paper_sections": ("text",),
    "chunks": ("section_name", "text"),
    "authors": ("name", "affiliation", "research_areas"),
    "citations": ("title", "authors", "venue", "institution_hints",
                  "abstract_inverted", "snippet"),
    "research_events": ("title", "description"),
    "watchlist": ("label", "updates"),
    "retrieval_profiles": ("sections", "terms"),
    "api_cache": ("payload",),
}


def _encrypt_legacy_metadata(db) -> None:
    """Rewrite legacy cleartext cells before ORM reads can load them."""
    from app.security.owner import server_encrypt

    changed = 0
    for table, columns in _ENCRYPTED_COLUMNS.items():
        pk = "key" if table == "api_cache" else "paper_id" if table == "retrieval_profiles" else "id"
        for column in columns:
            rows = db.execute(text(
                f"SELECT {pk}, {column} FROM {table} WHERE {column} IS NOT NULL "
                f"AND {column} != '' AND {column} NOT LIKE 'srv:v1:%'"
            )).fetchall()
            for row_id, value in rows:
                db.execute(text(f"UPDATE {table} SET {column} = :value WHERE {pk} = :row_id"),
                           {"value": server_encrypt(value), "row_id": row_id})
            changed += len(rows)
    # Older title fingerprints contained the readable title in an indexed key.
    for pid, old in db.execute(text(
        "SELECT id, dedup_key FROM papers WHERE dedup_key LIKE 'title:%'"
    )).fetchall():
        if len(old) != len("title:") + 64 or not all(c in "0123456789abcdef" for c in old[6:]):
            digest = hashlib.sha256(old[6:].encode("utf-8")).hexdigest()
            db.execute(text("UPDATE papers SET dedup_key = :key WHERE id = :pid"),
                       {"key": "title:" + digest, "pid": pid})
            changed += 1
    if changed:
        db.commit()
        log.info("encrypted %d legacy metadata cells and title fingerprints", changed)


def _hide_legacy_upload_filenames(db, papers: list[Paper]) -> None:
    """Move old descriptively named files to opaque encrypted file paths."""
    from app.security.owner import encrypt_bytes_with_key, server_wrap_file_key

    for paper in papers:
        if not paper.file_path:
            continue
        old = Path(paper.file_path).resolve()
        if not old.is_relative_to(UPLOAD_DIR.resolve()) or not old.is_file():
            continue
        if re.fullmatch(r"[0-9a-f]{32}\.enc", old.name):
            continue
        new = UPLOAD_DIR / f"{secrets.token_hex(16)}.enc"
        if old.suffix == ".enc":
            shutil.copyfile(old, new)
        else:
            file_key = secrets.token_bytes(32)
            new.write_bytes(encrypt_bytes_with_key(file_key, old.read_bytes()))
            paper.file_key_w1 = None
            paper.file_key_w2 = server_wrap_file_key(file_key)
        paper.file_path = str(new)
        db.commit()
        old.unlink(missing_ok=True)


def _refresh_extracted_metadata(db, papers: list[Paper]) -> None:
    """Correct older heuristic results from stored paper sections on upgrade."""
    from app.ingestion import metadata

    changed = 0
    for paper in papers:
        old = dict(paper.meta or {})
        if paper.status != "ready" or old.get("extraction_version", 0) >= 2:
            continue
        sections = list(paper.sections)
        if not sections:
            continue
        doc = SimpleNamespace(title=paper.title or "",
                              full_text=" ".join(s.text or "" for s in sections), pages=[])
        old.update({
            "extraction_version": 2,
            "methods": metadata.extract_methods(doc, sections),
            "datasets": metadata.extract_datasets(doc, sections),
            "metrics": metadata.extract_metrics(doc, sections),
        })
        paper.meta = old
        changed += 1
    if changed:
        db.commit()
        log.info("refreshed extracted metadata for %d papers", changed)


def _column_exists(conn, table: str, column: str) -> bool:
    res = conn.execute(text(f"PRAGMA table_info({table})"))
    return any(row[1] == column for row in res)


def run() -> None:
    # create_all does not add indexes to existing tables after an upgrade.
    from app.models.entities import Paper, Chunk, PaperSection, Author, Citation, ResearchEvent, WatchItem
    for model in (Paper, Chunk, PaperSection, Author, Citation, ResearchEvent, WatchItem):
        for index in model.__table__.indexes:
            index.create(bind=engine, checkfirst=True)
    # 1. schema: add columns missing from older SQLite databases.
    with engine.connect() as conn:
        for table, column in (("papers", "dedup_key"),
                              ("papers", "file_key_w1"),
                              ("papers", "file_key_w2"),
                              ("chat_messages", "owner"),
                              ("retrieval_profiles", "owner")):
            if not _column_exists(conn, table, column):
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} VARCHAR"))
                conn.commit()
                log.info("added %s.%s column", table, column)
        if not _column_exists(conn, "owner_sessions", "cloud_ai_opt_in"):
            conn.execute(text("ALTER TABLE owner_sessions ADD COLUMN cloud_ai_opt_in BOOLEAN DEFAULT 0"))
            conn.commit()
        if not _column_exists(conn, "owner_sessions", "research_lookup_opt_in"):
            conn.execute(text("ALTER TABLE owner_sessions ADD COLUMN research_lookup_opt_in BOOLEAN DEFAULT 0"))
            conn.commit()

    db = SessionLocal()
    try:
        index_file = DATA_DIR / "index" / "store.pkl"
        if index_file.exists() and not index_file.read_bytes().startswith(b"ssblob:v1:"):
            index_file.unlink()
            log.info("removed legacy plaintext search index for encrypted rebuild")
        # Earlier Gemini embedding mode wrote a shared on-disk vector cache.
        legacy_index = Path(__file__).resolve().parents[2] / "data" / "index"
        for suffix in ("", "-wal", "-shm"):
            (legacy_index / ("emb_cache.db" + suffix)).unlink(missing_ok=True)
        _encrypt_legacy_metadata(db)
        papers = db.query(Paper).all()
        _hide_legacy_upload_filenames(db, papers)
        _refresh_extracted_metadata(db, papers)

        # 2. backfill fingerprints
        backfilled = 0
        for p in papers:
            if not p.dedup_key and p.file_path:
                from app.ingestion.fingerprint import fingerprint_pdf
                key = fingerprint_pdf(p.file_path)
                if key:
                    p.dedup_key = key
                    backfilled += 1
        if backfilled:
            db.commit()
            log.info("backfilled %d paper fingerprints", backfilled)

        # 3. year corroboration for already-processed papers (arXiv id wins when
        # the scholarly-database year disagrees by more than one year)
        from app.services.scholarly.aggregator import _arxiv_year
        fixed_year = 0
        for p in papers:
            ay = _arxiv_year(p.arxiv_id)
            if ay and p.year and abs(p.year - ay) > 1:
                p.year = ay
                fixed_year += 1
        if fixed_year:
            db.commit()
            log.info("corroborated year on %d papers from arXiv ids", fixed_year)

        # 4. crash reconciliation - papers left mid-pipeline by a dead process
        stuck = [p for p in papers if p.status in ("queued", "processing")]
        for p in stuck:
            p.status = "failed"
            p.error = ("Processing was interrupted (the server restarted). "
                       "Please re-upload this paper.")
            try:
                for s in (p.stages or []):
                    if s.get("state") in ("pending", "active"):
                        s["state"] = "failed"
                        s["detail"] = "interrupted"
            except Exception:
                pass
        if stuck:
            db.commit()
            log.info("marked %d interrupted papers as failed", len(stuck))

        # 5. expired cache eviction (rows are ignored after TTL but never deleted)
        n_cache = db.query(ApiCache).filter(ApiCache.expires_at < dt.datetime.utcnow()).delete()
        if n_cache:
            db.commit()
            log.info("evicted %d expired cache entries", n_cache)

        # Previous versions persisted global lookup results. Erase those old
        # rows once; subsequent writes are limited to owner-scoped answers.
        migration_marker = DATA_DIR / ".owner-cache-v3"
        if not migration_marker.exists():
            db.query(ApiCache).delete()
            db.commit()
            migration_marker.touch()

        # Give existing owners an expiry clock. The legacy shared owner must
        # never be adopted by the first anonymous visitor to a public deploy.
        for owner, latest in db.query(Paper.owner, func.max(Paper.created_at)) \
                .group_by(Paper.owner).all():
            if owner and db.get(OwnerSession, owner) is None:
                db.add(OwnerSession(owner=owner, last_seen=latest or dt.datetime.utcnow()))
        db.commit()

        # A crash could have interrupted PDF extraction before its finally
        # block. No plaintext scratch files may survive a restart.
        for path in UPLOAD_DIR.glob("*.plain"):
            path.unlink(missing_ok=True)
    finally:
        db.close()
