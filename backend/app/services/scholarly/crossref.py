"""Crossref - metadata fallback when OpenAlex can't match a paper."""
from __future__ import annotations

from app.services.scholarly.common import http_get_json
from app.services.scholarly.openalex import _norm, _similarity

BASE = "https://api.crossref.org/works"


def find_by_title(title: str) -> dict | None:
    if not title or len(title) < 8:
        return None
    data = http_get_json(BASE, {"query.bibliographic": title[:300], "rows": 3})
    items = ((data or {}).get("message") or {}).get("items") or []
    if not items:
        return None
    it = max(items, key=lambda item: (
        _norm((item.get("title") or [""])[0]) == _norm(title),
        _similarity(_norm((item.get("title") or [""])[0]), _norm(title))))
    title_field = (it.get("title") or [""])[0]
    if not title_field:
        return None
    year = None
    for date_key in ("published-print", "published-online", "issued", "created"):
        parts = ((it.get(date_key) or {}).get("date-parts") or [[None]])
        if parts and parts[0] and parts[0][0]:
            year = parts[0][0]
            break
    authors = [
        f"{a.get('given', '').strip()} {a.get('family', '').strip()}".strip()
        for a in (it.get("author") or [])[:12]
    ]
    return {
        "title": title_field.strip(),
        "authors": [a for a in authors if a],
        "year": year,
        "venue": (it.get("container-title") or [""])[0] or None,
        "doi": it.get("DOI"),
        "cited_by_count": it.get("is-referenced-by-count", 0),
        "institutions": [
            (a.get("affiliation") or [{}])[0].get("name")
            for a in (it.get("author") or []) if (a.get("affiliation") or [{}])[0].get("name")
        ][:6],
    }
