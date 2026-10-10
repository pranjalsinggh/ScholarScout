"""Paper-grounded future directions and reproduction checklist."""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.models.entities import Citation, Paper, PaperSection
from app.services.analysis.text_units import sentences
from app.schemas.api import (GapItem, GapsResponse, ReproductionChecklist,
                             ReproductionResponse)

FUTURE_RE = re.compile(r"\b(future work|future research|we (?:plan|intend|aim|hope) to|"
                       r"we will (?:explore|investigate|extend|apply)|remains? an? open question)\b", re.I)
PLAN_RE = re.compile(r"\b(?:we|NASA|the (?:agency|programme?|plan))\s+(?:will|plans? to)\s+"
                     r"(?:explore|investigate|extend|develop|send|conduct|launch|establish|implement)\b", re.I)
LIMIT_MARK = re.compile(r"\b(limitation|drawback|shortcoming|caveat|fails? to|failed to|"
                        r"cannot|does not (?:work|generaliz|address|handle)|restricted to|"
                        r"only evaluated on|we leave .* for future work)\b", re.I)

GITHUB_RE = re.compile(r"(github\.com|gitlab\.com|bitbucket\.org)/[\w\-./]+", re.I)
SEED_RE = re.compile(r"\brandom seed(s)?\b|\bseed(ed|ing)?\b", re.I)
HW_RE = re.compile(r"\b(NVIDIA|GPU|TPU|V100|A100|H100|RTX|P100|K80)\b")
HP_RE = re.compile(r"\b(learning rate|batch size|optimizer|epoch(s)?|hyperparameter\w*|"
                   r"hidden (size|dimension)|number of layers|dropout|warm-?up)\b", re.I)
PREPROC_RE = re.compile(r"\b(preprocess\w*|normali[sz]\w*|tokeni[sz]\w*|augmentation|cleaning|pipeline)\b", re.I)
BASELINE_RE = re.compile(r"\bbaseline(s)?\b|\bcompared? (against|to|with)\b|\bbenchmark method", re.I)


def _sections(db: Session, paper_id: str) -> list[PaperSection]:
    return db.query(PaperSection).filter(PaperSection.paper_id == paper_id).all()


def _citing(db: Session, paper_id: str) -> list[Citation]:
    return db.query(Citation).filter(Citation.paper_id == paper_id,
                                     Citation.direction == "citing").all()


def _direction_title(statement: str) -> str:
    match = re.search(r"\b(?:we (?:plan|intend|aim|hope) to|we will|future work (?:is|will be|should be) to)\s+(.+)",
                      statement, re.I)
    clause = (match.group(1) if match else statement).strip(" .:;)")
    clause = re.split(r"[.;]", clause, maxsplit=1)[0]
    words = clause.split()
    if len(words) > 13:
        clause = " ".join(words[:13]) + "…"
    return clause[:1].upper() + clause[1:] if clause else "Investigate stated future work"


def _paper_source(paper: Paper, section: str) -> list[dict]:
    return [{"title": paper.title, "year": paper.year, "url": None,
             "source": f"{section} section of this paper"}]


