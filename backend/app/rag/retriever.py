"""Hybrid retrieval engine.

Combines, per candidate passage:
  - dense similarity  (sentence-transformers / TF-IDF vector store)
  - BM25 lexical score (exact term strength, robust when embeddings are weak)
  - static section priors (abstract/introduction summarize the paper)
  - feedback-learned adjustments from the paper's RetrievalProfile
      (sections & terms that produced well-rated answers)

Candidate fusion → min-max normalization → weighted sum → MMR selection for
diversity. Robust with either embedding backend: when the dense model is weak
the BM25 leg carries the ranking, and vice versa.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.database.database import SessionLocal
from app.models.entities import Chunk, Paper
from app.services.analysis.feedback import learned_adjustments, _terms
from app.services.vector_store import store

W_DENSE, W_LEX = 0.55, 0.30
W_SECTION, W_LEARNED_SEC, W_LEARNED_TERMS = 0.10, 0.12, 0.06
MMR_LAMBDA = 0.72
FLOOR = 0.08  # on the fused 0..1 score

PRIOR_SECTIONS = {"abstract": 0.06, "introduction": 0.03, "conclusion": 0.01}


@dataclass
class Retrieved:
    chunk_id: int
    paper_id: str
    paper_title: str
    section: str | None
    page: int | None
    text: str
    score: float


def _norm(values: dict[int, float]) -> dict[int, float]:
    if not values:
        return {}
    lo, hi = min(values.values()), max(values.values())
    if hi - lo < 1e-9:
        return {k: 1.0 for k in values}
    return {k: (v - lo) / (hi - lo) for k, v in values.items()}


def retrieve(query: str, k: int | None = None, paper_ids: list[str] | None = None) -> list[Retrieved]:
    from app.config import settings
    k = k or settings.retrieval_top_k

    scoped = None
    if paper_ids:
        from app.services.scoped_index import for_papers
        scoped = for_papers(paper_ids)
        dense_hits = scoped.dense(query, max(k * 4, 16), paper_ids) if scoped else []
        lex_hits = scoped.lexical(query, max(k * 4, 16), paper_ids) if scoped else []
    else:
        store.rebuild_if_needed()
        dense_hits = store.search_chunks(query, k=max(k * 4, 16), paper_ids=paper_ids)
        lex_hits = store.search_lexical(query, k=max(k * 4, 16), paper_ids=paper_ids)
    dense = {cid: s for cid, pid, s in dense_hits}
    lex = {cid: s for cid, pid, s in lex_hits}

    if not dense and not lex:
        return []

    paper_of: dict[int, str] = {}
    for cid, pid, _ in dense_hits:
        paper_of[cid] = pid
    for cid, pid, _ in lex_hits:
        paper_of.setdefault(cid, pid)

    dn, ln = _norm(dense), _norm(lex)
    candidates = set(dense) | set(lex)

    # feedback-learned profile of the primary paper
    primary_paper = paper_ids[0] if paper_ids else (
        paper_of.get(next(iter(candidates))) if candidates else None)
    prof_sections: dict[str, float] = {}
    prof_terms: dict[str, float] = {}
    if primary_paper:
        prof_sections, prof_terms = learned_adjustments(primary_paper)
    qterms = _terms(query)

    fused: dict[int, float] = {}
    for cid in candidates:
        score = W_DENSE * dn.get(cid, 0.0) + W_LEX * ln.get(cid, 0.0)
        paper_id = paper_of.get(cid, "")
        fused[cid] = score

    # fetch rows for sections/text (single query)
    rows = _load_rows(list(candidates))
    scored: list[tuple[float, int, str, Retrieved]] = []
    for cid, (text, section, page, title, pid) in rows.items():
        base = fused.get(cid, 0.0)
        sec_key = (section or "unknown").lower()
        score = base + W_SECTION * PRIOR_SECTIONS.get(sec_key, 0.0)
        score += W_LEARNED_SEC * prof_sections.get(sec_key, 0.0)
        if qterms and prof_terms:
            hits = [prof_terms[t] for t in qterms if t in prof_terms and t in text.lower()]
            if hits:
                score += W_LEARNED_TERMS * (sum(hits) / len(hits))
        scored.append((round(score, 4), cid, pid, Retrieved(
            chunk_id=cid, paper_id=pid, paper_title=title,
            section=section, page=page, text=text, score=round(score, 4))))

    scored.sort(key=lambda x: -x[0])
    pool = [s for s in scored if s[0] >= FLOOR][: max(k * 3, 12)] or scored[:k]

    # MMR - relevance balanced with diversity over dense vectors,
    # plus a hard cap of 2 passages per section for evidence variety
    selected: list[tuple[float, int, str, Retrieved]] = []
    section_count: dict[str, int] = {}
    for item in pool:
        if len(selected) >= k:
            break
        _, cid, _, ret = item
        sec_key = (ret.section or "unknown").lower()
        if section_count.get(sec_key, 0) >= 2:
            continue
        v = None if scoped else store.vector_by_chunk(cid)
        max_sim = 0.0
        if scoped:
            max_sim = max((scoped.similarity(cid, sid) for _, sid, _, _ in selected), default=0.0)
        elif v is not None:
            for _, sid, _, _ in selected:
                sv = store.vector_by_chunk(sid)
                if sv is not None:
                    max_sim = max(max_sim, float(v @ sv))
        if max_sim < 0.92:
            selected.append(item)
            section_count[sec_key] = section_count.get(sec_key, 0) + 1
    if not selected:  # everything was near-duplicate → take the best
        selected = pool[:1]
    return [r for _, _, _, r in selected[:k]]


def _load_rows(chunk_ids: list[int]) -> dict[int, tuple[str, str | None, int | None, str, str]]:
    if not chunk_ids:
        return {}
    db = SessionLocal()
    try:
        rows = (
            db.query(Chunk.id, Chunk.text, Chunk.section_name, Chunk.page, Paper.title, Paper.id)
            .join(Paper, Chunk.paper_id == Paper.id)
            .filter(Chunk.id.in_(chunk_ids), Paper.status == "ready")
            .all()
        )
        # strip the chunker's "[section]" tag from the display text - it is a
        # retrieval hint, not content the user should see in evidence cards
        return {
            r[0]: (re.sub(r"^\[[^\]]+\]\s*", "", r[1] or ""), r[2], r[3], r[4], r[5])
            for r in rows
        }
    finally:
        db.close()
