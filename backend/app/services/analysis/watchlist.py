"""Watchlist - saved papers with update polling (new citations, corrections...)."""
from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from app.models.entities import Citation, Paper, WatchItem
from app.schemas.api import WatchResponse
from app.services.scholarly import openalex


def add_watch(db: Session, paper_id: str) -> WatchResponse:
    paper = db.query(Paper).get(paper_id)
    if not paper:
        raise ValueError("Paper not found")
    item = WatchItem(paper_id=paper_id, label=paper.title or paper.filename,
                     last_count=db.query(Citation).filter(Citation.paper_id == paper_id).count())
    db.add(item)
    db.commit()
    return _to_response(item, 0)


def list_watch(db: Session) -> list[WatchResponse]:
    items = db.query(WatchItem).order_by(WatchItem.created_at.desc()).all()
    return [_to_response(i, 0) for i in items]


def remove_watch(db: Session, watch_id: int) -> bool:
    n = db.query(WatchItem).filter(WatchItem.id == watch_id).delete()
    db.commit()
    return n > 0


def check_updates(db: Session, item: WatchItem) -> int:
    """Poll OpenAlex for newly-indexed citing works; record the delta."""
    paper = db.query(Paper).get(item.paper_id)
    if not paper or not paper.openalex_id:
        return 0
    works = openalex.get_citing_works(paper.openalex_id, 200)
    owner, watch_id, paper_id = item.owner, item.id, paper.id
    from app.security.session import owner_write
    with owner_write(db, owner):
        paper = db.query(Paper).filter(Paper.id == paper_id, Paper.owner == owner).first()
        item = db.query(WatchItem).filter(WatchItem.id == watch_id, WatchItem.owner == owner).first()
        if paper is None or item is None:
            return 0
        return _apply_updates(db, item, paper, works)


def _apply_updates(db: Session, item: WatchItem, paper: Paper, works: list) -> int:
    """Called only while the owner's live-session write transaction is held."""
    known = {c.openalex_id for c in db.query(Citation).filter(
        Citation.paper_id == paper.id, Citation.direction == "citing").all()
        if c.openalex_id}
    new = [w for w in works if (w.get("id") or "").rsplit("/", 1)[-1] not in known]
    updates = list(item.updates or [])
    added = 0
    for w in new[:10]:
        r = openalex.work_to_row(w)
        updates.append({
            "kind": "new_citation",
            "title": r["title"],
            "year": r["year"],
            "url": (f"https://doi.org/{r['doi']}" if r["doi"] else
                    f"https://openalex.org/{r['openalex_id']}" if r["openalex_id"] else None),
            "date": dt.datetime.utcnow().date().isoformat(),
        })
        added += 1
    if added:
        item.updates = updates[-30:]
        item.last_checked = dt.datetime.utcnow()
    item.last_count = item.last_count or 0
    return added


def _to_response(item: WatchItem, new_count: int) -> WatchResponse:
    return WatchResponse(id=item.id, paper_id=item.paper_id, label=item.label,
                         updates=item.updates or [], last_count=item.last_count,
                         new_count=new_count)
