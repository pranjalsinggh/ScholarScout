"""Timeline - 'Since This Paper Was Published' - built from classified citations."""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy.orm import Session

from app.models.entities import Citation, Paper, ResearchEvent
from app.schemas.api import TimelineEntry, TimelineResponse
from app.services.scholarly.aggregator import NEW_DATASET_RE

MAX_PER_KIND = 14


def build_timeline(db: Session, paper: Paper) -> None:
    from app.security.session import SessionEnded, owner_write
    paper_id, owner = paper.id, paper.owner
    with owner_write(db, owner):
        current = db.get(Paper, paper_id)
        if current is None or current.owner != owner:
            raise SessionEnded("This research session has ended.")
        _build_timeline(db, current)


def _build_timeline(db: Session, paper: Paper) -> None:
    db.query(ResearchEvent).filter(ResearchEvent.paper_id == paper.id).delete()

    events: list[ResearchEvent] = []
    base_year = paper.year

    events.append(ResearchEvent(
        paper_id=paper.id, kind="publication", date_year=base_year,
        date_text=str(base_year) if base_year else "Publication date unknown",
        title=(paper.title or "Uploaded document")[:200],
        description=("Document publication year identified." if base_year else
                     "Publication year is unknown; the upload date is not a publication date.")
                    + (f" Venue: {paper.venue}." if paper.venue else ""),
        source_type="academic" if paper.openalex_id else "document",
        source_name=paper.venue or "Uploaded PDF",
        source_url=(f"https://doi.org/{paper.doi}" if paper.doi else None),
        confidence="high" if base_year else "unknown",
    ))

    cites = db.query(Citation).filter(Citation.paper_id == paper.id,
                                      Citation.direction == "citing").all()
    added = 0
    for c in sorted(cites, key=lambda x: (x.year or 9999, -(x.cited_by_count or 0))):
        if not c.title or added >= MAX_PER_KIND * 3:
            continue
        kind = None
        desc = None
        if c.is_correction:
            kind = "correction"
            desc = "Correction or erratum detected by title keywords."
        elif REPLICATION.detect(c.title):
            kind = "replication"
            desc = "Replication or reproduction study detected by title keywords."
        elif c.is_contradictory:
            kind = "contradiction"
            desc = "Uses challenge/questioning language toward results in this area."
        elif NEW_DATASET_RE.search(c.title or ""):
            kind = "newdataset"
            desc = "Introduces a new dataset or benchmark for this area."
        elif c.is_extension and (c.cited_by_count or 0) >= 10:
            kind = "followup"
            desc = "Notable follow-up building on this work."
        if kind:
            events.append(_event_from_citation(paper.id, c, kind, desc))
            added += 1

    # top follow-ups by citation count (one per year, max 6)
    per_year: dict[int, Citation] = {}
    for c in sorted(cites, key=lambda x: -(x.cited_by_count or 0)):
        if c.year and c.title and (not base_year or c.year >= base_year) and c.is_extension:
            if c.year not in per_year:
                per_year[c.year] = c
    for year, c in sorted(per_year.items())[:6]:
        if not any(e.citation_id == c.id for e in events):
            events.append(_event_from_citation(paper.id, c, "milestone",
                                               "Highly-cited follow-up work in this research line."))

    db.add_all(events)


def _event_from_citation(paper_id: str, c: Citation, kind: str, desc: str | None) -> ResearchEvent:
    return ResearchEvent(
        paper_id=paper_id, kind=kind, date_year=c.year, date_text=str(c.year) if c.year else None,
        title=c.title[:200], description=desc, source_type="academic",
        source_name=c.venue or "OpenAlex", confidence="heuristic",
        source_url=(f"https://doi.org/{c.doi}" if c.doi
                    else (f"https://openalex.org/{c.openalex_id}" if c.openalex_id else None)),
        citation_id=c.id,
    )


class REPLICATION:
    import re as _re
    _rx = _re.compile(r"\b(replicat\w*|reproduc\w*|re-?evaluat\w*|re-?examin\w*|revisit\w*)\b", _re.I)

    @classmethod
    def detect(cls, title: str | None) -> bool:
        return bool(title and cls._rx.search(title))


def get_timeline(db: Session, paper: Paper) -> TimelineResponse:
    events = db.query(ResearchEvent).filter(ResearchEvent.paper_id == paper.id) \
        .order_by(ResearchEvent.date_year, ResearchEvent.id).all()
    entries = [
        TimelineEntry(
            kind=e.kind, year=e.date_year, date_text=e.date_text, title=e.title,
            description=e.description, source_type=e.source_type, source_name=e.source_name,
            source_url=e.source_url, confidence=e.confidence or "high",
        ) for e in events
    ]
    note = None
    status = "ok"
    ecosystem_status = (paper.meta or {}).get("ecosystem_status")
    followups = any(e.kind != "publication" for e in events)
    if ecosystem_status in {"partial", "unavailable"}:
        status = "partial" if followups else "unavailable"
        note = paper.ecosystem_note or "Connected citation coverage could not be fully checked."
        if not paper.year:
            note += " The document's publication year is unknown."
        return TimelineResponse(entries=entries, note=note, status=status)
    if not followups:
        match = (paper.meta or {}).get("match_status")
        if not paper.ecosystem_ok:
            status = "disabled" if "off" in (paper.ecosystem_note or "").lower() else "unavailable"
            note = paper.ecosystem_note or "Connected research could not be checked."
        elif match in {"unmatched", "rejected"}:
            status = "unmatched"
            note = (paper.ecosystem_note or "No verified scholarly record was matched to this document.")
        else:
            status = "empty"
            note = ("No dated follow-up events were detected in the indexed citation records. "
                    "This does not imply that no subsequent research exists.")
        if not paper.year:
            note += " The document's publication year is unknown."
    return TimelineResponse(entries=entries, note=note, status=status)