def build_gaps(db: Session, paper: Paper) -> GapsResponse:
    sections = _sections(db, paper.id)
    citing = _citing(db, paper.id)
    gaps: list[GapItem] = []
    seen: set[str] = set()
    for section in sections:
        name = section.name.lower()
        if name in {"front matter", "references", "acknowledgements", "keywords", "about the author", "image credits"}:
            continue
        limitation_section = name in {"conclusion", "discussion", "limitations", "future work", "risks"}
        for statement in sentences(section.text, min_len=40, max_len=420):
            key = re.sub(r"\W+", "", statement.lower())[:90]
            if key in seen:
                continue
            if FUTURE_RE.search(statement):
                seen.add(key)
                gaps.append(GapItem(
                    title=_direction_title(statement),
                    why=f"The authors propose: “{statement}” This is a direction to investigate, not a verified open gap; check later work before claiming novelty.",
                    supporting=_paper_source(paper, section.name), direction="stated future work",
                ))
            elif PLAN_RE.search(statement) and re.search(r"plan|roadmap|vision|explor|future|recommend|conclusion", name):
                seen.add(key)
                gaps.append(GapItem(
                    title=_direction_title(statement),
                    why=f"The document states a plan: “{statement}” This records a proposed action at the time of writing; it does not establish a current research gap or whether the plan was carried out.",
                    supporting=_paper_source(paper, section.name), direction="stated plan",
                ))
            elif limitation_section and LIMIT_MARK.search(statement):
                seen.add(key)
                gaps.append(GapItem(
                    title="Investigate a stated limitation",
                    why=f"The paper states: “{statement}” A follow-up study could test where this limitation applies and whether a proposed fix improves the result.",
                    supporting=_paper_source(paper, section.name), direction="limitation",
                ))
            if len(gaps) >= 5:
                break
        if len(gaps) >= 5:
            break

    challenged = [c for c in citing if c.is_contradictory][:2]
    if challenged:
        gaps.append(GapItem(
            title="Verify potential challenges in later work",
            why="These citing papers were flagged by a keyword heuristic as possible challenges. Read their actual findings before treating them as contradictions.",
            supporting=[_cite_dict(c) for c in challenged], direction="literature check",
        ))

    return GapsResponse(
        gaps=gaps[:6],
        disclaimer=("Directions come from explicit future-work, plans or limitation statements in this document. "
                    "They are not claims of novelty; verify later literature before acting on them."),
    )


def generate_ideas(db: Session, paper: Paper) -> GapsResponse:
    """Turn grounded directions into study-design prompts without inventing novelty."""
    base = build_gaps(db, paper)
    ideas = [GapItem(
        title=f"Study design: {gap.title}",
        why=f"Start from the sourced direction above. Define a measurable outcome, a baseline, and a failure criterion, then check the latest literature before running the study. {gap.why}",
        supporting=gap.supporting, direction="study prompt",
    ) for gap in base.gaps if gap.direction in {"stated future work", "stated plan", "limitation"}]
    return GapsResponse(
        gaps=ideas[:4],
        disclaimer=("Study prompts are derived from the paper's own statements. "
                    "They do not establish that the idea is new or feasible."),
    )


