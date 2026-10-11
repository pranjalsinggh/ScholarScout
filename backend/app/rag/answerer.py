"""Evidence-first answer composition.

Default mode is deterministic extractive answering: sentences are selected
from retrieved passages by transparent term-overlap scoring, so every answer
is literally grounded in the uploaded paper. Nothing is invented - if evidence
is weak the assistant says so.

If GEMINI_API_KEY / GROQ_API_KEY (or OPENAI_API_KEY) are configured, answers
are synthesized from the same retrieved evidence with a strict evidence-only
prompt, tried in chain order with automatic fallback.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from app.database.database import SessionLocal
from app.models.entities import Paper
from app.rag.llm_providers import llm_configured
from app.rag.retriever import Retrieved, retrieve
from app.security.owner import decrypt_json, decrypt_text, encrypt_json, encrypt_text
from app.services.analysis.text_units import clean_pdf_text, sentences as pdf_sentences

STOPWORDS = set("""a an the of in on for to with and or is are was were be been this that these those we our
they their it its as by from at into using used use can could may might will would shall should not no
than then also such which who whom what when where how why there here more most other some any each both
paper document report essay article speech study work approach method methods results result section propose proposed show shows shown about author authors""".split())

RESULTS_INTENT_RE = re.compile(r"\b(main|key|major|primary)?\s*(results?|findings?|outcomes?)\b", re.I)
DATASET_INTENT_RE = re.compile(r"\b(datasets?|data sets?|benchmarks?|corpora|corpus)\b", re.I)
METRIC_INTENT_RE = re.compile(r"\b(metrics?|measures?|evaluat\w*|BLEU|accuracy|F1)\b", re.I)
LIMIT_INTENT_RE = re.compile(r"\b(limitations?|weaknesses?|drawbacks?|caveats?|failure modes?)\b", re.I)
# deliberate: "why does their method win?" must NOT be hijacked into the canned
# methodology paragraph - only explicit methodology questions route there
METHOD_INTENT_RE = re.compile(
    r"\b(methodolog\w*|architecture|(?:what|which)\s+methods?\b|"
    r"how\s+(?:does|did|is|was)\s+(?:it|the model|the method|the system|their approach)\s+(?:work|designed|built|structured))\b", re.I)
CLAIM_INTENT_RE = re.compile(r"\b(?:main|central|key|principal)\s+(?:claims?|arguments?|thes[ei]s)\b", re.I)
SUPPORT_INTENT_RE = re.compile(r"\b(?:what|which)\s+evidence\s+(?:supports?|backs?)|\bsupport(?:ing|s)?\s+(?:its|their|the|this)\s+(?:main\s+)?claim\b", re.I)


def _passage_sentences(text: str) -> list[str]:
    cleaned = clean_pdf_text(re.sub(r"^\[[^\]]+\]\s*", "", text or ""))
    parts = pdf_sentences(cleaned, min_len=40, max_len=420)
    if parts and re.match(r"^[a-z]", parts[0]) and not re.match(
        r"^(?:we|our|the|this|these|those|a|an|in|on|for|with|by|as|however|furthermore)\b", parts[0], re.I
    ):
        parts = parts[1:]
    return parts

META_INTENT_RE = re.compile(
    r"\b(what is (this|the) (paper|article|study|document|report|essay|speech|plan) about|"
    r"what does (this|the) (paper|document|report|essay|speech|plan) (do|study|explore|propose)|"
    r"summar(y|ize|ise) (?:(?:the|this) )?(paper|document|report|essay|speech|plan)|"
    r"give me an overview|main (topic|idea|contribution)s?|"
    r"(?:paper|document|report) (about|overview)|what are they studying)\b", re.I)

AUTHOR_INTENT_RE = re.compile(
    r"\b(?:who\s+(?:is|are|was|were)\s+(?:the\s+|this\s+)?(?:author|authors|writer|writers|people|person)"
    r"|about\s+the\s+(?:author|authors|writer|writers|people)"
    r"|tell\s+me\s+about\s+the\s+(?:author|authors|writer|writers)"
    r"|authors?\s+of\s+(?:this|the)\s+(?:paper|article|study|report)"
    r"|who\s+wrote\s+(?:this|the)?\s*(?:paper|article|report)?)\b", re.I)

# bare follow-ups - resolved against the previous turn instead of retrieval
FOLLOWUP_RE = re.compile(
    r"^(?:tell\s+me\s+more|more|why\??|elaborate|explain\s+(?:more|that|further)?|go\s+on|continue|"
    r"what\s+else|expand|details?\??|say\s+more|and\s*\??|examples?\??|summarize\s+that)[\s?.!]*$",
    re.I)

# factual metadata questions - answered from the paper record itself
STATS_RE = re.compile(
    r"\b(?P<year>what\s+year|when\s+(?:was|were)\s+(?:it|this|the\s+paper)\s+published"
    r"|how\s+old\s+is\s+(?:it|this\s+paper)"
    r"|(?P<pages>how\s+many\s+pages)"
    r"|(?P<cites>how\s+many\s+(?:citations?|times\s+cited|citing\s+works))"
    r"|(?P<venue>which\s+(?:venue|journal|conference)|where\s+(?:was\s+(?:it|this|the\s+paper)\s+published))"
    r"|(?P<authors_n>how\s+many\s+authors?))\b", re.I)

ELI5_RE = re.compile(
    r"\b(?:explain\s+(?:it|this|that|them)?\s*like\s+(?:a\s+|an\s+|im\s+|i'm\s+)?(?:10|ten|five|5|kid|child)"
    r"|explain\s+(?:it|this|that|them)?\s*(?:simply|in\s+plain)"
    r"|eli5|in\s+simple\s+(?:words|terms|english)|explain\s+simply|simpler\s+please|dumb\s+it\s+down)\b", re.I)


@dataclass
class Answer:
    text: str
    evidence: list = field(default_factory=list)
    mode: str = "extractive"
    insufficient: bool = False
    suggestions: list = field(default_factory=list)
    cached: bool = False


def _terms(q: str) -> set:
    return {t for t in re.findall(r"[a-z0-9]{2,}", q.lower()) if t not in STOPWORDS}


def _stem(t: str) -> str:
    for suf in ("ation", "ments", "ment", "ings", "ing", "ies", "ers", "er", "ed", "es", "s"):
        if len(t) > 5 and t.endswith(suf):
            return t[: -len(suf)]
    return t


def _match_terms(qterms: set, sterms: set) -> int:
    n = 0
    for qt in qterms:
        if qt in sterms or any(st == qt or st.startswith(qt) or qt.startswith(st) or
                               _stem(st) == _stem(qt) for st in sterms):
            n += 1
    return n


def _extractive(query: str, passages: list) -> Answer:
    qterms = _terms(query)
    scored = []
    for rank, p in enumerate(passages):
        passage_sents = _passage_sentences(p.text)
        for idx, s in enumerate(passage_sents):
            sterms = set(re.findall(r"[a-z0-9]{2,}", s.lower()))
            overlap = _match_terms(qterms, sterms)
            coverage = overlap / max(len(qterms), 1)
            position_bonus = 0.15 * (1 - idx / max(len(passage_sents), 1))
            score = 0.55 * coverage + 0.45 * (p.score if p.score <= 1 else p.score / 100) + position_bonus
            if overlap >= 1:
                scored.append((round(score, 4), -rank, idx, s, p))
    if not scored:
        # rescue for table/formula-heavy passages where every sentence was
        # filtered as too short: fall back to the raw passage text
        for rank, p in enumerate(passages[:2]):
            raw = " ".join((p.text or "").split())
            if raw and _match_terms(qterms, set(re.findall(r"[a-z0-9]{2,}", raw.lower()))):
                scored.append((round(0.3 - 0.1 * rank, 4), -rank, 0, raw[:420], p))
    scored.sort(key=lambda x: (-x[0], -x[1], x[2]))
    picked = []
    seen_starts = set()
    for score, negrank, idx, sent, p in scored[:60]:
        key = sent[:60].lower()
        if key in seen_starts:
            continue
        seen_starts.add(key)
        picked.append((sent, p))
        if len(picked) >= 4:
            break
    if not picked:
        closest = next((p for p in passages if (p.text or "").strip()), None)
        if closest:
            excerpt = clean_pdf_text(re.sub(r"^\[[^\]]+\]\s*", "", closest.text or ""))[:380]
            return Answer(
                text=("I couldn't find sufficient evidence for a direct answer to that question. "
                      "The closest retrieved passage is:\n\n" + excerpt),
                evidence=[closest], insufficient=True,
            )
        return Answer(
            text=("I couldn't find sufficient evidence in the available sources to answer this "
                  "confidently. Try rephrasing, or upload the specific paper this question is about."),
            insufficient=True,
            suggestions=[
                "What is this paper about?",
                "What methodology did the authors use?",
                "What are the stated limitations?",
                "Which datasets were used?",
            ],
        )
    body_parts = []
    used_evidence = []
    by_passage = {}
    for sent, p in picked:
        by_passage.setdefault(id(p), []).append(sent)
        if all(p.paper_id != e.paper_id or p.chunk_id != e.chunk_id for e in used_evidence):
            used_evidence.append(p)
    passage_order = {id(p): i for i, p in enumerate(passages)}
    for pid in sorted(by_passage, key=lambda k: passage_order.get(k, 99)):
        body_parts.append(" ".join(by_passage[pid]))
    if len(body_parts) > 1:
        # multiple evidence passages → readable bullets instead of a blob
        lead = body_parts[0]
        answer_text = lead + "\n" + "\n".join("• " + part for part in body_parts[1:])
    else:
        answer_text = " ".join(body_parts)
    # the paper's own bibliography markers ("... [35, 2, 5]") read like broken
    # evidence cites in chat - drop them from the composed answer only
    answer_text = re.sub(r"\s*\[\d+(?:\s*,\s*\d+)*\]", "", answer_text)
    if len(answer_text) > 1400:
        answer_text = answer_text[:1400].rsplit(" ", 1)[0] + " ..."
    return Answer(text=answer_text, evidence=used_evidence, mode="extractive")


def _synthesize(query: str, passages: list, paper_title: str,
                abstract: str = "", history: list | None = None,
                owner: str = "", style_hint: str = ""):
    """Evidence-locked LLM synthesis through the provider chain."""
    from app.rag import llm_providers
    return llm_providers.synthesize(query, passages, paper_title,
                                    abstract=abstract, history=history,
                                    owner=owner, style_hint=style_hint)


POSTPUB_RE = re.compile(
    r"\b(?:what\s+happened\s+(?:after|since)|after\s+(?:it|this|the\s+paper)\s+(?:was\s+)?published|"
    r"since\s+(?:it|this|the\s+paper)\s+(?:was\s+)?published|impact\s+(?:of|after)|"
    r"(?:its|the\s+paper'?s?)\s+(?:impact|influence|legacy)|what\s+happened\s+since)\b", re.I)



def _postpub_answer(query: str, db, paper: Paper) -> Answer | None:
    """Post-publication questions answered from the tracked timeline events and
    the citation record - honest about gaps when the paper isn't indexed."""
    if not POSTPUB_RE.search(query):
        return None
    from app.models.entities import ResearchEvent
    events = db.query(ResearchEvent).filter(
        ResearchEvent.paper_id == paper.id).order_by(ResearchEvent.date_year).all()
    substantive = [e for e in events if e.kind != "publication"]
    n_citing = sum(1 for c in paper.citations if c.direction == "citing")

    if not paper.openalex_id:
        return Answer(
            text=("This paper hasn't been matched in the OpenAlex research database, so "
                  "post-publication tracking (citations, follow-ups, replications) isn't "
                  "available for it. This is common for niche, historical, or non-indexed "
                  "work - it doesn't reflect on the paper itself."),
            mode="extractive",
        )
    if not substantive and n_citing == 0:
        return Answer(
            text=("No post-publication events were found in the research databases: no "
                  "citing works, follow-ups or replications are indexed yet. That's common "
                  "for recent or niche papers - check back later or browse the News and "
                  "Timeline tabs for updates."),
            mode="extractive",
        )

    parts = [f"Since its {paper.year} publication, {n_citing} citing works are indexed."
             if n_citing else
             f"Since its {paper.year} publication, no citing works are indexed yet."]
    shown = 0
    for e in substantive:
        if shown >= 4:
            break
        kind_label = {"followup": "Follow-up", "replication": "Replication",
                      "contradiction": "Contradiction", "correction": "Correction",
                      "newdataset": "New dataset", "milestone": "Milestone"}.get(e.kind, e.kind)
        parts.append(f"- {e.date_year or 'Undated'}: {kind_label} - {(e.title or '')[:110]}"
                     + (f" [source: {e.source_name}]" if e.source_name else ""))
        shown += 1
    if shown < len(substantive):
        parts.append(f"- ...and {len(substantive) - shown} more events in the Timeline tab.")
    text = chr(10).join(parts)[:1100]
    evidence = [Retrieved(chunk_id=0, paper_id=paper.id, paper_title=paper.title or "Paper",
                          section="timeline", page=None,
                          text="; ".join(f"{e.kind}:{e.title}" for e in substantive[:4]) or
                          f"{n_citing} citing works", score=1.0)]
    return Answer(text=text, evidence=evidence, mode="extractive")


