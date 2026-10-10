"""Debate tracker + replication tracker + people + institutions + health panel
- all derived views over the classified citation network."""
from __future__ import annotations

import re
from collections import Counter

from sqlalchemy.orm import Session

from app.models.entities import Author, Citation, Paper, PaperSection
from app.schemas.api import (DebateResponse, DisagreementItem, HealthCheck,
                             HealthResponse, InstitutionItem, PeopleResponse,
                             PersonProfile, ReplicationItem,
                             ReplicationResponse, TensionItem)
from app.services.analysis.distinctive import distinctive_terms
from app.services.analysis.text_units import clean_pdf_text, sentences


def _url(c: Citation) -> str | None:
    if c.doi:
        return f"https://doi.org/{c.doi}"
    if c.openalex_id:
        return f"https://openalex.org/{c.openalex_id}"
    return None


# ---------------------------------------------------------------- debate ----
HEDGE_RE = re.compile(
    r"\b(?:however|remains?\s+(?:unclear|open|unresolved)|open\s+(?:question|problem)|unclear|"
    r"we\s+cannot|cannot\s+be|it\s+is\s+(?:unclear|unknown)|questionable|doubt\w*|"
    r"controversial|disagree\w*|in\s+contrast|on\s+the\s+contrary|challenge\w*|"
    r"assumptions?|critici[sz]\w*|debat\w*|unresolved|convention)\b", re.I)


def _internal_tensions(db: Session, paper: Paper) -> list[TensionItem]:
    """Open questions and points of tension from the paper's OWN text - shown
    when the citation network offers no external disagreements, so the Debate
    view stays relevant to this specific paper."""
    sections = db.query(PaperSection).filter(PaperSection.paper_id == paper.id)         .order_by(PaperSection.position).all()
    # prefer sections where tension naturally lives
    priority = {"discussion": 0, "limitations": 1, "conclusion": 2, "results": 3,
                "methods": 4, "introduction": 5, "abstract": 6, "front matter": 7}
    terms = distinctive_terms(paper.title, paper.abstract, n=6)
    scored: list[tuple[int, int, TensionItem]] = []
    for s in sections:
        if not s.text or s.name.lower() in {"front matter", "references", "keywords", "acknowledgements", "about the author", "image credits"}:
            continue
        for i, sent in enumerate(sentences(s.text, min_len=40, max_len=350)):
            t = sent.strip()
            if not (40 < len(t) < 350) or not HEDGE_RE.search(t):
                continue
            bonus = 2 if any(term in t.lower() for term in terms) else 0
            page = next((c.page for c in paper.chunks if c.section_name == s.name
                         and clean_pdf_text(t).casefold()[:120] in clean_pdf_text(c.text).casefold()), s.page_start)
            scored.append((priority.get(s.name.lower(), 8) - bonus, len(scored),
                           TensionItem(text=t[:340], section=s.name, page=page)))
    scored.sort(key=lambda x: (x[0], x[1]))
    # dedupe near-identical sentences
    out, seen = [], set()
    for _, _, item in scored:
        key = item.text[:60].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= 4:
            break
    return out


