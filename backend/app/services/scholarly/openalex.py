"""OpenAlex client - the primary scholarly metadata provider (free, no key).

All calls return plain dicts or None; failures never raise into the pipeline.
"""
from __future__ import annotations

import re
import unicodedata

from app.services.scholarly.common import http_get_json, reconstruct_abstract

BASE = "https://api.openalex.org"

SELECT_WORKS = ",".join([
    "id", "doi", "title", "display_name", "publication_year", "cited_by_count",
    "authorships", "primary_location", "open_access", "topics", "abstract_inverted_index",
    "referenced_works", "type", "counts_by_year", "biblio",
])


def _work_url_id(openalex_id: str) -> str:
    if openalex_id.startswith("http"):
        return openalex_id
    return f"{BASE}/works/{openalex_id}"


def find_work_by_doi(doi: str) -> dict | None:
    doi = doi.strip().replace("https://doi.org/", "").replace("http://doi.org/", "")
    data = http_get_json(f"{BASE}/works/doi:{doi}", {"select": SELECT_WORKS})
    return data if data and data.get("id") else None


def find_work_by_title(title: str) -> tuple[dict, str] | None:
    """Title match with a confidence grade. A bare prefix match ("Exploration"
    → "Exploration and Exploitation in...") is weak - one-word titles used to
    grab the wrong paper - and is only accepted as such."""
    if not title or len(title) < 8:
        return None
    data = http_get_json(f"{BASE}/works", {
        "search": title[:300],
        "per-page": 25,
        "select": SELECT_WORKS,
    })
    results = (data or {}).get("results") or []
    if not results:
        return None
    t = _norm(title)
    scored = []
    for r in results:
        rn = _norm(r.get("display_name") or r.get("title") or "")
        score = _similarity(t, rn)
        if rn:
            scored.append((rn == t, score, r, rn))
    if not scored:
        return None
    exact, score, best, normalized = max(scored, key=lambda item: (item[0], item[1]))
    if exact:
        return best, "exact"
    if score >= 0.82:
        return best, "high"
    if score >= 0.6:
        return best, "medium"
    if normalized.startswith(t) and len(t) >= 8:
        return best, "weak"
    return None


def _norm(s: str) -> str:
    decomposed = unicodedata.normalize("NFKD", s.casefold())
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", plain)).strip()


def _similarity(a: str, b: str) -> float:
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    return 2 * len(ta & tb) / (len(ta) + len(tb))


def get_work(openalex_id: str) -> dict | None:
    return http_get_json(_work_url_id(openalex_id), {"select": SELECT_WORKS})


def get_citing_works(openalex_id: str, max_results: int = 120) -> list[dict]:
    """Works that cite this work, most-cited first."""
    out: list[dict] = []
    for page in range(1, 4):
        data = http_get_json(f"{BASE}/works", {
            "filter": f"cites:{openalex_id}",
            "sort": "cited_by_count:desc",
            "per-page": min(50, max_results),
            "page": page,
            "select": SELECT_WORKS,
        })
        results = (data or {}).get("results") or []
        out.extend(results)
        if len(results) < 50 or len(out) >= max_results:
            break
    return out[:max_results]


def get_works_batch(openalex_ids: list[str]) -> list[dict]:
    """Fetch up to ~50 works in one call via OR-filter."""
    if not openalex_ids:
        return []
    out: list[dict] = []
    for i in range(0, len(openalex_ids), 50):
        chunk = openalex_ids[i:i + 50]
        filt = "|".join(w.rsplit("/", 1)[-1] if "/" in w else w for w in chunk)
        data = http_get_json(f"{BASE}/works", {
            "filter": f"openalex_id:{filt}",
            "per-page": 50,
            "select": SELECT_WORKS,
        })
        out.extend((data or {}).get("results") or [])
    return out


def get_related_works(openalex_id: str, max_results: int = 12) -> list[dict]:
    data = http_get_json(f"{BASE}/works", {
        "filter": f"related_to:{openalex_id}",
        "per-page": max_results,
        "select": SELECT_WORKS,
    })
    return (data or {}).get("results") or []


def search_topic(query: str, max_results: int = 12) -> list[dict]:
    """Topic context for an unmatched document, never a citation relationship."""
    if not query or len(query) < 4:
        return []
    data = http_get_json(f"{BASE}/works", {
        "search": query[:150], "per-page": max_results, "select": SELECT_WORKS,
    })
    return (data or {}).get("results") or []


def get_authors_batch(openalex_ids: list[str]) -> list[dict]:
    if not openalex_ids:
        return []
    out: list[dict] = []
    for i in range(0, len(openalex_ids), 50):
        chunk = openalex_ids[i:i + 50]
        filt = "|".join(a.rsplit("/", 1)[-1] if "/" in a else a for a in chunk)
        data = http_get_json(f"{BASE}/authors", {
            "filter": f"openalex_id:{filt}",
            "per-page": 50,
        })
        out.extend((data or {}).get("results") or [])
    return out


def work_to_row(w: dict) -> dict:
    """Normalize an OpenAlex work into our citation-row shape."""
    loc = (w.get("primary_location") or {})
    source = loc.get("source") or {}
    return {
        "title": (w.get("display_name") or w.get("title") or "").strip(),
        "authors": [
            (a.get("author") or {}).get("display_name", "")
            for a in (w.get("authorships") or [])[:12]
        ],
        "year": w.get("publication_year"),
        "venue": source.get("display_name"),
        "doi": (w.get("doi") or "").replace("https://doi.org/", ""),
        "openalex_id": (w.get("id") or "").rsplit("/", 1)[-1],
        "cited_by_count": w.get("cited_by_count", 0),
        "institutions": sorted({
            (inst.get("display_name") or "")
            for a in (w.get("authorships") or [])
            for inst in (a.get("institutions") or [])
            if inst.get("display_name")
        })[:6],
        "abstract": reconstruct_abstract(w.get("abstract_inverted_index")),
        "topics": [t.get("display_name") for t in (w.get("topics") or [])[:3] if t.get("display_name")],
        "counts_by_year": w.get("counts_by_year") or [],
        "open_access_url": ((w.get("open_access") or {}).get("oa_url")),
    }