def _stats_answer(query: str, paper: Paper) -> Answer | None:
    """Factual questions (year, pages, citations, venue) from the paper record."""
    m = STATS_RE.search(query)
    if not m:
        return None
    asked_any = False
    facts: list[str] = []
    if m.group("year") or m.group("venue"):
        asked_any = True
        if paper.year and paper.venue:
            facts.append(f"It was published in {paper.year}" + (f" in {paper.venue}." if paper.venue else "."))
        elif paper.year:
            facts.append(f"It was published in {paper.year}.")
        elif paper.venue:
            facts.append(f"It appeared in {paper.venue}; the exact year isn't identified.")
        else:
            facts.append("No publication year or venue was identified for this paper.")
    if m.group("pages"):
        asked_any = True
        facts.append(f"The uploaded PDF has {paper.page_count or '?'} pages.")
    if m.group("cites"):
        asked_any = True
        n = sum(1 for c in paper.citations if c.direction == "citing")
        facts.append(f"{n} citing works are indexed" + (" from the research databases." if paper.openalex_id
                                                     else " - the paper isn't matched in the research databases, so this may be incomplete."))
    if m.group("authors_n"):
        asked_any = True
        people = [a.get("name") for a in (paper.authors or []) if a.get("name")]
        if people:
            facts.append(f"{len(people)} authors are listed: " + ", ".join(people[:6]) + ".")
        else:
            facts.append("No author names were identified in the paper.")
    if not asked_any:
        return None
    text = " ".join(facts)[:700]
    evidence = [Retrieved(chunk_id=0, paper_id=paper.id, paper_title=paper.title or "Paper",
                          section="metadata", page=1,
                          text=f"year={paper.year} venue={paper.venue} pages={paper.page_count}",
                          score=1.0)]
    return Answer(text=text, evidence=evidence, mode="extractive")


