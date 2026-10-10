"""arXiv API - preprints and recent developments on a topic."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import httpx

from app.config import settings
from app.services.scholarly.common import (cache_get, cache_set, make_key,
                                          SearchResult, record_lookup, track_lookups)

NS = {"a": "http://www.w3.org/2005/Atom"}
BASE = "https://export.arxiv.org/api/query"


def _params(base: dict) -> dict:
    p = dict(base)
    if settings.contact_email:
        p["mailto"] = settings.contact_email
    return p


def _fetch(params: dict) -> bytes | None:
    key = make_key("arxiv", sorted(params.items()))
    cached = cache_get("raw:" + key)
    if cached is not None:
        return bytes.fromhex(cached)
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True,
                          headers={"User-Agent": "ScholarScout/1.0"}) as client:
            r = client.get(BASE, params=_params(params))
            if r.status_code == 200:
                record_lookup("arXiv", "ok")
                cache_set("raw:" + key, r.content.hex(), hours=12)
                return r.content
            record_lookup("arXiv", "rate_limited" if r.status_code == 429 else "unavailable")
            return None
    except Exception:
        record_lookup("arXiv", "unavailable")
        return None


def search(query: str, max_results: int = 10, recent_first: bool = True) -> list[dict]:
    return search_with_status(query, max_results, recent_first).items


def search_with_status(query: str, max_results: int = 10,
                       recent_first: bool = True) -> SearchResult:
    q = " ".join(re.findall(r"[\w-]+", query))[:200].strip()
    if len(q) < 3:
        return SearchResult(provider="arXiv", status="insufficient_topic", query=q)
    params = {
        "search_query": " AND ".join(f"all:{term}" for term in q.split()[:6]),
        "max_results": max_results,
    }
    if recent_first:
        params["sortBy"] = "submittedDate"
        params["sortOrder"] = "descending"
    with track_lookups() as events:
        raw = _fetch(params)
    if not raw:
        status = events[-1]["status"] if events else "unavailable"
        return SearchResult(provider="arXiv", status=status, query=q,
                            note="The arXiv preprint service is temporarily unavailable.")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return SearchResult(provider="arXiv", status="unavailable", query=q,
                            note="The arXiv preprint service returned an unreadable response.")
    out = []
    for e in root.findall("a:entry", NS):
        title = (e.findtext("a:title", "", NS) or "").strip()
        if title.lower() == "error":
            return SearchResult(provider="arXiv", status="unavailable", query=q,
                                note="The arXiv preprint service rejected the topic query.")
        summary = (e.findtext("a:summary", "", NS) or "").strip()
        published = (e.findtext("a:published", "", NS) or "")[:10]
        link = ""
        for l in e.findall("a:link", NS):
            if l.get("type") == "application/pdf":
                link = l.get("href") or ""
        alt = (e.find("a:id", NS).text or "") if e.find("a:id", NS) is not None else ""
        out.append({
            "title": re.sub(r"\s+", " ", title),
            "abstract": re.sub(r"\s+", " ", summary)[:600],
            "published": published,
            "url": alt or link,
            "pdf_url": link,
        })
    items = [o for o in out if o["title"]][:max_results]
    return SearchResult(items=items, status="ok" if items else "no_results",
                        provider="arXiv", query=q)
