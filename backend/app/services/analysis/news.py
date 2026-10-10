"""Verified recent academic work and news, with explicit provider availability."""
from __future__ import annotations

import datetime as dt
import re
import threading
import time

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models.entities import Citation, Paper
from app.schemas.api import NewsItem, NewsResponse, ResearchSourceStatus
from app.services.analysis.distinctive import paper_topic_queries, topic_relevance_filter
from app.services.scholarly.common import SearchResult, cache_get, cache_set, make_key

_gdelt_lock = threading.Lock()
_gdelt_last_call = 0.0
_FAILURES = {"unavailable", "rate_limited"}
_PROMOTION_HEADLINE = re.compile(
    r"\b(?:pypi|npm|package release|lego|building kit|coupon|discount|"
    r"holodeck|solara one|video game\w*|vr game\w*|gaming|"
    r"securities fraud|class action|legal alert)\b|"
    r"\b(?:amazon|walmart|target)\b.*\$\d", re.I)


def _academic_from_citations(db: Session, paper: Paper) -> list[NewsItem]:
    rows = db.query(Citation).filter(Citation.paper_id == paper.id,
                                    Citation.direction == "citing") \
        .order_by(Citation.year.desc(), Citation.cited_by_count.desc()).limit(30).all()
    recent_year = dt.datetime.now(dt.timezone.utc).year - 5
    out = []
    for c in rows:
        if not c.title or not c.year or c.year < recent_year:
            continue
        connection = ("Replication/reproduction study citing this paper" if c.is_replication else
                      "Uses challenge language toward this area" if c.is_contradictory else
                      "Extends this work" if c.is_extension else "Cites this paper")
        url = f"https://doi.org/{c.doi}" if c.doi else (
            f"https://openalex.org/{c.openalex_id}" if c.openalex_id else None)
        out.append(NewsItem(headline=c.title, date=str(c.year),
                            publisher=c.venue or "OpenAlex", summary=(c.snippet or "")[:300],
                            connection=connection, url=url, category="academic"))
    return out[:10]


def _relevant(title: str, queries: list[str], summary: str = "") -> bool:
    text = re.sub(r"[^a-z0-9]+", " ", (title + " " + summary).lower())
    tokens = set(text.split())
    for query in queries:
        normalized = re.sub(r"[^a-z0-9]+", " ", query.lower()).strip()
        if normalized and normalized in text:
            return True
        words = set(normalized.split())
        if len(words) >= 2 and len(words & tokens) >= 2:
            return True
    return False


def _news_headline_relevant(title: str, queries: list[str], topic_filter=None) -> bool:
    """Research context requires a topical headline, excluding promotions."""
    if _PROMOTION_HEADLINE.search(title):
        return False
    return bool(topic_filter(title)) if topic_filter else _relevant(title, queries)


def _arxiv_recent(paper: Paper, queries: list[str], ensure_allowed=lambda: None,
                  topic_filter=None) -> SearchResult:
    from app.services.scholarly import arxiv
    topic_filter = topic_filter or topic_relevance_filter(paper)
    recent_year = dt.datetime.now(dt.timezone.utc).year - 5
    after_year = max(paper.year or 0, recent_year)
    result = SearchResult(provider="arXiv", status="no_results")
    for query in queries[:2]:
        ensure_allowed()
        result = arxiv.search_with_status(query, max_results=10, recent_first=True)
        if result.status in _FAILURES:
            return result
        items = []
        for hit in result.items:
            published = hit.get("published") or ""
            year = int(published[:4]) if published[:4].isdigit() else 0
            if year < after_year or not _relevant(hit.get("title", ""), queries,
                                                 hit.get("abstract", "")):
                continue
            if topic_filter and not topic_filter(hit.get("title", ""), hit.get("abstract", "")):
                continue
            items.append(NewsItem(headline=hit["title"], date=published,
                                  publisher="arXiv preprint", summary=hit.get("abstract", "")[:280],
                                  connection="Recent preprint on this document's topic; not necessarily a citing work",
                                  url=hit.get("url"), category="academic"))
        if items:
            return SearchResult(items=items[:6], provider="arXiv", status="ok", query=query)
    return SearchResult(provider="arXiv", status="no_results", query=result.query,
                        note="No relevant recent preprints were returned for these document topics.")


