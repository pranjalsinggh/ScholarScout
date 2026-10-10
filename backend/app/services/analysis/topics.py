"""Topic discovery (KMeans over paper vectors) + research trends over time.

Uses the same embeddings as search, so no extra model downloads. Works with a
single paper too (section-level clustering fallback).
"""
from __future__ import annotations

import re
from collections import Counter

import numpy as np
from sklearn.preprocessing import normalize
from sklearn.cluster import KMeans
from sklearn.feature_extraction.text import TfidfVectorizer
from sqlalchemy.orm import Session

from app.models.entities import Chunk, Citation, Paper
from app.schemas.api import TopicCluster, TopicsResponse, TrendPoint, TrendSeries, TrendsResponse
from app.services.vector_store import store

STOP = set("""the a an of in on for to with and or is are was were be we our their its this that these
using based approach method methods paper model models results study propose proposed novel which
from by as at it can performance data learning neural deep""".split())


def _label_from_terms(terms: list[str]) -> str:
    return " · ".join(t.title() for t in terms[:3]) if terms else "Topic"


def build_topics(db: Session, owner: str = "") -> TopicsResponse:
    papers = db.query(Paper).filter(Paper.status == "ready")
    if owner:
        papers = papers.filter(Paper.owner == owner)
    papers = papers.all()
    if not papers:
        return TopicsResponse(topics=[], backend="n/a", note="No processed papers yet.")

    if len(papers) >= 3:
        from app.services import scoped_index
        scope = owner or papers[0].owner
        pm = scoped_index.paper_matrix(scope)
        if pm:
            all_ids, all_vecs = pm
            allowed = {paper.id for paper in papers}
            positions = [i for i, pid in enumerate(all_ids) if pid in allowed]
            ids = [all_ids[i] for i in positions]
            vecs = all_vecs[positions]
        else:
            ids, vecs = [], None
        if len(ids) >= 3:
            k = min(6, len(ids) - 1) if len(ids) > 6 else 2
            km = KMeans(n_clusters=k, n_init=10, random_state=42)
            labels = km.fit_predict(vecs)
            by_cluster: dict[int, list[str]] = {}
            for pid, lab in zip(ids, labels):
                by_cluster.setdefault(int(lab), []).append(pid)
            topics = []
            for lab, pids in sorted(by_cluster.items(), key=lambda x: -len(x[1])):
                texts = [p.title + " " + (p.abstract or "")[:500] for p in papers if p.id in pids]
                terms = _top_terms(texts)
                topics.append(TopicCluster(
                    label=_label_from_terms(terms), terms=terms,
                    share=round(100 * len(pids) / len(ids), 1), paper_ids=pids,
                ))
            return TopicsResponse(topics=topics, backend=store.backend,
                                  note="Clusters computed over paper-level embedding centroids.")

    # single/few papers: cluster chunks of the primary paper
    paper = papers[0]
    chunks = db.query(Chunk).filter(Chunk.paper_id == paper.id).all()
    if len(chunks) < 4:
        return TopicsResponse(topics=[], backend=store.backend, note="Not enough content for topic discovery.")
    texts = [c.text for c in chunks]
    # Fit only this document, using sparse rows. Never mutate a shared encoder
    # or densify a long document's vocabulary into a large feature matrix.
    from app.services.embeddings import TfidfEmbeddings
    emb = TfidfEmbeddings()
    vecs = normalize(emb.union.fit_transform(texts), norm="l2").astype("float32")
    k = min(5, max(2, len(chunks) // 10))
    km = KMeans(n_clusters=k, n_init=10, random_state=42)
    labels = km.fit_predict(vecs)
    topics = []
    for lab in sorted(set(labels)):
        idx = [i for i, l in enumerate(labels) if l == lab]
        section_texts = [chunks[i].text for i in idx]
        terms = _top_terms(section_texts)
        topics.append(TopicCluster(
            label=_label_from_terms(terms), terms=terms,
            share=round(100 * len(idx) / len(chunks), 1), paper_ids=[paper.id] * 1,
        ))
    return TopicsResponse(topics=sorted(topics, key=lambda t: -t.share)[:6], backend=store.backend,
                          note=f"Section-level topics for “{paper.title[:80]}”.")


def _top_terms(texts: list[str], n: int = 6) -> list[str]:
    try:
        tf = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), max_features=4000)
        m = tf.fit_transform(texts)
        sums = np.asarray(m.sum(axis=0)).ravel()
        idx = np.argsort(-sums)[:n]
        feats = np.array(tf.get_feature_names_out())
        return [re.sub(r"\s+", " ", f) for f in feats[idx]]
    except Exception:
        return []


# ------------------------------------------------------------------ trends --
def build_trends(db: Session, owner: str = "") -> TrendsResponse:
    """Trends from actual publication years of the citation ecosystem + uploads."""
    papers = db.query(Paper).filter(Paper.status == "ready")
    if owner:
        papers = papers.filter(Paper.owner == owner)
    papers = papers.all()
    series_map: dict[str, Counter] = {}

    # topic series: papers' own metadata topics + citation years by topic keyword
    topic_keywords: dict[str, str] = {}
    for p in papers:
        for t in ((p.meta or {}).get("topics") or [])[:3]:
            topic_keywords[t] = t

    # count citation years per keyword across the ecosystem
    citing = []
    for p in papers:
        citing.extend(db.query(Citation).filter(Citation.paper_id == p.id).all())

    series_map["This paper's citation activity"] = Counter(
        c.year for c in citing if c.year and 2000 <= c.year <= 2030)

    # keyword-level series across citing titles
    kw_counter: dict[str, Counter] = {}
    known_kw = sorted({m for p in papers for m in ((p.meta or {}).get("methods") or [])[:8]}, key=str.lower)[:8]
    for c in citing:
        if not c.year or not (2000 <= c.year <= 2030) or not c.title:
            continue
        t = c.title.lower()
        for kw in known_kw:
            if kw.lower() in t:
                kw_counter.setdefault(kw.title(), Counter())[c.year] += 1

    series: list[TrendSeries] = []
    main = series_map.get("This paper's citation activity", Counter())
    if main:
        series.append(_series("This paper's citation activity", main))

    for kw, cnt in sorted(kw_counter.items(), key=lambda x: -sum(x[1].values()))[:5]:
        series.append(_series(kw, cnt))

    return TrendsResponse(
        series=series[:6],
        note="Computed from actual publication years of indexed works. Past frequency does not "
             "guarantee future importance.",
    )


def _series(topic: str, counter: Counter) -> TrendSeries:
    years = sorted(y for y in counter if y)
    if not years:
        return TrendSeries(topic=topic, status="established", points=[])
    lo, hi = min(years), max(years)
    points = [TrendPoint(year=y, count=counter.get(y, 0)) for y in range(lo, hi + 1)]
    # status from simple slope of the last 3 points vs earlier mean
    recent = [p.count for p in points[-3:]]
    earlier = [p.count for p in points[:-3]] or [0]
    mean_recent = sum(recent) / max(len(recent), 1)
    mean_earlier = sum(earlier) / max(len(earlier), 1)
    if mean_recent > mean_earlier * 1.3:
        status = "growing" if mean_earlier > 0 else "emerging"
    elif mean_recent < mean_earlier * 0.6 and mean_earlier > 0:
        status = "declining"
    else:
        status = "established"
    return TrendSeries(topic=topic, status=status, points=points)