def _paper_insight_answer(query: str, db, paper: Paper) -> Answer | None:
    """Answer common factual questions from the verified paper decomposition."""
    wants_results = bool(RESULTS_INTENT_RE.search(query))
    wants_datasets = bool(DATASET_INTENT_RE.search(query))
    wants_metrics = bool(METRIC_INTENT_RE.search(query))
    wants_limits = bool(LIMIT_INTENT_RE.search(query))
    wants_methods = bool(METHOD_INTENT_RE.search(query))
    wants_claim = bool(CLAIM_INTENT_RE.search(query))
    wants_support = bool(SUPPORT_INTENT_RE.search(query))
    if not any((wants_results, wants_datasets, wants_metrics, wants_limits, wants_methods, wants_claim, wants_support)):
        return None

    from app.services.analysis.xray import build_xray
    xray = build_xray(db, paper)
    parts: list[str] = []
    evidence: list[Retrieved] = []

    def cite(field) -> None:
        if not field:
            return
        evidence.append(Retrieved(
            chunk_id=0, paper_id=paper.id, paper_title=paper.title or "Paper",
            section=field.evidence_section, page=field.evidence_page,
            text=field.evidence_snippet or field.text[:300], score=1.0,
        ))

    if wants_claim or wants_support:
        if xray.main_claim:
            parts.append("The document's stated claim or central focus: " + xray.main_claim.text)
            cite(xray.main_claim)
        else:
            parts.append("No explicit central claim was identified in the uploaded document.")
    if wants_support:
        support = [f for f in [xray.dataset, xray.evaluation, *xray.main_results[:2]] if f]
        if support:
            parts.append("Supporting material reported in the document:\n" + "\n".join(f"• {f.text}" for f in support))
            for field in support:
                cite(field)
            parts.append("These are the document's own reports; they do not independently verify its claim.")
        else:
            parts.append("No explicit dataset, measured evaluation, or empirical result was identified to verify this claim. "
                         "The document may develop an argument or proposal; its statements alone do not establish that the claim is true.")

    if wants_methods:
        if xray.methodology:
            parts.append("Method or approach stated in the document: " + xray.methodology.text)
            cite(xray.methodology)
        else:
            parts.append("No explicit methodology or approach was identified in this document. "
                         "A historical description, speech, or theoretical discussion may not report "
                         "an experimental method. I cannot infer one from unrelated passages.")
    if wants_datasets:
        datasets = (paper.meta or {}).get("datasets") or []
        parts.append("Datasets: " + (", ".join(datasets[:8]) if datasets else "none identified with confidence") + ".")
        cite(xray.dataset)
    if wants_metrics:
        metrics = (paper.meta or {}).get("metrics") or []
        metrics_note = ", ".join(metrics[:8])
        if xray.evaluation and xray.evaluation.text:
            eval_text = xray.evaluation.text.strip()
            extra = (" (metrics: " + metrics_note + ")"
                     if metrics_note and metrics_note.lower() not in eval_text.lower() else "")
            if eval_text.lower().startswith("evaluation"):
                parts.append(eval_text + extra)
            else:
                parts.append("How they evaluated: " + eval_text + extra)
        else:
            parts.append("Evaluation metrics: " + (metrics_note if metrics_note else "none identified with confidence") + ".")
        cite(xray.evaluation)
    if wants_results:
        if xray.main_results:
            parts.append("Main reported results:\n" + "\n".join(
                f"• {field.text}" for field in xray.main_results[:3]))
            for field in xray.main_results[:3]:
                cite(field)
        else:
            parts.append("No clear result statement was extracted from the available paper text.")
    if wants_limits:
        if xray.limitations:
            parts.append("Stated limitations:\n" + "\n".join(
                f"• {field.text}" for field in xray.limitations[:3]))
            for field in xray.limitations[:3]:
                cite(field)
        else:
            parts.append("I did not detect an explicit limitations statement in this PDF. "
                         "That does not establish that the work has no limitations; check the discussion and later studies.")
    if not parts:
        # nothing extractable for this question (e.g. a strategy document with
        # no methodology section) - let the normal retrieval path answer instead
        # of serving an empty answer bubble
        return None
    return Answer(text="\n\n".join(parts)[:1800], evidence=evidence, mode="extractive",
                  insufficient=not bool(evidence) or (wants_support and not bool(support)))