def _throttle_gdelt() -> None:
    global _gdelt_last_call
    with _gdelt_lock:
        wait = 6.5 - (time.monotonic() - _gdelt_last_call)
        if wait > 0:
            time.sleep(wait)
        _gdelt_last_call = time.monotonic()


def _gdelt_news(queries: list[str], ensure_allowed=lambda: None, topic_filter=None) -> SearchResult:
    queries = [re.sub(r"[^\w\s-]", " ", q).strip()[:75] for q in queries if q][:3]
    if not queries:
        return SearchResult(provider="GDELT", status="insufficient_topic",
                            note="There is not enough readable topic text to search for news.")
    # Alternative grounded concepts give recall without requiring a headline
    # to contain every independently ranked word from a long academic title.
    query = "(" + " OR ".join(f'"{q}"' for q in queries) + ") sourcelang:eng"
    for attempt in range(2):
        _throttle_gdelt()
        ensure_allowed()
        try:
            response = httpx.get("https://api.gdeltproject.org/api/v2/doc/doc", params={
                "query": query, "mode": "artlist", "maxrecords": 30,
                "format": "json", "sort": "hybridrel", "timespan": "3m",
            }, timeout=httpx.Timeout(12, connect=5))
            if response.status_code == 429:
                return SearchResult(provider="GDELT", status="rate_limited", query=query,
                                    note="The news index is temporarily rate limited. Please retry later.")
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get("articles", []), list):
                raise ValueError("unreadable news response")
        except Exception:
            return SearchResult(provider="GDELT", status="unavailable", query=query,
                                note="The news index is temporarily unavailable. Coverage could not be checked.")
        items, seen_urls = [], set()
        for article in data.get("articles", []):
            title = (article.get("title") or "").strip()
            url = article.get("url") or ""
            if not title or not url.startswith(("http://", "https://")) or url in seen_urls:
                continue
            if not _relevant(title, queries):
                continue
            if not _news_headline_relevant(title, queries, topic_filter):
                continue
            seen_urls.add(url)
            date = article.get("seendate") or ""
            items.append(NewsItem(headline=title,
                                  date=f"{date[:4]}-{date[4:6]}-{date[6:8]}" if len(date) >= 8 else None,
                                  publisher=article.get("domain"), url=url, category="news",
                                  connection="Recent English news on a topic identified in this document (GDELT)"))
        if items:
            return SearchResult(items=items[:12], provider="GDELT", status="ok", query=query)
        # A bounded relaxed attempt allows inflected/intervening words while
        # the returned title still passes the same relevance test.
        if attempt == 0 and any(" " in q for q in queries):
            query = "(" + " OR ".join("(" + " ".join(q.split()[:3]) + ")"
                                       for q in queries) + ") sourcelang:eng"
        else:
            break
    return SearchResult(provider="GDELT", status="no_results", query=query,
                        note="No relevant English news was returned for these topics in the last three months.")


def _real_news(queries: list[str], ensure_allowed=lambda: None,
               topic_filter=None) -> tuple[SearchResult, list[SearchResult]]:
    attempts = []
    if settings.newsapi_key and queries:
        ensure_allowed()
        query = " OR ".join(f'"{q}"' for q in queries)[:180]
        try:
            response = httpx.get("https://newsapi.org/v2/everything", params={
                "q": query, "pageSize": 20, "language": "en", "sortBy": "publishedAt",
                "apiKey": settings.newsapi_key,
            }, timeout=httpx.Timeout(12, connect=5))
            response.raise_for_status()
            data = response.json()
            if data.get("status") != "ok":
                raise ValueError("news provider rejected request")
            items = []
            for article in data.get("articles") or []:
                title, summary = article.get("title") or "", article.get("description") or ""
                url = article.get("url") or ""
                if (title and url.startswith(("http://", "https://"))
                        and _relevant(title, queries, summary)
                        and _news_headline_relevant(title, queries, topic_filter)):
                    items.append(NewsItem(headline=title, date=(article.get("publishedAt") or "")[:10],
                                          publisher=(article.get("source") or {}).get("name"),
                                          summary=summary[:280], url=url, category="news",
                                          connection="News on a topic identified in this document (NewsAPI)"))
            result = SearchResult(items=items[:8], provider="NewsAPI",
                                  status="ok" if items else "no_results", query=query)
        except Exception:
            result = SearchResult(provider="NewsAPI", status="unavailable", query=query,
                                  note="NewsAPI could not complete the lookup; trying the news index.")
        attempts.append(result)
        if result.items:
            return result, attempts
    result = _gdelt_news(queries, ensure_allowed, topic_filter)
    attempts.append(result)
    return result, attempts


