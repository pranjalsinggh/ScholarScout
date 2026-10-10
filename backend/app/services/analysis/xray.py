"""Research X-Ray - deterministic, evidence-linked paper decomposition.

Each field quotes the paper and points at the section/page it came from.
No LLM involved: every statement is extracted text or a transparent summary.
"""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.models.entities import Paper, PaperSection
from app.schemas.api import XRayField, XRayResponse
from app.services.analysis.text_units import clean_pdf_text, sentences

CLAIM_RE = re.compile(
    r"\b(we (propose|present|introduce|show|demonstrate|find|prove|argue|report|observe)|"
    r"this paper (proposes|presents|introduces|shows|holds|reviews|not only reviews|argues)|our (results|findings|approach|method)|"
    r"we (have )?(developed|designed|trained|achieved|recommend)|"
    r"this (report|essay|analysis|study) (argues|finds|recommends|shows)|"
    r"our recommendation|i (find|argue|recommend|believe))\b", re.I)
FOCUS_RE = re.compile(r"\b(?:the (?:fundamental |main |central )?(?:goal|aim|purpose|objective)|"
                      r"this (?:paper|report|document|essay|study|plan|vision)\s+(?:examines?|explores?|describes?|addresses?|considers?|reviews?)|"
                      r"we (?:investigate|examine|explore)|it is (?:critical|important) to (?:investigate|study|research|continue researching))\b", re.I)
APPROACH_RE = re.compile(r"\b(?:this (?:paper|study|analysis)|we (?:use|employ|analy[sz]|survey|compare)|"
                         r"our (?:approach|method|analysis)|the (?:approach|method|plan|model|strategy)|"
                         r"logical analysis|surveyed|interviewed)\b", re.I)
QUESTION_RE = re.compile(
    r"\b(remains? (unclear|uncertain|unknown|challenging)|open (problem|question|challenge)|"
    r"however,? (it|this|existing)|limited by|suffer(s|ing)? from|it is unclear|"
    r"little (is known|attention)|gap\b|hard to|difficult to|expensive to|cannot)\b", re.I)
RESULT_RE = re.compile(
    r"\b(outperform(s|ed)?|improv(s|es|ed|ement)|achiev\w*|state[- ]of[- ]the[- ]art|sota\b|"
    r"best (reported|known|results?)|significant(ly)?|superior|beats?\b|surpass\w*|"
    r"reduc\w*|increase[sd]?\b|gain(s|ed)?\b)\b|\b\d+(?:\.\d+)?\s?%(?!\w)", re.I)
PROSPECTIVE_RE = re.compile(r"\b(will|would|could|may|might|planned|projected|expected|"
                            r"recommend(?:ed|ation)?|propos(?:ed|al))\b", re.I)
ASSUMPTION_RE = re.compile(
    r"\b(we assume|assuming that|under the assumption|suppose that|"
    r"for simplicity|without loss of generality|it is assumed)\b", re.I)
REPRO_RE = re.compile(
    r"\b(github\.com|gitlab\.com|bitbucket|code (is|will be) (available|released|published)|"
    r"open[- ]sourc\w*|reproduc\w*|random seed|hyperparameter|GPU|TPU|hardware|"
    r"checkpoint|scripts? (are|will be) (available|released))\b", re.I)
DATASET_HINT_RE = re.compile(
    r"(\b\w+[\w-]*(?:\s+\w+[\w-]*){0,2}\s+(?:dataset|corpus|benchmark|database))", re.I)


def _sections(db: Session, paper_id: str) -> list[PaperSection]:
    return db.query(PaperSection).filter(PaperSection.paper_id == paper_id) \
        .order_by(PaperSection.position).all()


def _sec(sections, *names) -> PaperSection | None:
    for name in names:
        for section in sections:
            if section.name.lower() == name.lower():
                return section
    return None


JUNK_RE = re.compile(r"arXiv:\d{4}\.\d{4,5}|proceedings of the \d+\w+ (conference|annual)|"
                     r"published as a conference paper|^\s*\d+(st|nd|rd|th)\s+(conference|workshop)|"
                     r"long beach|under review|preprint|papers series|this series|series editor|"
                     r"publisher|pardee (?:papers|center)|editor.s note|views expressed|"
                     r"programs of research, publications|trustees of|all rights reserved|"
                     r"your (?:mind|consciousness)|transcends limitations", re.I)


