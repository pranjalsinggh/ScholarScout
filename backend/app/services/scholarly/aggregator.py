"""Ecosystem enrichment - matches the paper in scholarly databases and builds
the citation network, researcher profiles, and classification flags.

Failures bubble up as one aggregate exception: the pipeline marks the paper
ready with a clear "external data unavailable" note instead of failing.
"""
from __future__ import annotations

import re
from contextvars import ContextVar
from copy import deepcopy

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database.database import Base
from app.models.entities import Author, Citation, Paper, PaperSection
import time

from app.ingestion.metadata import is_junk_author_name
from app.services.scholarly import crossref, openalex
from app.services.scholarly.common import lookup_budget, track_lookups

_ENRICH_BUDGET_S = 25.0  # soft ceiling for external database work
_deadline = ContextVar("ecosystem_deadline", default=0.0)
_consent_guard = ContextVar("ecosystem_consent_guard", default=None)


class ResearchLookupDisabled(PermissionError):
    pass


def _guard() -> None:
    callback = _consent_guard.get()
    if callback is not None:
        callback()

# --- transparent keyword classifiers (title/abstract based) -----------------
REPLICATION_RE = re.compile(r"\b(replicat\w*|reproduc\w*|re-?evaluat\w*|re-?examin\w*|revisit\w*)\b", re.I)
CONTRARY_RE = re.compile(
    r"\b(contradict\w*|challenge[sd]?\b|question\w*|refut\w*|fail(s|ed|ing)? to\b|reassess\w*|"
    r"critique[sd]?\b|myths?\b|overestimat\w*|underestimat\w*|much (less|smaller|larger)|"
    r"re-?examin\w*|limitations? of\b|pitfalls?\b|reconsider\w*)\b", re.I)
CORRECTION_RE = re.compile(r"\b(corrigendum|corrections?\b|erratum|errata)\b", re.I)
RETRACTION_RE = re.compile(r"\b(retract\w*|withdraw\w*)\b", re.I)
EXTENSION_RE = re.compile(
    r"\b(extend(s|ed|ing)?\b|improv\w*|generaliz\w*|scal\w*|build(s|ing)? (on|upon)\b|"
    r"towards?\b|beyond\b|enhanc\w*|advanc\w*)\b", re.I)
NEW_DATASET_RE = re.compile(r"\b(new|large-?scale|open)\s+(dataset|corpus|benchmark)\b", re.I)


def _arxiv_year(arxiv_id: str | None) -> int | None:
    """Publication year from an arXiv id (YYMM → 20YY for YY<90, else 19YY)."""
    if not arxiv_id:
        return None
    m = re.match(r"^(\d{2})(\d{2})\.", arxiv_id)
    if not m:
        return None
    yy = int(m.group(1))
    return 2000 + yy if yy < 90 else 1900 + yy


def classify_title(title: str, abstract: str = "") -> dict:
    text = f"{title}. {abstract[:400]}"
    return {
        "is_replication": bool(REPLICATION_RE.search(title) or REPLICATION_RE.search(abstract[:300])),
        "is_contradictory": bool(CONTRARY_RE.search(title)),
        "is_correction": bool(CORRECTION_RE.search(title) or RETRACTION_RE.search(title)),
        "is_extension": bool(EXTENSION_RE.search(title)),
    }


def _authors_all_junk(authors: list[dict]) -> bool:
    from app.ingestion.metadata import is_junk_author_name
    if not authors:
        return True
    return all(is_junk_author_name(a.get("name", "")) for a in authors)


def _doi(value: str | None) -> str:
    return (value or "").strip().lower().removeprefix("https://doi.org/") \
        .removeprefix("http://doi.org/").removeprefix("doi:")


def _surname(name: str) -> str:
    words = re.findall(r"[^\W\d_]+", (name or "").casefold())
    return words[-1] if words else ""