def get_news(db: Session, paper: Paper) -> NewsResponse:
    from app.security.session import research_lookup_allowed

    def ensure_allowed():
        if not research_lookup_allowed(paper.owner):
            raise PermissionError("Connected research lookups are off for this session.")

    try:
        ensure_allowed()
    except PermissionError as error:
        return NewsResponse(academic=[], news=[], commentary=[], status="disabled", note=str(error),
                            sources=[ResearchSourceStatus(provider="Connected research", status="disabled")])
    queries = paper_topic_queries(paper, db)
    topic_filter = topic_relevance_filter(paper, db)
    news_topic_filter = topic_relevance_filter(paper, db, headline=True)
    key = f"research::{paper.owner}::{make_key('news:v6', paper.id, queries)}"
    cached = cache_get(key)
    if cached:
        return NewsResponse(**cached)
    academic = _academic_from_citations(db, paper)
    if not queries:
        return NewsResponse(academic=academic, news=[], commentary=[], status="insufficient_topic",
                            note="No reliable topic could be identified for a news lookup. The document analysis is still available.")
    try:
        preprints = _arxiv_recent(paper, queries, ensure_allowed, topic_filter)
        headlines, attempts = _real_news(queries, ensure_allowed, news_topic_filter)
        ensure_allowed()
    except PermissionError as error:
        return NewsResponse(academic=[], news=[], commentary=[], status="disabled", note=str(error))
    academic += preprints.items
    sources = [ResearchSourceStatus(provider=result.provider, status=result.status,
                                     note=result.note, query=result.query)
               for result in [preprints, *attempts]]
    citation_items = _academic_from_citations(db, paper)
    ecosystem_status = (paper.meta or {}).get("ecosystem_status")
    citation_status = ("partial" if citation_items and ecosystem_status in {"partial", "unavailable"}
                       else "ok" if citation_items else "unavailable"
                       if ecosystem_status in {"partial", "unavailable"} or not paper.ecosystem_ok
                       else "no_results")
    sources.insert(0, ResearchSourceStatus(provider="OpenAlex citations",
                   status=citation_status,
                   note=paper.ecosystem_note if not paper.openalex_id else
                   (paper.ecosystem_note or "Recent works citing the verified research record.")))
    failure = (ecosystem_status in {"partial", "unavailable"} or citation_status in _FAILURES
               or any(result.status in _FAILURES for result in [preprints, *attempts]))
    any_items = bool(academic or headlines.items)
    status = "partial" if any_items and failure else "ok" if any_items else "unavailable" if failure else "empty"
    if failure:
        note = "Some research services could not complete their lookups. Available verified results are shown; retry later for missing sources."
    elif any_items:
        note = "Academic items and news are shown separately. Topic matches are context, and are not proof of a citation or endorsement."
    else:
        note = "The connected services returned no relevant recent coverage for these topics. This does not affect the analysis of the uploaded document."
    result = NewsResponse(academic=academic, news=headlines.items, commentary=[], status=status,
                          sources=sources, note=note)
    # Cache is encrypted, isolated to this anonymous owner and removed by erase.
    # Brief failure caching limits storms; successful results remain stable in a tab.
    ensure_allowed()
    cache_set(key, result.model_dump(), hours=1 / 120 if failure else 1)
    return result