def get_debate(db: Session, paper: Paper) -> DebateResponse:
    contrary = db.query(Citation).filter(
        Citation.paper_id == paper.id, Citation.is_contradictory.is_(True)
    ).order_by(Citation.cited_by_count.desc()).limit(12).all()

    items = []
    for c in contrary:
        if not c.title:
            continue
        items.append(DisagreementItem(
            original_claim=_main_claim(paper),
            alternative_finding=c.title,
            why_they_differ=("Reported differences may stem from datasets, evaluation protocols, "
                             "scale, or methodology - read both papers before concluding."),
            paper_title=c.title, year=c.year, source_url=_url(c),
            confidence="heuristic",
        ))
    tensions = _internal_tensions(db, paper)
    note = None
    if not items:
        status = (paper.meta or {}).get("ecosystem_status")
        stage = next((s for s in (paper.stages or []) if s.get("key") == "ecosystem"), {})
        if stage.get("state") == "active":
            note = "Connected research is still being refreshed. These passages show questions raised within the uploaded document."
        elif stage.get("state") == "skipped" or status == "disabled":
            note = "Connected research was not run. These passages show questions raised within the uploaded document."
        elif status in {"unavailable", "partial"}:
            note = "External research coverage is incomplete because a provider was unavailable. These passages are from the uploaded document."
        elif not paper.openalex_id:
            note = "No verified citation network is available for this document. These passages show internal questions, not independently confirmed disagreements."
        else:
            note = ("No citing titles using challenge/contradiction language were detected. "
                    "Keyword heuristics can miss genuine disagreements; internal questions are shown below.")
    else:
        note = ("Disagreements are detected by title keywords and presented neutrally - "
                "a listing here does not mean either paper is wrong.")
    return DebateResponse(disagreements=items, internal_tensions=tensions, note=note)


def _main_claim(paper: Paper) -> str:
    abstract = paper.abstract or ""
    for sent in abstract.replace("\n", " ").split(". "):
        if any(k in sent.lower() for k in ("we propose", "we show", "we find", "we present", "this paper")):
            return sent.strip()[:300]
    return (paper.title or "The original claim")[:200]


# ---------------------------------------------------------- replication ----
def get_replication(db: Session, paper: Paper) -> ReplicationResponse:
    repl = db.query(Citation).filter(
        Citation.paper_id == paper.id, Citation.is_replication.is_(True)
    ).order_by(Citation.cited_by_count.desc()).limit(15).all()

    items = []
    counts = Counter()
    for c in repl:
        if not c.title:
            continue
        # neutral categorization from title language only - never from similarity
        t = c.title.lower()
        if any(w in t for w in ("fail", "failing", "cannot", "unable", "does not reproduce", "myth")):
            cat = "failed"
        elif any(w in t for w in ("partial", "limitation", "mixed", "under certain")):
            cat = "partial"
        elif any(w in t for w in ("contradict", "refute", "challenge", "reassess", "overestimat")):
            cat = "contradictory"
        elif re.search(r"\b(?:successfully (?:replicat\w*|reproduc\w*)|"
                       r"(?:replicat\w*|reproduc\w*) (?:success|confirm\w*)|confirm\w*|support\w*)\b", t):
            cat = "supporting"
        else:
            cat = "insufficient"
        counts[cat] += 1
        items.append(ReplicationItem(
            category=cat, paper_title=c.title, year=c.year,
            snippet=("Outcome is unestablished unless the title contains an explicit outcome cue. Read the study to verify. "
                     + (c.snippet or "")),
            source_url=_url(c),
        ))

    if not items:
        ecosystem_stage = next((stage for stage in paper.stages or []
                                if stage.get("key") == "ecosystem"), {})
        state = ecosystem_stage.get("state")
        if state == "active":
            note = "Connected research is still being refreshed. Replication coverage has not been established yet."
        elif state == "skipped" and "off" in (paper.ecosystem_note or ecosystem_stage.get("detail") or "").lower():
            note = "Connected research lookups are off. External replication studies have not been searched for this session."
        elif paper.ecosystem_ok is False:
            note = (paper.ecosystem_note or "Connected research services could not be reached.") + " Replication coverage could not be established; retry connected research later."
        elif state not in {"done"} and not paper.openalex_id:
            note = "No completed connected research lookup is available. Replication coverage has not been established."
        elif not paper.openalex_id:
            note = (paper.ecosystem_note or "No verified scholarly record was matched.") + " Without a matched citation network, absence of replication studies cannot be assessed."
        else:
            note = ("No replication-tagged studies were detected among the indexed citing works. "
                    "This limited search does not establish that no replication exists or that the claims are robust.")
        return ReplicationResponse(
            status="insufficient",
            items=[],
            note=note,
        )
    status = counts.most_common(1)[0][0]
    return ReplicationResponse(
        status=status, items=items,
        note=("Categories come from title-keyword heuristics. A replication title without an explicit "
              "outcome is marked insufficient; similarity never establishes replication success."),
    )