def _match_confidence(paper: Paper, row: dict, search_grade: str,
                      source: str) -> tuple[str, bool]:
    """Corroborate a provider candidate before attaching its citation graph."""
    title = openalex._norm(paper.title or "")
    candidate = openalex._norm(row.get("title") or "")
    similarity = openalex._similarity(title, candidate) if title and candidate else 0.0
    pdf_doi, provider_doi = _doi(paper.doi), _doi(row.get("doi"))
    if pdf_doi and provider_doi and pdf_doi != provider_doi:
        return "weak", False
    pdf_authors = {_surname(a.get("name", "")) for a in (paper.authors or [])
                   if not is_junk_author_name(a.get("name", ""))}
    provider_authors = {_surname(a) for a in (row.get("authors") or []) if a}
    pdf_authors.discard("")
    provider_authors.discard("")
    author_agreement = bool(pdf_authors & provider_authors)
    if pdf_authors and provider_authors and not author_agreement:
        return "weak", False

    if source == "openalex_doi":
        # A DOI extracted from a PDF may belong to a cited work; corroborate
        # its title before allowing that record to become this paper's graph.
        if not pdf_doi or pdf_doi != provider_doi or (title and similarity < 0.6):
            return "weak", False
        return "strong", True

    if search_grade not in {"exact", "high"} or similarity < 0.82:
        return "weak", False
    if len(title.split()) <= 2 and not (author_agreement or
                                         (pdf_doi and pdf_doi == provider_doi)):
        return "weak", False
    if (paper.year and row.get("year") and abs(paper.year - row["year"]) > 5
            and not author_agreement):
        return "weak", False
    return search_grade, True


def _record_match(paper: Paper, *, source: str, confidence: str,
                  status: str) -> None:
    meta = dict(paper.meta or {})
    meta.update(match_source=source, match_confidence=confidence,
                match_status=status)
    paper.meta = meta


def _values(row, *, omit=()):
    return {column.key: deepcopy(getattr(row, column.key))
            for column in row.__table__.columns if column.key not in omit}


def enrich_ecosystem(db: Session, paper: Paper) -> None:
    """Collect remotely in private memory, then publish one short transaction.

    The temporary database is in memory and never writes a shared database
    while a remote provider is being called. A failed/erased refresh discards
    all staged rows. Publication rechecks both ownership and current consent
    under the same lifecycle lock used by purge.
    """
    from app.security.session import SessionEnded, live_owner, owner_write

    paper_id, owner = paper.id, paper.owner

    def consent():
        db.rollback()  # release read snapshots between remote boundaries
        session = live_owner(db, owner)
        enabled = bool(session.research_lookup_opt_in)
        db.rollback()
        if not enabled:
            raise ResearchLookupDisabled("Connected research lookups are off for this session.")

    consent()
    current = db.get(Paper, paper_id)
    if current is None or current.owner != owner:
        raise SessionEnded("This research session has ended.")
    paper_values = _values(current)
    citations = [_values(row) for row in db.scalars(select(Citation).where(Citation.paper_id == paper_id))]
    authors = [_values(row) for row in db.scalars(select(Author).where(Author.paper_id == paper_id))]
    sections = [_values(row) for row in db.scalars(select(PaperSection).where(PaperSection.paper_id == paper_id))]
    db.rollback()
    staging_engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(staging_engine)
        with Session(staging_engine) as staged:
            staged_paper = Paper(**paper_values)
            staged.add(staged_paper)
            staged.flush()
            staged.add_all([Citation(**row) for row in citations])
            staged.add_all([Author(**row) for row in authors])
            staged.add_all([PaperSection(**row) for row in sections])
            staged.commit()
            _collect_ecosystem(staged, staged_paper, consent)
            updates = {name: deepcopy(getattr(staged_paper, name)) for name in (
                "openalex_id", "year", "doi", "venue", "abstract", "authors",
                "meta", "ecosystem_ok", "ecosystem_note")}
            new_citations = [_values(row, omit=("id",)) for row in staged.scalars(
                select(Citation).where(Citation.paper_id == paper_id))]
            new_authors = [_values(row, omit=("id",)) for row in staged.scalars(
                select(Author).where(Author.paper_id == paper_id))]
        with owner_write(db, owner):
            if not live_owner(db, owner).research_lookup_opt_in:
                raise ResearchLookupDisabled("Connected research lookups are off for this session.")
            current = db.get(Paper, paper_id)
            if current is None or current.owner != owner:
                raise SessionEnded("This research session has ended.")
            for name, value in updates.items():
                setattr(current, name, value)
            db.query(Citation).filter(Citation.paper_id == paper_id).delete(synchronize_session=False)
            db.query(Author).filter(Author.paper_id == paper_id).delete(synchronize_session=False)
            db.add_all([Citation(**row) for row in new_citations])
            db.add_all([Author(**row) for row in new_authors])
    finally:
        staging_engine.dispose()