def _eli5_answer(query: str, db, paper: Paper, owner: str = "",
                 use_llm: bool = True) -> Answer | None:
    """'Explain it simply' - reuses the ELI5 engine inside the chat."""
    if not ELI5_RE.search(query):
        return None
    from app.services.analysis.xray import build_xray
    if use_llm:
        from app.services.analysis.eli5 import build_eli5
        fields = build_eli5(db, paper, owner=owner).get("fields") or {}
    else:
        # Local is a per-answer promise even if this session previously used
        # cloud explanations. Do not enter the cloud rewrite/cache path.
        from app.services.analysis.eli5 import _deterministic_eli5
        fields = _deterministic_eli5(build_xray(db, paper))
        fields.pop("__intro__", None)
    if not fields:
        return None
    order = [("main_claim", "What the document says"),
             ("methodology", "Its method or approach"),
             ("main_results", "Key results"),
             ("research_question", "The question they asked"),
             ("dataset", "Examples they used"),
             ("limitations", "What it can't do yet")]
    parts = []
    for key, label in order:
        if fields.get(key):
            parts.append(f"{label}: {fields[key]}")
    if not parts:
        return None
    text = "Here's the simple version:\n" + "\n".join(f"• {p}" for p in parts[:4])[:1200]
    xray = build_xray(db, paper)
    evidence = [Retrieved(chunk_id=0, paper_id=paper.id, paper_title=paper.title or "Paper",
                          section=f.evidence_section, page=f.evidence_page,
                          text=f.evidence_snippet or f.text[:400], score=1.0)
                for key, _ in order for f in [getattr(xray, key, None)]
                if key in fields and f is not None and not isinstance(f, list)]
    return Answer(text=text, evidence=evidence, mode="eli5")


def _is_followup(query: str) -> bool:
    return bool(FOLLOWUP_RE.match(query.strip()))


def _expand_followup(db, paper_id: str, owner: str, token: str, query: str):
    """Bare follow-ups ('tell me more', 'why?') are resolved against the
    previous turn: retrieval runs on the earlier question plus the follow-up."""
    from app.models.entities import ChatMessage
    from app.security.owner import decrypt_text
    last_user = db.query(ChatMessage).filter(
        ChatMessage.paper_id == paper_id, ChatMessage.owner == owner,
        ChatMessage.role == "user").order_by(ChatMessage.created_at.desc()).first()
    if not last_user:
        return None
    prev_q = decrypt_text(token, last_user.content) or ""
    if len(prev_q) < 4:
        return None
    expanded = f"{prev_q} {' '.join(_terms(query))}"
    return prev_q, expanded


def _author_answer(query: str, paper_ids) -> Answer | None:
    """Author questions answered from metadata + OpenAlex profiles - never from
    quote pages or epigraphs."""
    if not AUTHOR_INTENT_RE.search(query):
        return None
    db = SessionLocal()
    try:
        q = db.query(Paper).filter(Paper.status == "ready")
        if paper_ids:
            q = q.filter(Paper.id.in_(paper_ids))
        papers = q.all()
        paper = papers[0] if len(papers) == 1 else None
        if paper is None and papers:
            for p in papers:
                t = set(re.findall(r"[a-z0-9]{3,}", ((p.title or "") + " " + (p.abstract or "")).lower()))
                if t & _terms(query):
                    paper = p
                    break
            paper = paper or papers[0]
        if paper is None:
            return None
        from app.ingestion.metadata import is_junk_author_name
        from app.models.entities import Author
        meta = paper.meta or {}
        oa_authors = meta.get("openalex_authors") or []

        class _P:
            def __init__(self, name, affiliation=None, h_index=None, works_count=None,
                         research_areas=None):
                self.name, self.affiliation = name, affiliation
                self.h_index, self.works_count = h_index, works_count
                self.research_areas = research_areas or []

        people = []
        seen = set()
        # OpenAlex authorships are the authoritative record when the paper matched
        for a in oa_authors:
            nm = (a.get("name") or "").strip()
            if nm and not is_junk_author_name(nm) and nm.lower() not in seen:
                seen.add(nm.lower())
                people.append(_P(nm, a.get("affiliation")))
        if not people:
            rows = db.query(Author).filter(Author.paper_id == paper.id,
                                           Author.role == "original").order_by(Author.position).all()
            for a in rows:
                if not a.name or is_junk_author_name(a.name):
                    continue
                key = a.name.lower()
                if key in seen:
                    continue
                seen.add(key)
                people.append(a)
        if not people:
            for a in (paper.authors or []):
                name = (a.get("name") or "").strip()
                if name and not is_junk_author_name(name) and name.lower() not in seen:
                    seen.add(name.lower())
                    people.append(_P(name, a.get("affiliation")))
        names = ", ".join(p.name for p in people[:6]) if people else None
        if not names:
            return Answer(
                text=("The uploaded PDF didn't contain identifiable author metadata, and the "
                      "research databases don't list authors for this record. I won't guess - "
                      "check the paper's first page for the author list."),
                mode="extractive",
            )
        parts = ["The paper " + chr(0x201C) + ((paper.title or "Untitled")[:90]) + chr(0x201D)
                 + (f" ({paper.year}" if paper.year else " (")
                 + (f", {paper.venue})" if paper.venue else ")")
                 + f" lists {len(people)} author" + ("s" if len(people) > 1 else "")
                 + f": {names}."]
        profiled = [p for p in people if getattr(p, "h_index", None) or getattr(p, "affiliation", None)
                    or getattr(p, "research_areas", None) or getattr(p, "works_count", None)]
        if profiled:
            detail = "; ".join(
                p.name + (f" ({p.affiliation})" if p.affiliation else "")
                + (f", h-index {p.h_index}" if p.h_index else "")
                + (f", {p.works_count} indexed works" if p.works_count else "")
                for p in profiled[:4])
            parts.append(f"From indexed research profiles: {detail}.")
        elif not meta.get("openalex_authors"):
            parts.append("The name comes from the paper's own text; the research databases "
                         "don't list a research profile for it.")
        answer_text = " ".join(parts)[:900]
        evidence = [Retrieved(chunk_id=0, paper_id=paper.id, paper_title=paper.title or "Paper",
                              section="metadata", page=1, text=f"Authors: {names}", score=1.0)]
        return Answer(text=answer_text, evidence=evidence, mode="extractive")
    finally:
        db.close()