def _sentences(text: str) -> list[str]:
    sents = sentences(text, min_len=35, max_len=450)
    cleaned = []
    for sent in sents:
        sent = re.sub(r"^(?:[A-Z][A-Za-z\-]+\s+){1,5}\(\d{3,4}[-–]\d{0,4}\)\s+(?=To\b|We\b|The\b|NASA\b|Our\b)", "", sent)
        sent = re.sub(r"^\s*(?:\d+(?:\.\d+)?\s*)?(?:Figure|Table)\s+\d+:[^.]*\.\s*", "", sent, flags=re.I)
        sent = re.sub(r"^\s*\d+(?:\.\d+)*\s+[A-Z][A-Za-z &/-]{3,60}?\s+(?=(?:We|The|On|In|For|Our|Encoder|Decoder)\b)",
                      "", sent)
        if len(sent) >= 35 and not JUNK_RE.search(sent):
            cleaned.append(sent)
    return cleaned


def _field(text: str, sec: PaperSection | None, max_len: int = 700,
           evidence_snippet: str | None = None, paper: Paper | None = None) -> XRayField:
    text = text.strip()
    if len(text) > max_len:
        text = text[:max_len].rsplit(" ", 1)[0] + " …"
    quote = evidence_snippet or text
    page = sec.page_start if sec else None
    if paper is not None and sec:
        needle = clean_pdf_text(quote).casefold()[:150]
        for chunk in paper.chunks or []:
            if chunk.section_name == sec.name and needle in clean_pdf_text(chunk.text).casefold():
                page = chunk.page
                break
    return XRayField(
        text=text,
        evidence_section=sec.name if sec else None,
        evidence_page=page,
        evidence_snippet=quote[:180],
    )