def _collect_ecosystem(db: Session, paper: Paper, consent) -> None:
    """Run the enrichment implementation against a private staging session."""
    consent()
    guard_token = _consent_guard.set(consent)
    deadline_token = _deadline.set(time.monotonic() + _ENRICH_BUDGET_S)
    try:
        with lookup_budget(_ENRICH_BUDGET_S), track_lookups() as events:
            _enrich_ecosystem(db, paper)
        consent()
        meta = dict(paper.meta or {})
        meta["research_sources"] = events
        failures = [event for event in events if event["status"] in {"unavailable", "rate_limited"}]
        reached = any(event["status"] == "ok" for event in events)
        if failures and not reached and meta.get("match_status") not in {"accepted", "accepted_limited"}:
            # A later topic lookup failure does not undo a rejected identity.
            # Keep identity diagnostics separate from service availability.
            if meta.get("match_status") != "rejected":
                meta["match_status"] = "unavailable"
            meta["ecosystem_status"] = "unavailable"
            paper.ecosystem_ok = False
            paper.ecosystem_note = ("The connected research services are temporarily unavailable. "
                                    "No reliable lookup was completed; the uploaded document remains available.")
        elif failures:
            meta["ecosystem_status"] = "partial"
            paper.ecosystem_ok = True
            paper.ecosystem_note = ("Some connected research services could not be reached. "
                                    "Available verified records are shown; the citation network may be incomplete.")
        else:
            meta["ecosystem_status"] = "ok"
            paper.ecosystem_ok = True
            if meta.get("match_status") == "accepted":
                paper.ecosystem_note = None
        paper.meta = meta
        if failures:
            details = list(dict.fromkeys(event["note"] for event in failures if event.get("note")))
            if details:
                paper.ecosystem_note += " " + " ".join(details)[:350]
        db.commit()
    finally:
        _consent_guard.reset(guard_token)
        _deadline.reset(deadline_token)