JUNK_RE = re.compile(
    r"arXiv:\d{4}\.\d{4,5}|proceedings of the \d+\w+ (conference|annual)|"
    r"published as a conference paper|^\s*\d+(st|nd|rd|th)\s+(conference|workshop)|"
    r"long beach|under review|preprint|[†‡]\s*work performed|all rights reserved|"
    r"copyright|association for computational linguistics|www\.|https?://|"
    r"papers (?:in this|series)|pardee (?:papers|center)|series editor|editor.s note|"
    r"this series|programs of research, publications|your (?:mind|consciousness)|"
    r"the views expressed|trustees of|permission to reproduce|reprinted with|"
    r"\b(?:all rights reserved|bay state road|publisher address|telephone|fax)\b", re.I)


def _sentences(text: str) -> list:
    sents = pdf_sentences(text, min_len=35, max_len=450)
    return [s for s in sents if not JUNK_RE.search(s)]


def _overview_passages(paper: Paper) -> list[Retrieved]:
    """Use the upload's summary and opening sections, rather than title hits
    that may select a bibliography, cover footer, or quotation page."""
    chunks = list(getattr(paper, "chunks", None) or [])
    priority = {"abstract": 0, "executive summary": 0, "overview": 1,
                "goal and objectives": 1,
                "introduction": 1, "conclusion": 3, "front matter": 5}
    chunks = [c for c in chunks if (c.section_name or "").lower()
              not in {"references", "acknowledgements", "keywords", "contents", "preface", "about the author", "image credits"}]
    chunks.sort(key=lambda c: (priority.get((c.section_name or "").lower(), 2),
                               c.page or 1, c.id or 0))
    passages = []
    counts = {}
    for chunk in chunks:
        key = (chunk.section_name or "document").lower()
        text = re.sub(r"^\[[^\]]+\]\s*", "", chunk.text or "")
        sents = _sentences(text)
        if not sents or counts.get(key, 0) >= 2:
            continue
        if len(sents) < len(pdf_sentences(text, min_len=35, max_len=450)) / 2:
            continue
        passages.append(Retrieved(chunk.id, paper.id, paper.title or "Document",
                                  chunk.section_name, chunk.page, text, 1.0))
        counts[key] = counts.get(key, 0) + 1
        if len(passages) >= 6:
            break
    return passages


def _abstract_answer(query: str, paper_ids, db=None, owner: str = "",
                     use_llm: bool = True) -> Answer | None:
    """Meta-questions ("what is this paper about") answered from the paper's
    OWN content: retrieval over its real chunks (abstract + introduction), then
    LLM-written when configured, else composed extractively. This beats the
    stored abstract, which is sometimes a wrong repository description."""
    if not META_INTENT_RE.search(query):
        return None
    owns_db = db is None
    db = db or SessionLocal()
    try:
        q = db.query(Paper).filter(Paper.status == "ready")
        if paper_ids:
            q = q.filter(Paper.id.in_(paper_ids))
        papers = q.all()
        best = None
        if len(papers) == 1:
            best = papers[0]
        elif papers:
            for p in papers:
                t = set(re.findall(r"[a-z0-9]{3,}", ((p.title or "") + " " + (p.abstract or "")).lower()))
                if t & _terms(query):
                    best = p
                    break
            best = best or papers[0]
        if best is None:
            return None

        # retrieve the paper's own content about itself (staleness guard inside)
        passages = _overview_passages(best) or retrieve(
            f"What is {best.title} about?", paper_ids=[best.id], k=4)
        focus_passages = []
        if not best.abstract:
            from app.services.analysis.xray import build_xray, CLAIM_RE, FOCUS_RE
            xray = build_xray(db, best)
            for field in (xray.research_question, xray.main_claim):
                if (field and (CLAIM_RE.search(field.text) or FOCUS_RE.search(field.text))
                        and all(field.text != p.text for p in focus_passages)):
                    focus_passages.append(Retrieved(0, best.id, best.title or "Document",
                        field.evidence_section, field.evidence_page, field.text, 1.0))
            passages = focus_passages + passages[:3]
        if not passages:
            return None

        # LLM-written version when configured
        if use_llm and llm_configured(owner):
            synth = _synthesize("Describe what this paper is about for a reader who has not opened it.",
                                passages, best.title, abstract=best.abstract[:1200],
                                owner=owner)
            if synth:
                text, provider = synth
                insufficient = "couldn't find sufficient evidence" in text.lower()
                if not insufficient:
                    return Answer(text=text, evidence=passages,
                                  mode=f"synthesized:{provider}", cached=provider == "cache")

        # Compose from the uploaded document's own evidence. Repository
        # descriptions can be mismatched, and many reports have no abstract.
        focus = _terms((best.title or "") + " " + " ".join(getattr(best, "keywords", None) or []))
        def overview_score(p):
            if (p.section or "").lower() in {"abstract", "executive summary"}:
                return 100
            sents = _sentences(p.text)
            return max((_match_terms(focus, set(re.findall(r"[a-z0-9]{2,}", s.lower()))) for s in sents), default=-1)
        preferred = focus_passages[0] if focus_passages else max(passages, key=overview_score)
        text = clean_pdf_text(re.sub(r"^\[[^\]]+\]\s*", "", preferred.text or ""))
        sents = _sentences(text)
        if (preferred.section or "").lower() not in {"abstract", "executive summary"}:
            relevant = [s for s in sents if _match_terms(
                focus, set(re.findall(r"[a-z0-9]{2,}", s.lower()))) >= 1]
            sents = relevant or sents
        if not sents:
            sents = [text[:600]] if text.strip() else []
        if not sents:
            return None
        answer_text = " ".join(sents[:4])
        if len(answer_text) > 1100:
            answer_text = answer_text[:1100].rsplit(" ", 1)[0] + " ..."
        if not best.abstract:
            answer_text = "From the uploaded document's " + (preferred.section or "text") + ": " + answer_text
        else:
            answer_text = "The document states: " + answer_text
        evidence = [preferred]
        return Answer(text=answer_text, evidence=evidence, mode="extractive")
    finally:
        if owns_db:
            db.close()


