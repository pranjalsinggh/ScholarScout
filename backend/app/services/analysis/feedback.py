"""Feedback-driven retrieval learning.

Users rate chat answers with 👍/👎. Each rating nudges the paper's
RetrievalProfile:
  - the sections that supplied the evidence gain/lose weight
  - query terms shared with the evidence gain/lose weight
All weights decay on every update, so the profile tracks recent feedback and
can't grow unbounded. Retrieval reads this profile and adjusts scores.

The mechanism is deterministic and fully inspectable - no hidden model state.
"""
from __future__ import annotations

import datetime as dt
import re

from sqlalchemy.orm import Session

from app.database.database import SessionLocal
from app.models.entities import RetrievalProfile

STOP = set("""a an the of in on for to with and or is are was were be been this that these those we our
they their it its as by from at into using used use can could may might will would shall should not no
what when where how why paper about give me show tell""".split())

WEIGHT_MIN, WEIGHT_MAX = -0.5, 0.5
UP_STEP, DOWN_STEP = 0.08, 0.04
TERM_UP, TERM_DOWN = 0.05, 0.02
DECAY = 0.97


def _terms(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]{2,}", (text or "").lower()) if t not in STOP}


def _clamp(v: float) -> float:
    return max(WEIGHT_MIN, min(WEIGHT_MAX, v))


def _profile(db: Session, paper_id: str, owner: str = "") -> RetrievalProfile:
    row = db.get(RetrievalProfile, paper_id)
    if not row:
        row = RetrievalProfile(paper_id=paper_id, owner=owner or "local", sections={}, terms={},
                               up_count=0, down_count=0)
        db.add(row)
        db.flush()
    elif owner and row.owner != owner:
        row.owner = owner  # migrate older profiles created with the legacy default
    return row


def apply_feedback(db: Session, paper_id: str, evidence: list[dict],
                   query: str, rating: str, owner: str = "") -> RetrievalProfile:
    """Update the profile from one rating. rating: "up" | "down".

    `evidence` items are lightweight dicts: {section, text}.
    """
    profile = _profile(db, paper_id, owner)
    sections: dict[str, float] = dict(profile.sections or {})
    terms: dict[str, float] = dict(profile.terms or {})

    # decay everything - recent feedback matters more
    sections = {k: round(_clamp(v * DECAY), 4) for k, v in sections.items()}
    terms = {k: round(_clamp(v * DECAY), 4) for k, v in terms.items()}

    qterms = _terms(query)
    if rating == "up":
        profile.up_count = (profile.up_count or 0) + 1
        for e in evidence:
            sec = ((e.get("section") or "unknown") or "unknown").lower()
            sections[sec] = _clamp(sections.get(sec, 0.0) + UP_STEP)
            eterms = _terms(e.get("text") or "")
            for t in qterms & eterms:
                terms[t] = _clamp(terms.get(t, 0.0) + TERM_UP)
    else:
        profile.down_count = (profile.down_count or 0) + 1
        for e in evidence:
            sec = ((e.get("section") or "unknown") or "unknown").lower()
            sections[sec] = _clamp(sections.get(sec, 0.0) - DOWN_STEP)
            eterms = _terms(e.get("text") or "")
            for t in qterms & eterms:
                terms[t] = _clamp(terms.get(t, 0.0) - TERM_DOWN)

    # keep the term map bounded (most significant 60)
    if len(terms) > 60:
        terms = dict(sorted(terms.items(), key=lambda kv: -abs(kv[1]))[:60])

    profile.sections = sections
    profile.terms = terms
    profile.updated_at = dt.datetime.utcnow()
    db.commit()
    return profile


def get_profile(db: Session, paper_id: str, owner: str = "") -> RetrievalProfile:
    return _profile(db, paper_id, owner)


def profile_summary(profile: RetrievalProfile) -> dict:
    sections = profile.sections or {}
    terms = profile.terms or {}
    top_sections = sorted(sections.items(), key=lambda kv: -kv[1])[:6]
    top_terms = sorted(terms.items(), key=lambda kv: -kv[1])[:10]
    return {
        "up_count": profile.up_count or 0,
        "down_count": profile.down_count or 0,
        "favored_sections": [{"section": s, "weight": round(w, 3)} for s, w in top_sections if w > 0.01],
        "favored_terms": [{"term": t, "weight": round(w, 3)} for t, w in top_terms if w > 0.01],
        "learned": (profile.up_count or 0) + (profile.down_count or 0) > 0,
    }


def learned_adjustments(paper_id: str) -> tuple[dict[str, float], dict[str, float]]:
    """Convenience for the retriever: (sections, terms) weights, empty dicts if none."""
    db = SessionLocal()
    try:
        p = db.get(RetrievalProfile, paper_id)
        if not p:
            return {}, {}
        return dict(p.sections or {}), dict(p.terms or {})
    finally:
        db.close()