def _enrich_ecosystem(db: Session, paper: Paper) -> None:
    work = None
    match_confidence = "none"
    match_source = "none"
    rejected_source = "none"
    if paper.doi:
        _guard()
        candidate = openalex.find_work_by_doi(paper.doi)
        if candidate:
            grade, accepted = _match_confidence(
                paper, openalex.work_to_row(candidate), "strong", "openalex_doi")
            if accepted:
                work, match_confidence, match_source = candidate, grade, "openalex_doi"
            else:
                rejected_source = "openalex_doi"
    if work is None and paper.title:
        _guard()
        matched = openalex.find_work_by_title(paper.title)
        if matched:
            candidate, search_grade = matched
            grade, accepted = _match_confidence(
                paper, openalex.work_to_row(candidate), search_grade, "openalex_title")
            if accepted:
                work, match_confidence, match_source = candidate, grade, "openalex_title"
            else:
                rejected_source = "openalex_title"

    crossref_row = None
    if work is None:
        _guard()
        candidate = crossref.find_by_title(paper.title or "")
        if candidate:
            title = openalex._norm(paper.title or "")
            candidate_title = openalex._norm(candidate.get("title") or "")
            similarity = openalex._similarity(title, candidate_title)
            search_grade = ("exact" if title == candidate_title else
                            "high" if similarity >= 0.82 else "weak")
            grade, accepted = _match_confidence(
                paper, candidate, search_grade, "crossref_title")
            if accepted:
                crossref_row, match_confidence, match_source = candidate, grade, "crossref_title"
            else:
                rejected_source = "crossref_title"

    if work is None and crossref_row is None:
        _record_match(paper, source=rejected_source,
                      confidence="weak" if rejected_source != "none" else "none",
                      status="rejected" if rejected_source != "none" else "unmatched")
        paper.ecosystem_note = (
            "We found a possible research record but could not verify it against the uploaded "
            "document. Citation context is hidden to avoid showing the wrong paper."
            if rejected_source != "none" else
            "We couldn't match this paper in OpenAlex or Crossref, so citation context "
            "is unavailable. The document itself is fully analyzed."
        )
        paper.ecosystem_ok = True
        _insert_topic_context(db, paper)
        return

    if work is not None:
        row = openalex.work_to_row(work)
        paper.openalex_id = row["openalex_id"]
        # Preserve the document's own metadata. An accepted external record
        # fills missing values; arXiv can corroborate a conflicting year.
        oa_year = row["year"]
        arxiv_year = _arxiv_year(paper.arxiv_id)
        if arxiv_year and paper.year and abs(paper.year - arxiv_year) > 1:
            paper.year = arxiv_year
        elif not paper.year:
            paper.year = arxiv_year or oa_year
        if row["doi"] and not paper.doi:
            paper.doi = row["doi"]
        if row["venue"] and not paper.venue:
            paper.venue = row["venue"]
        if row["abstract"] and not paper.abstract:
            paper.abstract = row["abstract"]
        # Fill only empty/junk PDF author fields after the candidate passes
        # title/DOI/author corroboration.
        if (not paper.authors or _authors_all_junk(paper.authors)) and row["authors"]:
            paper.authors = [{"name": a, "affiliation": None}
                             for a in row["authors"] if a and not is_junk_author_name(a)]
        meta = dict(paper.meta or {})
        meta["openalex_cited_by"] = row["cited_by_count"]
        meta["topics"] = row["topics"]
        # authoritative author list from OpenAlex authorships (names + institutions)
        oa_authors = []
        for au in (work.get("authorships") or [])[:12]:
            nm = ((au.get("author") or {}).get("display_name") or "").strip()
            if nm:
                inst = next((i.get("display_name") for i in (au.get("institutions") or [])
                             if i.get("display_name")), None)
                oa_authors.append({"name": nm, "affiliation": inst})
        if oa_authors:
            meta["openalex_authors"] = oa_authors
        meta.update(match_confidence=match_confidence, match_source=match_source,
                    match_status="accepted")
        paper.meta = meta
        _insert_citations(db, paper, work, row)
    else:
        # The match is this document, not a second related paper. Crossref
        # provides identity metadata, but no verified citation graph here.
        _record_match(paper, source=match_source, confidence=match_confidence,
                      status="accepted_limited")
        if not paper.doi:
            paper.doi = crossref_row.get("doi")
        if not paper.year:
            paper.year = crossref_row.get("year")
        if not paper.venue:
            paper.venue = crossref_row.get("venue")
        if _authors_all_junk(paper.authors or []):
            paper.authors = [{"name": name, "affiliation": None}
                             for name in crossref_row.get("authors", []) if not is_junk_author_name(name)]
        _guard()
        db.commit()
        paper.ecosystem_note = "Matched via Crossref only - OpenAlex unavailable, so the citation network is limited."
        _insert_topic_context(db, paper)
        return

    _enrich_authors(db, paper, work)
    _guard()
    db.commit()