def _resolve_followup(query: str, paper_ids, owner: str = "", token: str = ""):
    """Bare follow-ups ('tell me more', 'why?') continue the previous turn:
    retrieval runs on the earlier question plus the follow-up words."""
    if not _is_followup(query):
        return None, None
    db = SessionLocal()
    try:
        paper_id = (paper_ids or [""])[0]
        got = _expand_followup(db, paper_id, owner, token, query)
        if not got:
            return None, None
        prev_q, expanded = got
        passages = retrieve(expanded, paper_ids=paper_ids)
        if not passages:
            return None, None
        ans = _extractive(expanded, passages)
        ans.text = "On " + chr(0x201C) + prev_q[:80] + chr(0x201D) + " - " + ans.text
        return ans, passages
    finally:
        db.close()


def _resolve_meta(query: str, paper_ids, db=None, token: str = "",
                  owner: str = "", prefer_llm: bool = False,
                  use_llm: bool = True):
    """Intent routing shared by sync and streaming paths.
    Order: author -> stats -> insight -> post-publication -> ELI5 -> abstract.

    With `prefer_llm` (cloud synthesis configured) the composed insight and
    ELI5 answers are not served directly - they come back as `soft`, a better-
    than-raw fallback used only when synthesis fails or admits insufficiency.
    Returns (answer | None, paper | None, soft | None)."""
    author = _author_answer(query, paper_ids)
    if author:
        return author, None, None
    owns_db = db is None
    db = db or SessionLocal()
    try:
        paper = None
        if paper_ids:
            paper = db.query(Paper).filter(Paper.id == paper_ids[0], Paper.status == "ready").first()
        soft: Answer | None = None
        if paper is not None:
            stats = _stats_answer(query, paper)
            if stats:
                return stats, paper, None
            insight = _paper_insight_answer(query, db, paper)
            if insight:
                if prefer_llm:
                    soft = insight
                else:
                    return insight, paper, None
            postpub = _postpub_answer(query, db, paper)
            if postpub:
                return postpub, paper, None
            eli5 = _eli5_answer(query, db, paper, owner=owner, use_llm=use_llm)
            if eli5:
                if prefer_llm:
                    soft = soft or eli5
                else:
                    return eli5, paper, None
        meta = _abstract_answer(query, paper_ids, db=db, owner=owner, use_llm=use_llm)
        return meta, paper, soft
    finally:
        if owns_db:
            db.close()


ELI5_STYLE_HINT = ("Style: plain language for a newcomer to the field - short sentences, "
                   "no unexplained jargon.\n\n")


def _usable_soft(soft: Answer | None) -> Answer | None:
    """A deterministic fallback is only usable when it actually says something."""
    if soft is not None and soft.text and soft.text.strip():
        return soft
    return None


def _refusal(text: str) -> bool:
    return "couldn't find sufficient evidence" in (text or "").lower()


def _bridged_fallback(llm_text: str, soft: Answer | None, ans: Answer) -> Answer:
    """Turn a bare 'no evidence' refusal into something useful: keep the honesty,
    then show the closest retrieved content instead of a dead end."""
    soft_ans = _usable_soft(soft)
    if soft_ans:
        return soft_ans
    if _refusal(llm_text) and ans.text and not _refusal(ans.text):
        return Answer(
            text=("I couldn't find a direct answer to that in the passages I retrieved - "
                  "here is the closest relevant content from the paper:\n\n" + ans.text),
            evidence=ans.evidence, mode="extractive",
        )
    if llm_text and llm_text.strip():
        return Answer(text=llm_text, evidence=ans.evidence,
                      mode="extractive", insufficient=_refusal(llm_text))
    return ans


# Non-ML documents (policy papers, strategy documents, essays) rarely contain
# the literal academic words users ask with - widen retrieval when an intent
# word is present so "methodology" also finds "approach/strategy/design".
INTENT_EXPANSIONS: dict[str, str] = {
    "methodolog": "approach strategy design architecture how it works plan",
    "result": "findings outcomes achievements conclusions accomplishments",
    "limitation": "challenges weaknesses caveats concerns risks drawbacks",
    "dataset": "data samples benchmarks sources collection",
    "contribution": "innovation novelty advances breakthrough",
}


def _expand_query(query: str, paper: Paper | None = None) -> str:
    q = query.lower()
    extra = " ".join(terms for key, terms in INTENT_EXPANSIONS.items() if key in q)
    meta = (getattr(paper, "meta", None) or {}) if paper is not None else {}
    # These terms were extracted from this document's own sections. Limit the
    # expansion so generic paper vocabulary cannot drown out the question.
    keys = []
    if re.search(r"method|approach|strateg|design|framework", q):
        keys.append("methods")
    if re.search(r"dataset|\bdata\b|corpus|samples?|sources?", q):
        keys.append("datasets")
    if re.search(r"result|finding|outcome|perform|evaluat|accuracy|score", q):
        keys.append("metrics")
    specific = []
    for key in keys:
        for value in (meta.get(key) or [])[:3]:
            if isinstance(value, str) and 3 <= len(value) <= 70 and value.lower() not in q:
                specific.append(value)
    tail = " ".join(filter(None, [extra, *specific]))
    return f"{query} {tail}" if tail else query


def answer_question(query: str, paper_ids=None, owner: str = "", token: str = "",
                    answer_mode: Literal["local", "cloud"] | None = None) -> Answer:
    author = _author_answer(query, paper_ids)
    if author:
        return author
    llm_on = answer_mode != "local" and llm_configured(owner)
    meta, paper, soft = _resolve_meta(query, paper_ids, owner=owner,
                                     prefer_llm=llm_on, use_llm=llm_on)
    if meta:
        return meta
    follow, f_passages = _resolve_followup(query, paper_ids, owner, token)
    if follow:
        return follow
    passages = retrieve(_expand_query(query, paper), paper_ids=paper_ids)
    if not passages:
        return Answer(
            text=("I couldn't find passages in this paper matching that question. Try "
                  "rephrasing it, or ask about a different aspect of the paper."),
            insufficient=True,
        )
    ans = _usable_soft(soft) or _extractive(query, passages)
    if not ans.text or not ans.text.strip():
        return Answer(text="I couldn't form a reliable answer from the retrieved passages. "
                           "Try a more specific question about this document.",
                      evidence=passages, insufficient=True)
    if llm_on:
        first_paper = passages[0].paper_title
        db = SessionLocal()
        try:
            paper_row = db.query(Paper).filter(Paper.id.in_(paper_ids or [""])).first()
            abstract = (paper_row.abstract or "")[:1200] if paper_row else ""
            history = _recent_history(db, paper_row.id, owner, token) if paper_row else []
        finally:
            db.close()
        hint = ELI5_STYLE_HINT if ELI5_RE.search(query) else ""
        synth = _synthesize(query, passages, first_paper,
                            abstract=abstract, history=history, owner=owner,
                            style_hint=hint)
        if synth:
            text, provider = synth
            if _refusal(text):
                return _bridged_fallback(text, soft, ans)
            return Answer(text=text, evidence=passages, mode=f"synthesized:{provider}",
                          insufficient=False, cached=provider == "cache")
    return ans