def build_reproduction(db: Session, paper: Paper) -> ReproductionResponse:
    sections = _sections(db, paper.id)
    meta = paper.meta or {}
    body = [section for section in sections if section.name.lower() not in {
        "references", "acknowledgements", "keywords", "front matter"}]
    full = " ".join(s.text or "" for s in body)

    def evidence(pattern: str | re.Pattern, *, candidates=None) -> str | None:
        regex = re.compile(pattern, re.I) if isinstance(pattern, str) else pattern
        for section in candidates if candidates is not None else body:
            for statement in sentences(section.text or "", min_len=20, max_len=300):
                if regex.search(statement):
                    page = section.page_start
                    needle = re.sub(r"\s+", " ", statement).casefold()[:120]
                    for chunk in paper.chunks or []:
                        if chunk.section_name == section.name and needle in re.sub(r"\s+", " ", chunk.text).casefold():
                            page = chunk.page
                            break
                    location = f"p. {page}, {section.name}" if page else section.name
                    return f"{location}: {statement}"
        return None

    # Discussions of somebody else's experiments do not establish a protocol
    # for reproducing this document. Require an experiment/evaluation section
    # or an explicit statement that the authors performed an empirical study.
    empirical_sections = [s for s in body if s.name.lower() in {
        "experiments", "experimental setup", "experimental results", "evaluation", "results"}]
    own_study = re.search(r"\b(?:we|this study|our study)\s+(?:have\s+)?(?:conducted|performed|measured|"
                          r"collected|trained|tested|evaluated)\b", full, re.I)
    experimental = bool(own_study or any(re.search(
        r"\b(?:experiment\w*|measurement\w*|dataset|participants?|samples?|benchmark|accuracy|"
        r"confidence interval|statistical test)\b", s.text or "", re.I) for s in empirical_sections))

    if experimental:
        machine_learning = bool(HP_RE.search(full) or re.search(
            r"\b(?:neural network|pytorch|tensorflow|model training|training dataset)\b", full, re.I))
        definitions = [
            ("Data or participants identified", r"\b(?:dataset|corpus|participants?|sample size|cohort|observations?)\b"),
            ("Measured outcomes or metrics mentioned", r"\b(?:metric|accuracy|precision|recall|measurement|measured|outcome|error rate)\b"),
            ("Experimental procedure described", r"\b(?:procedure|protocol|we (?:conducted|performed|tested|measured|collected)|experimental setup)\b"),
            ("Comparison or control mentioned", BASELINE_RE.pattern + r"|\bcontrol group\b"),
            ("Uncertainty or statistical analysis mentioned", r"\b(?:confidence interval|standard deviation|error bar|statistical|uncertainty|variance)\b"),
            ("Materials, data or code access mentioned", GITHUB_RE.pattern + r"|\b(?:data availability|materials available|supplementary materials|code available)\b"),
        ]
        if machine_learning:
            definitions.extend([
                ("Model or architecture mentioned", r"\b(?:model|architecture|neural network|transformer|classifier)\b"),
                ("Hardware mentioned", HW_RE.pattern),
                ("Software dependencies mentioned", r"\b(?:tensorflow|pytorch|jax|scikit-?learn|keras|numpy|cuda)\b"),
                ("Hyperparameters mentioned", HP_RE.pattern),
                ("Preprocessing mentioned", PREPROC_RE.pattern),
                ("Random seeds mentioned", r"\brandom seeds?\b"),
            ])
        context = "empirical procedure"
        applicability = "An empirical procedure was identified in the document. "
    else:
        theory = bool(re.search(r"\b(?:quantum|relativity|theorem|derivation|spacetime|wave function)\b",
                               full + " " + (paper.title or ""), re.I))
        definitions = [
            ("Purpose or central position stated", r"\b(?:goal|purpose|objective|this (?:paper|report|study)|we (?:argue|present|propose)|i (?:believe|recommend))\b"),
            ("Reasoning or approach described", r"\b(?:analysis|approach|reasoning|we (?:examine|compare|review|use)|the (?:plan|strategy))\b"),
            ("Assumptions or scope stated", r"\b(?:assum\w*|scope|only consider|restricted to|under the condition)\b"),
            ("Limitations or uncertainties acknowledged", r"\b(?:limitation\w*|uncertain\w*|unclear|unresolved|constraint\w*|cannot|remains? open)\b"),
        ]
        if theory:
            definitions.append(("Definitions or derivations supplied", r"\b(?:defin\w*|derivation|equation|theorem|lemma|proof)\b"))
            context = "conceptual argument"
        else:
            definitions.extend([
                ("Actions or recommendations stated", r"\b(?:recommend\w*|propos\w*|will|planned|should|roadmap)\b"),
                ("Dates, resources or conditions described", r"\b(?:budget|funding|resources|conditions?|\d{4}|century|inscription\w*)\b"),
            ])
            context = "report, history or strategy"
        applicability = ("No identifiable original empirical protocol was found. "
                         f"For this {context}, check sources and reasoning rather than expecting training code or experimental datasets. ")

    checklist = []
    for label, pattern in definitions:
        excerpt = evidence(pattern)
        checklist.append(ReproductionChecklist(item=label, found=bool(excerpt), detail=excerpt))
    source_sections = [s for s in sections if s.name.lower() in {"references", "bibliography", "sources"} and s.text]
    source_excerpt = evidence(r"\S", candidates=source_sections) or evidence(r"\[\d+(?:[,–\- ]\d+)*\]|\b(?:cited|according to|et al\.|references)\b")
    checklist.append(ReproductionChecklist(item="Sources or references supplied", found=bool(source_excerpt), detail=source_excerpt))
    n_found = sum(1 for c in checklist if c.found)
    summary = (applicability + f"{n_found} of {len(checklist)} information checks have matching source passages. "
               "A mention does not establish that the instructions are complete or that the claim has been verified.")
    return ReproductionResponse(checklist=checklist, summary=summary,
                                disclaimer="Source availability and verification checklist; it does not establish reproducibility or scientific validity.")


def _top_citing(citing: list[Citation], k: int) -> list[dict]:
    top = sorted((c for c in citing if c.title), key=lambda c: -(c.cited_by_count or 0))[:k]
    return [_cite_dict(c) for c in top]


def _cite_dict(c: Citation) -> dict:
    return {
        "title": c.title, "year": c.year,
        "url": (f"https://doi.org/{c.doi}" if c.doi else
                f"https://openalex.org/{c.openalex_id}" if c.openalex_id else None),
        "cited_by": c.cited_by_count,
    }