# ---------------------------------------------------------------- people ----
def _profile(a: Author, notable: list[dict] | None = None) -> PersonProfile:
    return PersonProfile(
        name=a.name, role=a.role or "original", affiliation=a.affiliation,
        research_areas=a.research_areas or [], h_index=a.h_index,
        works_count=a.works_count, notable_works=notable or [],
        openalex_id=a.openalex_id,
        profile_url=f"https://openalex.org/{a.openalex_id}" if a.openalex_id else None,
    )


def get_people(db: Session, paper: Paper) -> PeopleResponse:
    authors = db.query(Author).filter(Author.paper_id == paper.id).order_by(Author.position).all()

    originals, extenders, challengers = [], [], []
    for a in authors:
        p = _profile(a)
        if a.role == "original":
            originals.append(p)
        elif a.role == "extender":
            extenders.append(p)
        elif a.role == "challenger":
            challengers.append(p)

    # The uploaded document is authoritative for its own authors. Their
    # presence must not depend on being matched by an external database.
    from app.ingestion.metadata import is_junk_author_name
    known_originals = {p.name.casefold() for p in originals}
    for author in paper.authors or []:
        name = (author.get("name") or "").strip()
        if name and name.casefold() not in known_originals and not is_junk_author_name(name):
            originals.append(PersonProfile(name=name, role="original",
                                             affiliation=author.get("affiliation")))
            known_originals.add(name.casefold())

    # notable works: top cited citing papers per researcher
    notable_by_author: dict[str, list[dict]] = {}
    for c in db.query(Citation).filter(Citation.paper_id == paper.id,
                                       Citation.direction == "citing") \
            .order_by(Citation.cited_by_count.desc()).limit(60).all():
        for name in (c.authors or [])[:4]:
            if name and len(notable_by_author.setdefault(name, [])) < 3:
                notable_by_author[name].append({"title": c.title, "year": c.year, "url": _url(c)})

    for group in (originals, extenders, challengers):
        for p in group:
            p.notable_works = notable_by_author.get(p.name, [])[:3]

    # major researchers: most frequent authors across citing works
    counter: Counter = Counter()
    for c in db.query(Citation).filter(Citation.paper_id == paper.id,
                                       Citation.direction == "citing").limit(200).all():
        for name in (c.authors or [])[:5]:
            if name:
                counter[name] += 1
    known = {p.name for p in originals + extenders + challengers}
    majors = []
    for name, n in counter.most_common(12):
        if name in known:
            continue
        majors.append(PersonProfile(
            name=name, role="major",
            research_areas=[f"published {n} indexed works citing this paper"],
            notable_works=notable_by_author.get(name, [])[:3],
        ))
        if len(majors) >= 6:
            break

    note = None
    if not paper.openalex_id:
        note = ("Authors identified in the document are shown without external profile metrics. "
                + (paper.ecosystem_note or "No verified OpenAlex record was matched."))
    elif not originals:
        note = "No authors could be identified in the document or its verified external record."
    return PeopleResponse(original_authors=originals[:12], extenders=extenders,
                          challengers=challengers, major_researchers=majors, note=note)


# --------------------------------------------------------- institutions ----
def get_institutions(db: Session, paper: Paper) -> list[InstitutionItem]:
    counts: Counter = Counter()
    roles: dict[str, set[str]] = {}

    def add(name: str | None, role: str):
        if not name:
            return
        counts[name] += 1
        roles.setdefault(name, set()).add(role)

    for a in paper.authors or []:
        if a.get("affiliation"):
            add(a["affiliation"], "original paper")

    for a in db.query(Author).filter(Author.paper_id == paper.id, Author.affiliation.isnot(None)).all():
        add(a.affiliation, "original paper")

    for c in db.query(Citation).filter(Citation.paper_id == paper.id).limit(300).all():
        role = ("follow-up research" if c.direction == "citing"
                else "cited foundation" if c.direction == "referenced" else "related research")
        for inst in (c.institution_hints or []):
            add(inst, role)

    out = []
    for name, n in counts.most_common(20):
        out.append(InstitutionItem(name=name, roles=sorted(roles.get(name, set())),
                                   related_papers=n))
    return out


