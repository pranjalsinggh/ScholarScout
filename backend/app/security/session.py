"""Durable anonymous session lifecycle and serialized owner-scoped erasure."""
from __future__ import annotations

from contextlib import contextmanager
import datetime as dt
import hashlib
from pathlib import Path
import re
import shutil

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import UPLOAD_DIR, settings
from app.database.database import SessionLocal
from app.models.entities import (ApiCache, ChatMessage, EndedSession, OwnerSession,
                                 Paper, ProviderUsage, RetrievalProfile, WatchItem)


class SessionEnded(Exception):
    pass


class SessionCapacityError(Exception):
    pass


def is_ended(owner: str) -> bool:
    """Revocation survives process restarts and is shared by every worker."""
    with SessionLocal() as db:
        return db.get(EndedSession, owner) is not None


def live_owner(db: Session, owner: str) -> OwnerSession:
    row = db.get(OwnerSession, owner)
    if (not row or db.get(EndedSession, owner) is not None or
            row.last_seen < dt.datetime.utcnow() - dt.timedelta(hours=settings.retention_hours)):
        raise SessionEnded("This research session has ended.")
    return row


@contextmanager
def owner_write(db: Session, owner: str, *, require_live: bool = True):
    """Commit a short write transaction, serialized against purge.

    Enter BEFORE changing/adding rows. An existing read transaction is rolled
    back so validation sees latest revocation, not a stale ORM snapshot.
    SQLite write locks and PostgreSQL transaction advisory locks also protect
    session admission quotas across processes. Keep remote calls outside.
    """
    if db.new or db.dirty or db.deleted:
        raise RuntimeError("Enter owner_write before changing rows")
    db.rollback()
    try:
        dialect = db.get_bind().dialect.name
        if dialect == "sqlite":
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        elif dialect == "postgresql":
            lock_key = int.from_bytes(hashlib.sha256(b"scholarscout-lifecycle").digest()[:8], "big", signed=True)
            db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
        else:
            raise RuntimeError("Session isolation requires SQLite or PostgreSQL")
        db.expire_all()
        if require_live:
            live_owner(db, owner)
        yield
        db.commit()
    except BaseException:
        db.rollback()
        raise


def _revoke(db: Session, owner: str) -> None:
    if db.get(EndedSession, owner) is None:
        db.add(EndedSession(owner=owner))


def end_owner(owner: str) -> None:
    """Compatibility helper; normal erasure revokes atomically in purge_owner."""
    with SessionLocal() as db:
        with owner_write(db, owner, require_live=False):
            if db.get(OwnerSession, owner) is not None:
                _revoke(db, owner)


def cloud_ai_allowed(owner: str) -> bool:
    with SessionLocal() as db:
        try:
            return bool(live_owner(db, owner).cloud_ai_opt_in)
        except SessionEnded:
            return False


def research_lookup_allowed(owner: str) -> bool:
    with SessionLocal() as db:
        try:
            return bool(live_owner(db, owner).research_lookup_opt_in)
        except SessionEnded:
            return False


def _remove_content(db: Session, owner: str):
    """Caller holds the lifecycle transaction; erase files and derived rows."""
    from app.services.embeddings import emb_hash
    from app.services.scoped_index import forget_owner
    from app.services.jobs import cancel_owner_jobs

    cancel_owner_jobs(owner)
    papers = db.query(Paper).filter(Paper.owner == owner).all()
    hashes = [emb_hash(c.text or "") for paper in papers for c in paper.chunks]
    for paper in papers:
        if paper.file_path:
            path = Path(paper.file_path).resolve()
            if path.is_relative_to(UPLOAD_DIR.resolve()):
                path.unlink(missing_ok=True)
                Path(str(path) + ".plain").unlink(missing_ok=True)
        db.delete(paper)
    for path in UPLOAD_DIR.glob(f"parse-{owner}-*"):
        resolved = path.resolve()
        if resolved.is_relative_to(UPLOAD_DIR.resolve()) and resolved.is_dir():
            shutil.rmtree(resolved)
    counts = {
        "papers_removed": len(papers),
        "chat_messages_removed": db.query(ChatMessage).filter(ChatMessage.owner == owner).delete(),
        "watchlist_removed": db.query(WatchItem).filter(WatchItem.owner == owner).delete(),
    }
    db.query(RetrievalProfile).filter(RetrievalProfile.owner == owner).delete()
    if papers:
        db.query(RetrievalProfile).filter(RetrievalProfile.paper_id.in_([paper.id for paper in papers])).delete(synchronize_session=False)
    counts["cached_answers_removed"] = db.query(ApiCache).filter(
        ApiCache.key.like(f"synth::{owner}::%") | ApiCache.key.like(f"eli5::v4::{owner}::%")
    ).delete(synchronize_session=False)
    counts["cached_research_removed"] = db.query(ApiCache).filter(
        ApiCache.key.like(f"research::{owner}::%")
    ).delete(synchronize_session=False)
    counts["provider_usage_removed"] = db.query(ProviderUsage).filter(ProviderUsage.owner == owner).delete()
    db.query(OwnerSession).filter(OwnerSession.owner == owner).delete()
    forget_owner(owner)
    return counts, hashes