def build_xray(db: Session, paper: Paper) -> XRayResponse:
    sections = _sections(db, paper.id)
    abstract = _sec(sections, "abstract")
    intro = _sec(sections, "introduction")
    problem = _sec(sections, "problem", "problem context", "context", "background")
    recommendations = _sec(sections, "recommendations", "policy recommendations",
                           "proposed actions", "proposals")
    methods = _sec(sections, "methods", "methodology", "approach")
    datasets_sec = _sec(sections, "experiments", "evaluation", "results", "findings")
    results = _sec(sections, "results", "findings", "evidence", "experiments")
    discussion = _sec(sections, "discussion", "conclusion")
    limits = _sec(sections, "limitations", "risks", "trade-offs", "caveats")
    meta = paper.meta or {}
    body_sections = [sec for sec in sections if sec.name.lower() not in {
        "front matter", "references", "acknowledgements", "keywords", "preface", "about the author", "image credits"}]
    if not body_sections:
        body_sections = [sec for sec in sections if sec.name.lower() not in {"references", "acknowledgements", "keywords"}]
    body_pairs = [(sent, sec) for sec in body_sections for sent in _sentences(sec.text)]

    abstract_sents = _sentences(abstract.text) if abstract else _sentences(paper.abstract or "")
    intro_sents = _sentences(intro.text)[:40] if intro else []
    problem_sents = _sentences(problem.text)[:20] if problem else []
    recommendation_sents = _sentences(recommendations.text)[:20] if recommendations else []
    method_sents = _sentences(methods.text)[:40] if methods else []
    result_sources = [s for s in (results, datasets_sec) if s]
    result_sources = list(dict.fromkeys(s.id for s in result_sources))
    section_by_id = {s.id: s for s in sections}
    result_pairs = [(sent, section_by_id[sid]) for sid in result_sources
                    for sent in _sentences(section_by_id[sid].text)]
    # Reports and theoretical papers use meaningful domain headings rather
    # than a fixed IMRaD template. Explicit measured findings remain usable.
    if not result_pairs:
        result_pairs = [(sent, sec) for sent, sec in body_pairs
                        if re.search(r"\b(?:we|our (?:study|experiment|model|survey))\s+(?:have\s+)?(?:found|measured|observed|reported|achieved|outperformed)\b", sent, re.I)
                        and RESULT_RE.search(sent)]

    # Problem and claim fields retain the section of the selected sentence.
    q_pool = [(s, abstract) for s in abstract_sents[:20] if QUESTION_RE.search(s)]
    if not q_pool:
        q_pool = [(s, intro) for s in intro_sents if re.search(
            r"\b(open problem|open question|remains unclear|little is known|major challenge)\b", s, re.I)]
    focus_pool = [(sent, sec) for sent, sec in body_pairs if FOCUS_RE.search(sent)]
    q, q_source = (q_pool[0] if q_pool else focus_pool[0] if focus_pool else
                   ((problem_sents[0], problem) if problem_sents else
                    ((abstract_sents[0], abstract) if abstract_sents else
                     ((intro_sents[0], intro) if intro_sents else (body_pairs[0] if body_pairs else ("", None))))))

    # main claim - first "we propose/show" sentence
    claim_pool = [(s, sec) for sec, pool in ((abstract, abstract_sents),
                                             (intro, intro_sents),
                                             (recommendations, recommendation_sents))
                  for s in pool if CLAIM_RE.search(s)]
    if not claim_pool:
        claim_pool = [(sent, sec) for sent, sec in body_pairs if CLAIM_RE.search(sent)]
    if not claim_pool:
        claim_pool = focus_pool
    claim, claim_source = (claim_pool[0] if claim_pool else
                           ((abstract_sents[1], abstract) if len(abstract_sents) > 1
                            else (q, q_source)))

    # Prefer a concrete architecture/procedure sentence to generic background
    # prose that often opens a Methods section.
    method_terms = (meta.get("methods") or [])[:5]
    def method_score(sent: str) -> int:
        score = 2 * sum(bool(re.search(rf"\b{re.escape(term)}\b", sent, re.I)) for term in method_terms)
        score += 2 * bool(re.search(r"\b(uses?|employs?|consists?|composed|built|follows|architecture|layers?)\b", sent, re.I))
        score += bool(re.search(r"\b(encoder|decoder|training|model)\b", sent, re.I))
        score -= 4 * bool(re.search(r"\b(previous|existing|most competitive|related work|traditionally)\b", sent, re.I))
        return score
    ranked_methods = sorted(method_sents, key=method_score, reverse=True)
    methodology = " ".join(s for s in ranked_methods[:2] if method_score(s) >= 2)
    methodology_sec = methods
    if not methodology:
        approach_pool = [(sent, sec) for sent, sec in body_pairs
                         if APPROACH_RE.search(sent) and method_score(sent) >= 2]
        if approach_pool:
            methodology, methodology_sec = max(approach_pool, key=lambda item: method_score(item[0]))
    if not methodology:
        plan_pool = [(sent, sec) for sent, sec in body_pairs
                     if re.search(r"\b(?:we|NASA|the (?:agency|programme?|plan))\s+(?:will|plans? to)\s+(?:send|develop|conduct|implement|explore|establish|launch)\b", sent)
                     and re.search(r"roadmap|vision|plan|capabilities|explor", sec.name, re.I)]
        if plan_pool:
            methodology, methodology_sec = plan_pool[0]

    # dataset
    ds_names = (meta.get("datasets") or [])
    ds_sents: list[tuple[str, PaperSection]] = []
    for sec in (datasets_sec, methods, abstract):
        if not sec:
            continue
        for s in _sentences(sec.text):
            if DATASET_HINT_RE.search(s) or any(re.search(rf"\b{re.escape(d)}\b", s) for d in ds_names):
                ds_sents.append((s, sec))
                break
        if ds_sents:
            break
    dataset_quote, dataset_source = ds_sents[0] if ds_sents else ("", None)
    dataset_text = ("Datasets identified: " + ", ".join(ds_names[:8]) + ". " if ds_names else "") + dataset_quote

    # evaluation
    metrics = meta.get("metrics") or []
    eval_pool = [(s, sec) for s, sec in result_pairs if re.search(
        r"\b(evaluat\w*|accuracy|score|F1|BLEU|ROUGE|perplexity|benchmark|test set)\b", s, re.I)]
    eval_pool.sort(key=lambda item: (bool(re.search(r"\b(BLEU|F1|accuracy|perplexity|ROUGE)\b", item[0], re.I)),
                                     bool(re.search(r"\bWMT\b|\b\d+(?:\.\d+)?\b", item[0], re.I))), reverse=True)
    evaluation_quote = eval_pool[0][0] if eval_pool else ""
    evaluation_text = (("Evaluation metrics: " + ", ".join(metrics[:8]) + ". ") if metrics else "") + evaluation_quote

    # results
    def result_score(item: tuple[str, PaperSection]) -> int:
        sent, sec = item
        score = 2 if sec.name.lower() == "results" else 0
        score += 3 * bool(re.search(r"\b(outperform\w*|surpass\w*|achiev\w*|improv\w*|state[- ]of[- ]the[- ]art)\b", sent, re.I))
        score += 2 * bool(re.search(r"\b\d+(?:\.\d+)?\s*(?:BLEU\b|%(?!\w)|points?\b)", sent, re.I))
        score += bool(re.search(r"\b(WMT|test set|benchmark|translation task)\b", sent, re.I))
        score -= 3 * bool(re.match(r"\s*(?:\d+(?:\.\d+)?\s+)?(?:Table|Figure)\s+\d+", sent, re.I))
        score -= 2 * bool(re.search(r"\b(label smoothing|regularization|training procedure)\b", sent, re.I))
        score -= 4 * bool(re.search(r"\b(previous (?:models?|work)|RNN models?)\b", sent, re.I)
                          and not re.search(r"\b(our|Transformer|proposed)\b", sent, re.I))
        score -= 5 * bool(PROSPECTIVE_RE.search(sent) and not re.search(
            r"\b(found|observed|measured|reported|achieved|outperformed)\b", sent, re.I))
        return score
    result_candidates = sorted((item for item in result_pairs if RESULT_RE.search(item[0])),
                               key=result_score, reverse=True)
    main_results: list[tuple[str, PaperSection]] = []
    seen_results: set[str] = set()
    for sent, sec in result_candidates:
        key = re.sub(r"\W+", "", sent[:90].lower())
        if result_score((sent, sec)) < 3 or key in seen_results:
            continue
        seen_results.add(key)
        main_results.append((sent, sec))
        if len(main_results) >= 3:
            break

    # limitations
    if limits:
        limitations = [(s, limits) for s in _sentences(limits.text)[:6]]
    else:
        limitations = [(s, sec) for s, sec in body_pairs
                       if re.search(r"\b(limitations?|drawbacks?|caveats?|shortcomings?|restricted to|"
                                    r"remains? (?:unclear|unknown|uncertain)|nature is still unclear|"
                                    r"only evaluated on|does not (?:address|handle|generaliz))\b", s, re.I)][:4]

    # assumptions
    assum = [(s, sec) for s, sec in body_pairs if ASSUMPTION_RE.search(s)][:4]

    # reproducibility
    full_scan = " ".join(s.text for s in sections if s.name.lower() in {"methods", "experiments", "results"})
    repro_hits = [m.group(0) for m in REPRO_RE.finditer(full_scan)]
    repro_sents = [s for s in _sentences(full_scan[:40000]) if REPRO_RE.search(s)
                   and not re.search(r"permission to reproduce|provided proper attribution", s, re.I)][:3]
    repro_text = ("Reproducibility signals detected: " + ", ".join(sorted(set(repro_hits))[:8])
                  + ". " + " ".join(repro_sents[:2])) if repro_hits else ""

    return XRayResponse(
        research_question=_field(q, q_source, paper=paper) if q else None,
        main_claim=_field(claim, claim_source, paper=paper) if claim else None,
        methodology=_field(methodology, methodology_sec, paper=paper) if methodology else None,
        dataset=_field(dataset_text, dataset_source, evidence_snippet=dataset_quote, paper=paper) if dataset_text else None,
        evaluation=_field(evaluation_text, eval_pool[0][1] if eval_pool else None,
                          evidence_snippet=evaluation_quote, paper=paper) if evaluation_text else None,
        main_results=[_field(s, sec, paper=paper) for s, sec in main_results],
        limitations=[_field(s, sec, paper=paper) for s, sec in limitations],
        assumptions=[_field(s, sec, paper=paper) for s, sec in assum],
        reproducibility=_field(repro_text, methods, evidence_snippet=repro_sents[0] if repro_sents else None)
                        if repro_text else None,
        disclaimer=("This brief selects document passages with transparent heuristics. "
                    "Source labels identify where quoted passages appear; inferred lists may be incomplete."),
    )