# ---------------------------------------------------------------- health ----
def get_health(db: Session, paper: Paper) -> HealthResponse:
    n_citing = db.query(Citation).filter(Citation.paper_id == paper.id,
                                         Citation.direction == "citing").count()
    has_followup = db.query(Citation).filter(Citation.paper_id == paper.id,
                                             Citation.direction == "citing",
                                             Citation.is_extension.is_(True)).count()
    n_repl = db.query(Citation).filter(Citation.paper_id == paper.id,
                                       Citation.is_replication.is_(True)).count()
    n_contra = db.query(Citation).filter(Citation.paper_id == paper.id,
                                         Citation.is_contradictory.is_(True)).count()
    n_corr = db.query(Citation).filter(Citation.paper_id == paper.id,
                                       Citation.is_correction.is_(True)).count()

    import datetime as dt
    current_year = dt.datetime.now(dt.timezone.utc).year
    recent = db.query(Citation).filter(Citation.paper_id == paper.id,
                                      Citation.direction == "citing",
                                      Citation.year >= current_year - 5).count() > 0
    context_note = paper.ecosystem_note or "No verified scholarly record was matched."

    checks = [
        HealthCheck(label="Publication", state="ok" if paper.openalex_id else "partial",
                    detail=(paper.venue or "Published work indexed") if paper.openalex_id else context_note),
        HealthCheck(label="Citation activity", state="ok" if n_citing >= 10 else "partial" if n_citing else "none",
                    detail=f"{n_citing} citing works indexed"),
        HealthCheck(label="Follow-up studies", state="ok" if has_followup >= 3 else "partial" if has_followup else "none",
                    detail=f"{has_followup} extension-type works detected"),
        HealthCheck(label="Replication evidence", state="ok" if n_repl >= 2 else "partial" if n_repl else "none",
                    detail=f"{n_repl} replication-tagged works" if n_repl else "No replication studies detected"),
        HealthCheck(label="Contradictory work", state="warning" if n_contra else "ok",
                    detail=f"{n_contra} works use challenge language" if n_contra else "None detected"),
        HealthCheck(label="Corrections", state="warning" if n_corr else "ok",
                    detail=f"{n_corr} correction/erratum detected" if n_corr else "None detected"),
        HealthCheck(label="Retraction", state="ok" if not _retracted(paper) else "warning",
                    detail="None detected" if not _retracted(paper) else "Retraction-tagged work found - verify"),
        HealthCheck(label="Recent activity", state="ok" if recent else "none",
                    detail="Citations within recent years" if recent else "No recent indexed activity"),
    ]
    coverage = (paper.meta or {}).get("ecosystem_status")
    stage = next((s for s in (paper.stages or []) if s.get("key") == "ecosystem"), {})
    if not paper.openalex_id or coverage in {"unavailable", "partial", "disabled"} or stage.get("state") in {"skipped", "active"}:
        for check in checks:
            if check.label in {"Contradictory work", "Corrections", "Retraction"} and check.state == "ok":
                check.state = "partial"
                check.detail = "Not established: connected citation coverage is unavailable or incomplete."
    return HealthResponse(
        checks=checks,
        disclaimer=("This panel summarizes detected evidence - it is not a scientific quality "
                    "score, and absence of evidence is not evidence of absence."),
    )


def _retracted(paper: Paper) -> bool:
    import re
    return bool(re.search(r"\bretract", (paper.title or "") + " " + (paper.meta or {}).get("openalex_title", ""), re.I))