def _finish_erasure(counts, hashes, *, rebuild: bool):
    from app.services.embeddings import purge_embedding_cache_by_hash
    counts["embedding_vectors_removed"] = purge_embedding_cache_by_hash(hashes)
    if rebuild and counts["papers_removed"]:
        from app.services.vector_store import store
        store.rebuild()
    return counts


def purge_owner(db: Session, owner: str, *, rebuild: bool = True) -> dict[str, int]:
    """Revoke and erase atomically. Unknown-token purge is idempotent.

    Tombstones hold only an irreversible hash and timestamp. Unknown tokens
    never receive tombstones, avoiding anonymous database growth.
    """
    with owner_write(db, owner, require_live=False):
        known = db.get(OwnerSession, owner) is not None
        counts, hashes = _remove_content(db, owner)
        if known or any(counts.values()):
            _revoke(db, owner)
    return _finish_erasure(counts, hashes, rebuild=rebuild)


def touch_session(owner: str) -> None:
    """Create/extend a live session. Expired and revoked browser keys stay ended."""
    expired = None
    with SessionLocal() as db:
        with owner_write(db, owner, require_live=False):
            if db.get(EndedSession, owner) is not None:
                raise SessionEnded
            now = dt.datetime.utcnow()
            row = db.get(OwnerSession, owner)
            if row and row.last_seen < now - dt.timedelta(hours=settings.retention_hours):
                expired = _remove_content(db, owner)
                _revoke(db, owner)
            elif row is None:
                if db.query(OwnerSession).count() >= settings.max_active_sessions:
                    raise SessionCapacityError("The service is at its session capacity.")
                db.add(OwnerSession(owner=owner, last_seen=now))
            else:
                row.last_seen = now
    if expired:
        _finish_erasure(*expired, rebuild=True)
        raise SessionEnded


def expire_abandoned_sessions() -> int:
    """Expire idle sessions and sweep old orphan artifacts without a clock."""
    cutoff = dt.datetime.utcnow() - dt.timedelta(hours=settings.retention_hours)
    with SessionLocal() as db:
        sessions = {row.owner: row.last_seen for row in db.query(OwnerSession).all()}
        candidates = {owner for owner, seen in sessions.items() if seen < cutoff}
        for model in (Paper, ChatMessage, WatchItem, RetrievalProfile, ProviderUsage):
            candidates.update(owner for (owner,) in db.query(model.owner).distinct().all()
                              if owner and owner not in sessions)
        for (key,) in db.query(ApiCache.key).all():
            match = re.match(r"^(?:synth|eli5::v4|research)::([0-9a-f]{32})::", key)
            if match and match[1] not in sessions:
                candidates.add(match[1])
    removed = 0
    for owner in candidates:
        with SessionLocal() as db:
            with owner_write(db, owner, require_live=False):
                row = db.get(OwnerSession, owner)
                if row and row.last_seen >= cutoff and db.get(EndedSession, owner) is None:
                    continue
                counts, hashes = _remove_content(db, owner)
                _revoke(db, owner)
            _finish_erasure(counts, hashes, rebuild=False)
            removed += 1
    if removed:
        from app.services.vector_store import store
        store.rebuild()
    return removed
