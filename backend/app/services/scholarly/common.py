"""Shared HTTP + caching helpers for external providers."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.database.database import SessionLocal
from app.models.entities import ApiCache, OwnerSession

UA = "ScholarScout/1.0 (research intelligence app)"
TIMEOUT = httpx.Timeout(20.0, connect=10.0)
_lookup_events = ContextVar("scholarly_lookup_events", default=None)
_lookup_deadline = ContextVar("scholarly_lookup_deadline", default=None)


@dataclass
class SearchResult:
    items: list = field(default_factory=list)
    status: str = "no_results"
    provider: str = ""
    note: str | None = None
    query: str | None = None


@contextmanager
def track_lookups():
    """Per-operation diagnostics, with no query or document text in server logs."""
    events = []
    parent = _lookup_events.get()
    token = _lookup_events.set(events)
    try:
        yield events
    finally:
        _lookup_events.reset(token)
        if parent is not None:
            parent.extend(events)


def record_lookup(provider: str, status: str, *, note: str | None = None,
                  http_status: int | None = None) -> None:
    events = _lookup_events.get()
    if events is not None:
        event = {"provider": provider, "status": status}
        if note:
            event["note"] = note
        if http_status is not None:
            event["http_status"] = http_status
        events.append(event)


@contextmanager
def lookup_budget(seconds: float):
    """Bound all provider calls in one operation, independently across threads."""
    token = _lookup_deadline.set(time.monotonic() + seconds)
    try:
        yield
    finally:
        _lookup_deadline.reset(token)


def _request_timeout() -> httpx.Timeout | None:
    deadline = _lookup_deadline.get()
    if deadline is None:
        return TIMEOUT
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None
    return httpx.Timeout(min(20.0, remaining), connect=min(10.0, remaining))


def _private_cache_key(key: str) -> bool:
    """Only owner-scoped answer and research caches may be persisted to disk."""
    return bool(re.match(r"^(?:synth|eli5::v4|research)::[0-9a-f]{32}::", key))


def _research_cache_allowed(db: Session, key: str) -> bool:
    from app.security.session import SessionEnded, live_owner
    owner = key.split("::")[2 if key.startswith("eli5::v4::") else 1]
    try:
        row = live_owner(db, owner)
    except SessionEnded:
        return False
    return bool(row.research_lookup_opt_in if key.startswith("research::") else row.cloud_ai_opt_in)


def cache_get(key: str):
    if not _private_cache_key(key):
        return None
    db = SessionLocal()
    try:
        if not _research_cache_allowed(db, key):
            return None
        row = db.get(ApiCache, key)
        if row and row.expires_at and row.expires_at > dt.datetime.utcnow():
            return row.payload
        return None
    finally:
        db.close()


def cache_set(key: str, payload, hours: float = 24) -> None:
    if not _private_cache_key(key):
        return
    db = SessionLocal()
    try:
        from app.security.session import SessionEnded, owner_write
        owner = key.split("::")[2 if key.startswith("eli5::v4::") else 1]
        try:
            with owner_write(db, owner):
                if not _research_cache_allowed(db, key):
                    return
                row = db.get(ApiCache, key)
                expires = dt.datetime.utcnow() + dt.timedelta(hours=hours)
                if row:
                    row.payload = payload
                    row.expires_at = expires
                else:
                    db.add(ApiCache(key=key, payload=payload, expires_at=expires))
        except SessionEnded:
            return
    finally:
        db.close()


def make_key(*parts) -> str:
    return hashlib.sha1("::".join(str(p) for p in parts).encode()).hexdigest()


def http_get_json(url: str, params: dict | None = None) -> dict | None:
    """GET with cache + timeout. Returns None on any failure (never raises)."""
    params = dict(params or {})
    if settings.contact_email and "mailto" not in params:
        params["mailto"] = settings.contact_email
    key = make_key("GET", url, json.dumps(params, sort_keys=True))
    cached = cache_get(key)
    if cached is not None:
        return cached
    is_openalex = urlparse(url).hostname == "api.openalex.org"
    provider = "OpenAlex" if is_openalex else "Crossref"
    timeout = _request_timeout()
    if timeout is None:
        record_lookup(provider, "unavailable", note=f"{provider} lookup budget was exhausted.")
        return None
    headers = {"User-Agent": UA}
    if is_openalex and settings.openalex_api_key:
        headers["Authorization"] = f"Bearer {settings.openalex_api_key}"
    try:
        with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
            r = client.get(url, params=params)
            if r.status_code == 200:
                data = r.json()
                record_lookup(provider, "ok")
                cache_set(key, data)
                return data
            note = (f"{provider} temporarily limited the request or daily API budget."
                    if r.status_code == 429 else
                    ("OpenAlex authentication was rejected; connected research needs a server configuration update."
                     if is_openalex and r.status_code in {401, 403} else
                     f"{provider} returned HTTP {r.status_code}; coverage could not be checked."))
            record_lookup(provider, "rate_limited" if r.status_code == 429 else "unavailable",
                          note=note, http_status=r.status_code)
            return None
    except Exception as error:
        note = (f"{provider} did not respond within the lookup timeout."
                if isinstance(error, httpx.TimeoutException) else
                f"{provider} could not complete the request.")
        record_lookup(provider, "unavailable", note=note)
        return None


def reconstruct_abstract(inverted: dict | None) -> str:
    """OpenAlex stores abstracts as an inverted word index."""
    if not inverted:
        return ""
    positions: list[tuple[int, str]] = []
    for word, idxs in inverted.items():
        for i in idxs:
            positions.append((i, word))
    positions.sort()
    return " ".join(w for _, w in positions)[:1200]