def build_suggestions(db, paper) -> list[str]:
    """Follow-up questions tailored to what THIS paper actually contains -
    derived from its extracted sections, metadata, and citation signals.
    Accepts a bound Paper or a paper id; only answerable questions are offered."""
    from app.models.entities import PaperSection
    if isinstance(paper, str):
        paper = db.query(Paper).filter(Paper.id == paper).first()
        if not paper:
            return []
    else:
        paper = db.merge(paper)
    meta = paper.meta or {}
    methods = meta.get("methods") or []
    datasets = meta.get("datasets") or []
    metrics = meta.get("metrics") or []
    if isinstance(paper, str):
        paper = db.query(Paper).filter(Paper.id == paper).first()
        if not paper:
            return []
    else:
        paper = db.merge(paper)
    sections = {s.name.lower() for s in db.query(PaperSection)
                .filter(PaperSection.paper_id == paper.id).all()}
    n_citing = sum(1 for c in paper.citations if c.direction == "citing")
    n_contra = sum(1 for c in paper.citations if c.is_contradictory)
    valid_authors = [a.get("name") for a in (paper.authors or [])
                     if a.get("name") and not _junk(a.get("name"))]

    out: list[str] = []
    if paper.abstract:
        out.append("What is this paper about?")
    if valid_authors:
        out.append("Tell me about the author")
    if methods:
        out.append("What methodology did the authors use?")
    if datasets:
        out.append("Which datasets were used?")
    if metrics or "results" in sections or "experiments" in sections:
        out.append("What were the main results?")
    if "limitations" in sections or "discussion" in sections or "conclusion" in sections:
        out.append("What are the stated limitations?")
    if n_contra:
        out.append("Who disagrees with this paper?")
    if n_citing:
        out.append("What happened after it was published?")
    if sections - {"front matter", "references", "acknowledgements"}:
        out.append("Explain it simply")
    if not paper.openalex_id:
        out.append("What year was it published?")
    return out[:5]


def _junk(name: str) -> bool:
    from app.ingestion.metadata import is_junk_author_name
    return is_junk_author_name(name)


def _recent_history(db, paper_id: str, owner: str, token: str, limit: int = 4) -> list[str]:
    """Last few decrypted turns as 'Role: text' - gives synthesis continuity."""
    from app.models.entities import ChatMessage
    msgs = db.query(ChatMessage).filter(
        ChatMessage.paper_id == paper_id, ChatMessage.owner == owner)         .order_by(ChatMessage.created_at.desc()).limit(limit).all()
    out = []
    for m in reversed(msgs):
        text = decrypt_text(token, m.content)
        if text:
            out.append(("User: " if m.role == "user" else "Assistant: ") + text[:300])
    return out