def _insert_topic_context(db: Session, paper: Paper) -> None:
    """Keep useful literature for essays/reports that have no indexed identity.

    Each row is explicitly a topic match. It is excluded from citing-work
    counts, follow-up timelines, debate and replication classifications.
    """
    from app.services.analysis.distinctive import paper_topic_queries, topic_relevance_filter
    queries = paper_topic_queries(paper, db)
    topic_filter = topic_relevance_filter(paper, db)
    collected = {}
    any_success = False
    for query in queries[:2]:
        _guard()
        with track_lookups() as events:
            works = openalex.search_topic(query, max_results=16)
        if works or not events or any(e["status"] == "ok" for e in events):
            any_success = True
        terms = set(re.findall(r"[a-z0-9]+", query.lower()))
        for work in works:
            row = openalex.work_to_row(work)
            title = openalex._norm(row.get("title") or "")
            if not title or title == openalex._norm(paper.title or ""):
                continue
            if paper.doi and _doi(row.get("doi")) == _doi(paper.doi):
                continue
            if not topic_filter(row.get("title") or "", row.get("abstract") or ""):
                continue
            overlap = terms & set(title.split())
            if len(overlap) < min(2, len(terms)):
                continue
            key = row["openalex_id"] or row["doi"] or title
            collected[key] = row
        if len(collected) >= 6:
            break
    _guard()
    if any_success:
        db.query(Citation).filter(Citation.paper_id == paper.id,
                                  Citation.direction == "related").delete()
        for row in list(collected.values())[:10]:
            db.add(Citation(paper_id=paper.id, direction="related",
                            snippet="Topic match in OpenAlex; not a citation relationship",
                            **_citation_fields(row)))
        meta = dict(paper.meta or {})
        meta.update(topic_context_count=min(len(collected), 10), topic_queries=queries,
                    related_source="topic_search")
        paper.meta = meta
        db.commit()


def _insert_citations(db: Session, paper: Paper, work: dict, row: dict) -> None:
    from app.config import settings

    deadline = _deadline.get()

    def replace(direction, fetch):
        _guard()
        with track_lookups() as events:
            results = fetch()
        _guard()
        # Preserve the last verified rows when a refresh provider is down.
        # Successful empty responses really clear that direction; failures do not.
        if any(e["status"] != "ok" for e in events):
            # A paginated lookup may succeed partly. Retain earlier verified
            # records and append only genuinely new identities on that path.
            known = {c.openalex_id for c in db.query(Citation).filter(
                Citation.paper_id == paper.id, Citation.direction == direction).all()
                     if c.openalex_id}
            return [work for work in results
                    if (work.get("id") or "").rsplit("/", 1)[-1] not in known]
        db.query(Citation).filter(Citation.paper_id == paper.id,
                                  Citation.direction == direction).delete()
        return results

    # works this paper cites (references)
    ref_ids = [rid.rsplit("/", 1)[-1] for rid in (work.get("referenced_works") or [])][:30]
    if ref_ids:
        for w in replace("referenced", lambda: openalex.get_works_batch(ref_ids)):
            r = openalex.work_to_row(w)
            if r["title"]:
                db.add(Citation(paper_id=paper.id, direction="referenced", **_citation_fields(r)))
    else:
        db.query(Citation).filter(Citation.paper_id == paper.id,
                                  Citation.direction == "referenced").delete()

    # works citing this paper
    if time.monotonic() > deadline:
        return
    citing = replace("citing", lambda: openalex.get_citing_works(paper.openalex_id, settings.openalex_max_citing))
    for w in citing:
        r = openalex.work_to_row(w)
        if not r["title"]:
            continue
        cls = classify_title(r["title"], r["abstract"])
        db.add(Citation(paper_id=paper.id, direction="citing",
                        abstract_inverted=None, snippet=_snippet_for(r, cls), **cls, **_citation_fields(r)))

    # semantically related
    if time.monotonic() > deadline:
        return
    for w in replace("related", lambda: openalex.get_related_works(paper.openalex_id, 12)):
        r = openalex.work_to_row(w)
        if r["title"]:
            db.add(Citation(paper_id=paper.id, direction="related", score=0.5,
                            snippet="Related via shared references and citations (OpenAlex)", **_citation_fields(r)))
    _guard()
    db.commit()