def stream_answer_events(query: str, paper_ids=None, owner: str = "", token: str = "",
                         answer_mode: Literal["local", "cloud"] | None = None):
    """SSE payload generator: evidence first, then answer tokens. Persists
    encrypted, mirroring the non-streaming /chat route.

    Never dies silently: any unexpected error is reported as an {"type":
    "error"} event followed by a done event, so the UI can recover instead of
    hanging on a half-open stream."""
    import json
    import time as _time

    import logging

    from app.models.entities import ChatMessage

    from app.security.session import SessionEnded, owner_write

    db = None
    paper_id = (paper_ids or [""])[0]
    flight_key = None
    flight = None
    flight_result = None

    def ev(obj) -> str:
        return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"

    def stream_text(text: str):
        for i in range(0, len(text), 80):
            yield ev({"type": "token", "text": text[i:i + 80]})
            _time.sleep(0.01)

    try:
        db = SessionLocal()
        ans: Answer | None = None
        passages: list = []
        llm_on = answer_mode != "local" and llm_configured(owner)

        # 1. intent routes: author → stats → insight/postpub/ELI5 → abstract
        meta, paper, soft = _resolve_meta(query, paper_ids, db=db, token=token,
                                          owner=owner, prefer_llm=llm_on,
                                          use_llm=llm_on)
        routed = False
        if meta:
            ans = meta
            routed = True
            passages = list(meta.evidence or [])
        else:
            # 2. bare follow-ups continue the previous turn
            follow, follow_passages = _resolve_followup(query, paper_ids, owner, token)
            if follow:
                ans = follow
                passages = list(follow_passages or follow.evidence or [])
            else:
                # 3. retrieval-grounded answer (LLM synthesis when configured)
                passages = retrieve(_expand_query(query, paper), paper_ids=paper_ids)
                if not passages:
                    ans = Answer(
                        text=("I couldn't find sufficient evidence in the available sources. "
                              "Upload a paper first, then ask about its content."),
                        insufficient=True,
                    )
                else:
                    ans = _extractive(query, passages)
        if ans is None or not ans.text or not ans.text.strip():
            ans = Answer(text="I couldn't form a reliable answer from the retrieved passages. "
                              "Try a more specific question about this document.",
                         evidence=passages, insufficient=True)
        if not passages and ans is not None and ans.evidence:
            passages = list(ans.evidence)

        if passages:
            yield ev({"type": "evidence", "evidence": [{
                "paper_id": e.paper_id, "paper_title": e.paper_title, "section": e.section,
                "page": e.page, "snippet": e.text[:400], "score": e.score,
            } for e in passages]})

        # conversation history must be read BEFORE this turn's user message is
        # persisted, or the model sees the current question twice
        want_history = llm_on and not routed and bool(passages)
        history = _recent_history(db, paper_id, owner, token) if want_history else []

        with owner_write(db, owner):
            if not db.query(Paper).filter(Paper.id == paper_id, Paper.owner == owner).first():
                raise SessionEnded("The document is no longer available.")
            db.add(ChatMessage(paper_id=paper_id, role="user", owner=owner,
                               content=encrypt_text(token, query)))

        mode = ans.mode
        text = ans.text
        cached_answer = ans.cached
        insufficient_answer = ans.insufficient
        used_stream = False
        if llm_on and not routed and passages:
            from app.rag import llm_providers
            from app.services.scholarly.common import cache_get, cache_set
            key = llm_providers._cache_key(query, passages, owner=owner)
            cached = cache_get(key)
            if (cached and isinstance(cached, dict) and cached.get("answer")
                    and llm_providers.valid_evidence_citations(cached["answer"], len(passages))):
                mode, text = "synthesized:cache", cached["answer"]
                cached_answer = True
                insufficient_answer = _refusal(text)
                yield from stream_text(text)
                used_stream = True
            else:
                flight_key = key
                leader, flight = llm_providers.claim_synthesis(key)
                if not leader:
                    shared = llm_providers.wait_synthesis(flight)
                    if (shared and shared.get("answer") and
                            llm_providers.valid_evidence_citations(shared["answer"], len(passages))):
                        text, mode, cached_answer = shared["answer"], "synthesized:cache", True
                        insufficient_answer = _refusal(text)
                    else:
                        fallback = _bridged_fallback("", soft, ans)
                        text, mode = fallback.text, fallback.mode
                        insufficient_answer = fallback.insufficient
                    yield from stream_text(text)
                    used_stream = True
                else:
                    # Another request can follow this one while the provider is
                    # streaming; the finally block releases it even on failure.
                    pass
            if not used_stream and flight is not None:
                abstract = (paper.abstract or "")[:1200] if paper else ""
                hint = ELI5_STYLE_HINT if ELI5_RE.search(query) else ""
                # try each configured provider in chain order; a provider that
                # dies mid-stream (rate limit, drop) hands off to the next one
                tried: set = set()
                for _attempt in range(4):
                    stream = llm_providers.synthesize_stream(
                        query, passages, abstract=abstract, history=history,
                        owner=owner, style_hint=hint, exclude=tried)
                    if not stream:
                        break
                    provider, iterator = stream
                    tried.add(provider)
                    mode = f"synthesized:{provider}"
                    parts = []
                    try:
                        for delta in iterator:
                            parts.append(delta)
                            yield ev({"type": "token", "text": delta})
                        raw_text = "".join(parts)
                        text = llm_providers.repair_evidence_citations(raw_text, len(passages))
                        if not llm_providers.valid_evidence_citations(text, len(passages)):
                            raise ValueError("answer lacks valid evidence citations")
                        if _refusal(text) and _usable_soft(soft):
                            # the model admitted defeat, but the deterministic
                            # decomposition can still answer - prefer it
                            raise ValueError("insufficient synthesis with deterministic fallback")
                        if _refusal(text):
                            # keep the honesty but bridge into the closest
                            # retrieved content instead of a bare dead end
                            bridged = _bridged_fallback(text, soft, ans)
                            text, mode = bridged.text, bridged.mode
                            insufficient_answer = bridged.insufficient
                            yield ev({"type": "fallback"})
                            yield from stream_text(text)
                            used_stream = True
                            break
                        if text != raw_text:
                            yield ev({"type": "fallback"})
                            yield from stream_text(text)
                        used_stream = True
                        insufficient_answer = False
                        from app.services.usage import record_usage
                        model_name = {"gemini": llm_providers.settings.gemini_model,
                                      "groq": llm_providers.settings.groq_model,
                                      "openai": llm_providers.settings.openai_model}[provider]
                        record_usage(owner, provider, model_name,
                                     llm_providers.SYSTEM_PROMPT + hint + abstract +
                                     " ".join(history) + llm_providers._build_context(passages) + query,
                                     text)
                        flight_result = {"answer": text, "provider": provider}
                        try:
                            cache_set(key, flight_result, hours=6)
                        except Exception:
                            logging.getLogger("scholarscout.chat").exception("answer cache write failed")
                        break
                    except Exception:
                        # Keep a cited partial answer if the provider fails after
                        # writing useful text. A partial without valid evidence
                        # cards cannot be shown as a generated claim.
                        partial = llm_providers.repair_evidence_citations("".join(parts), len(passages))
                        if partial and llm_providers.valid_evidence_citations(partial, len(passages)) and not _refusal(partial):
                            text = partial + "\n\nThe provider stopped early. Retry for a complete answer."
                            yield ev({"type": "fallback"})
                            yield from stream_text(text)
                            used_stream = True
                            insufficient_answer = False
                            # A partial answer must not be cached as complete.
                            break
                        parts = []
                        yield ev({"type": "fallback"})
                        continue
                if not used_stream:
                    fallback = _bridged_fallback("", soft, ans)
                    text = fallback.text
                    mode = fallback.mode
                    insufficient_answer = fallback.insufficient
                    yield from stream_text(text)
                    used_stream = True

        if not used_stream:
            yield from stream_text(text)

        with owner_write(db, owner):
            if not db.query(Paper).filter(Paper.id == paper_id, Paper.owner == owner).first():
                raise SessionEnded("The document is no longer available.")
            assistant = ChatMessage(
                paper_id=paper_id, role="assistant", owner=owner, mode=mode,
                content=encrypt_text(token, text),
                evidence=encrypt_json(token, [{
                    "paper_id": e.paper_id, "paper_title": e.paper_title, "section": e.section,
                    "page": e.page, "snippet": e.text[:400], "score": e.score,
                } for e in (passages or [])]),
            )
            db.add(assistant)
        db.refresh(assistant)
        try:
            paper_row = db.query(Paper).filter(Paper.id == paper_id).first()
            next_suggestions = build_suggestions(db, paper_row) if paper_row else []
        except Exception:
            next_suggestions = []
        yield ev({"type": "done", "message_id": assistant.id, "mode": mode,
                  "cached": cached_answer,
                  "insufficient": bool(insufficient_answer),
                  "suggestions": next_suggestions})
    except SessionEnded:
        yield ev({"type": "error", "message": "This research session has ended. Start a new session."})
        yield ev({"type": "done", "message_id": None, "mode": "ended",
                  "insufficient": True, "suggestions": []})
    except Exception as e:  # retrieval, x-ray, DB or provider blow-up: never hang the UI
        logging.getLogger("scholarscout.chat").exception("chat stream failed: %s", type(e).__name__)
        yield ev({"type": "error", "message": (
            "Something interrupted this answer. Please send the question again - "
            "if it keeps failing, try rephrasing or re-uploading the paper.")})
        yield ev({"type": "done", "message_id": None, "mode": "error",
                  "insufficient": True, "suggestions": []})
    finally:
        if flight_key and flight and not flight["event"].is_set():
            from app.rag import llm_providers
            llm_providers.finish_synthesis(flight_key, flight, flight_result)
        if db is not None:
            db.close()