def _citation_fields(r: dict) -> dict:
    return dict(
        title=r["title"], authors=r["authors"], year=r["year"], venue=r["venue"],
        doi=r["doi"], openalex_id=r["openalex_id"], cited_by_count=r["cited_by_count"],
        institution_hints=r["institutions"],
    )


def _snippet_for(r: dict, cls: dict) -> str:
    bits = []
    if cls.get("is_replication"):
        bits.append("mentions replication/reproduction")
    if cls.get("is_contradictory"):
        bits.append("title uses challenge/questioning language")
    if cls.get("is_correction"):
        bits.append("correction/erratum")
    if cls.get("is_extension"):
        bits.append("extends or improves")
    return "Classified by keyword heuristic: " + "; ".join(bits) if bits else "Cites this paper"


def _enrich_authors(db: Session, paper: Paper, work: dict) -> None:
    """Author rows with roles + h-index from OpenAlex (batched, capped)."""
    if time.monotonic() > _deadline.get():
        return
    existing = {a["name"].lower() for a in (paper.authors or [])}
    db.query(Author).filter(Author.paper_id == paper.id).delete()

    originals = []
    for i, a in enumerate((paper.authors or [])[:12]):
        originals.append((a["name"], a.get("affiliation"), None, "original", i))
    # overlay with OpenAlex authorships (ids + institutions)
    seen_ids: set[str] = set()
    for j, au in enumerate((work.get("authorships") or [])[:12]):
        aid = ((au.get("author") or {}).get("id") or "").rsplit("/", 1)[-1]
        name = (au.get("author") or {}).get("display_name")
        inst = next((i.get("display_name") for i in (au.get("institutions") or []) if i.get("display_name")), None)
        matched = any(name and name.lower() == n.lower() for n, *_ in originals)
        if matched:
            for k, o in enumerate(originals):
                if name and o[0].lower() == name.lower():
                    originals[k] = (o[0], o[1] or inst, aid, "original", o[4])
        elif name and not any(o[0].lower() == name.lower() for o in originals):
            originals.append((name, inst, aid, "original", len(originals)))

    # roles from citing papers
    extenders: dict[str, int] = {}
    challengers: dict[str, int] = {}
    for c in db.query(Citation).filter(Citation.paper_id == paper.id, Citation.direction == "citing").all():
        for name in (c.authors or [])[:3]:
            if not name:
                continue
            if c.is_contradictory:
                challengers[name] = challengers.get(name, 0) + 1
            elif c.is_extension:
                extenders[name] = extenders.get(name, 0) + 1

    rows: list[Author] = []
    for name, inst, aid, role, pos in originals[:12]:
        rows.append(Author(paper_id=paper.id, name=name, position=pos, affiliation=inst,
                           openalex_id=aid, role=role))
    for name, _ in sorted(extenders.items(), key=lambda x: -x[1])[:8]:
        if name.lower() not in existing and not any(r.name.lower() == name.lower() for r in rows):
            rows.append(Author(paper_id=paper.id, name=name, role="extender"))
    for name, _ in sorted(challengers.items(), key=lambda x: -x[1])[:8]:
        if name.lower() not in existing and not any(r.name.lower() == name.lower() for r in rows):
            rows.append(Author(paper_id=paper.id, name=name, role="challenger"))
    for r in rows:
        db.add(r)
    db.flush()

    # h-index enrichment for the top authors (one batched call)
    ids = [r.openalex_id for r in rows if r.openalex_id][:20]
    if ids:
        _guard()
        details = {a.get("id", "").rsplit("/", 1)[-1]: a for a in openalex.get_authors_batch(ids)}
        for r in rows:
            if not r.openalex_id:
                continue
            d = details.get(r.openalex_id)
            if not d:
                continue
            ss = d.get("summary_stats") or {}
            r.h_index = ss.get("h_index")
            r.works_count = d.get("works_count")
            r.last_seen = (d.get("updated") or "")[:10]
            areas = [t.get("display_name") for t in (d.get("topics") or [])[:3] if t.get("display_name")]
            r.research_areas = areas
