"""Complexity analysis - transparent, explainable learning-aid scores (1-5)."""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.models.entities import Paper, PaperSection
from app.schemas.api import ComplexityResponse, ComplexityScore

PREREQ_MAP: list[tuple[str, list[str], str]] = [
    # method keyword → prerequisite → unlocks
    ("transformer", ["Neural networks", "Attention mechanism"], "Transformers"),
    ("attention mechanism", ["Neural networks"], "Attention"),
    ("self-attention", ["Neural networks"], "Attention"),
    ("convolutional neural network", ["Linear regression", "Neural networks"], "CNNs"),
    ("CNN", ["Neural networks"], "CNNs"),
    ("recurrent neural network", ["Neural networks"], "RNNs"),
    ("LSTM", ["Neural networks", "RNN sequence models"], "LSTM"),
    ("graph neural network", ["Neural networks", "Graph theory basics"], "GNNs"),
    ("generative adversarial network", ["Neural networks", "Probability basics"], "GANs"),
    ("diffusion model", ["Probability basics", "Deep learning"], "Diffusion models"),
    ("variational autoencoder", ["Probability basics", "Neural networks"], "VAEs"),
    ("reinforcement learning", ["Markov decision processes", "Linear algebra"], "RL"),
    ("bayesian inference", ["Probability basics", "Statistics"], "Bayesian methods"),
    ("markov chain monte carlo", ["Probability basics", "Stochastic processes"], "MCMC"),
    ("retrieval-augmented generation", ["Information retrieval basics", "Neural language models"], "RAG"),
    ("large language model", ["Neural networks", "Transformers"], "LLMs"),
    ("random forest", ["Decision trees", "Statistics basics"], "Ensemble methods"),
    ("support vector machine", ["Linear algebra", "Optimization basics"], "SVMs"),
    ("k-means", ["Linear algebra", "Statistics basics"], "Clustering"),
    ("principal component analysis", ["Linear algebra", "Statistics basics"], "PCA"),
    ("contrastive learning", ["Neural networks", "Representation learning"], "Contrastive methods"),
    ("knowledge distillation", ["Neural networks"], "Distillation"),
    ("fine-tuning", ["Neural networks"], "Transfer learning"),
    ("zero-shot", ["Language model basics"], "Zero-shot evaluation"),
    ("few-shot", ["Language model basics"], "Few-shot evaluation"),
]

MATH_TERMS = re.compile(
    r"\b(theorem|lemma|proof|corollary|proposition|derivation|matrix|eigen\w*|gradient|"
    r"optimi[sz]\w+|convex|stochastic|probability distributions?|expectation|variance|"
    r"integrals?|derivatives?|log-?likelihood|posterior|prior|differential equations?|"
    r"wave functions?|hilbert spaces?|tensors?|lagrangian|hamiltonian|commutators?)\b", re.I)

# Background suggestions only appear when the document contains their topic.
# They are reading aids, not claims that a reader needs a particular course.
DOMAIN_TOPICS: list[tuple[str, list[str], str]] = [
    (r"\bquantum\b", ["Quantum mechanics", "Probability and measurement"], "quantum theory"),
    (r"\b(?:entanglement|bell(?:'s)? inequality|wave functions?|hilbert spaces?)\b",
     ["Quantum states and measurement", "Linear algebra"], "quantum states"),
    (r"\b(?:relativity|spacetime|space[- ]time|lorentz)\b",
     ["Special relativity", "Coordinate systems and reference frames"], "relativity"),
    (r"\b(?:cosmolog\w*|black hole|general relativity)\b",
     ["Gravity and cosmology"], "cosmology"),
    (r"\b(?:space exploration|spacecraft|astronaut|lunar|solar system)\b",
     ["Solar system science", "Space missions and orbital motion"], "space exploration"),
    (r"\b(?:propulsion|rocket|launch vehicle|aerospace)\b",
     ["Basic mechanics", "Spaceflight engineering"], "spaceflight"),
    (r"\b(?:robotic mission|robotics|robotic exploration)\b",
     ["Robotic exploration"], "robotics"),
    (r"\b(?:archaeolog\w*|inscriptions?|historical chronology)\b",
     ["Historical chronology", "Evaluating archaeological sources"], "historical sources"),
    (r"\b(?:temple|sanctum|lingam|sculptur\w*|iconograph\w*)\b",
     ["Religious iconography", "Temple architecture"], "temple history"),
    (r"\b(?:policy|strategic|strategy|roadmap|budget)\b",
     ["Evaluating policy goals and constraints"], "planning"),
    (r"\b(?:genom\w*|gene expression|sequencing)\b",
     ["Molecular biology", "Genomic data analysis"], "genomics"),
    (r"\b(?:clinical trial|randomi[sz]ed|patient cohort)\b",
     ["Study design", "Statistical inference"], "clinical studies"),
]


def _topic_evidence(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, re.I)
    return match.group(0) if match else None


def _historical_evidence(text: str, title: str) -> str | None:
    """An incidental monument inscription does not make a history document."""
    strong = _topic_evidence(r"\b(?:archaeolog\w*|epigraph\w*|historical chronology)\b", text + " " + title)
    if strong:
        return strong
    title_context = re.search(r"\b(?:temple|inscriptions?|ancient history|historical|archaeolog\w*|epigraph\w*)\b", title, re.I)
    for match in re.finditer(r"\binscriptions?\b", text, re.I):
        context = text[max(0, match.start() - 180):match.end() + 180]
        if title_context or re.search(r"\b(?:temple|sanctum|dynast\w*|excavat\w*|ruins|epigraph\w*)\b", context, re.I):
            return match.group(0)
    return None


def build_complexity(db: Session, paper: Paper) -> ComplexityResponse:
    sections = db.query(PaperSection).filter(PaperSection.paper_id == paper.id).all()
    full = " ".join(s.text or "" for s in sections
                    if s.name.lower() not in {"references", "acknowledgements", "keywords"})[:120000]
    meta = paper.meta or {}
    methods = [m.lower() for m in (meta.get("methods") or [])]
    datasets = meta.get("datasets") or []
    eq_density = float(meta.get("equation_density") or 0)

    def clamp(v: float) -> int:
        return max(1, min(5, round(v)))

    math_mentions = MATH_TERMS.findall(full)
    math_kinds = {term.lower() for term in math_mentions}
    word_count = max(1, len(full.split()))
    math_density = len(math_mentions) / word_count * 1000
    math_score = clamp(1 + len(math_kinds) * .3 + math_density / 12 + eq_density / 6)
    math_why = (f"{len(math_kinds)} distinct mathematical terms ({len(math_mentions)} mentions) "
                f"and ~{eq_density:.0f} equation symbols per 1000 characters detected. "
                "This estimates mathematical language, not the correctness of an argument.")

    needed: dict[str, dict] = {}
    detected_topics: list[str] = []
    for kw, prereqs, unlocks in PREREQ_MAP:
        evidence = _topic_evidence(rf"\b{re.escape(kw)}\b", full)
        if kw in methods or evidence:
            detected_topics.append(unlocks)
            for p in prereqs:
                needed.setdefault(p, {"topic": p, "needed": True, "evidence": evidence or kw})
    for pattern, prereqs, topic in DOMAIN_TOPICS:
        evidence = (_historical_evidence(full, paper.title or "") if topic == "historical sources"
                    else _topic_evidence(pattern, full + " " + (paper.title or "")))
        if evidence:
            detected_topics.append(topic)
            for p in prereqs:
                needed.setdefault(p, {"topic": p, "needed": True, "evidence": evidence})
    prerequisites = list(needed.values())[:8]
    prereq_score = clamp(1 + len(needed) / 2.5)
    prereq_why = (f"{len(needed)} suggested background topics inferred from: "
                  + ", ".join(dict.fromkeys(detected_topics)) + ".") if needed else (
        "No supported background topics were identified; the extraction may be incomplete.")
    concept_score = clamp(1 + len(set(detected_topics)) / 2 + len(math_kinds) / 8)
    concept_why = (f"{len(set(detected_topics))} recognized topic families and "
                   f"{len(math_kinds)} mathematical concepts in the document.")
    scores = [
        ComplexityScore(dimension="Mathematical language", score=math_score, rationale=math_why),
        ComplexityScore(dimension="Topic breadth", score=concept_score, rationale=concept_why),
        ComplexityScore(dimension="Background knowledge", score=prereq_score, rationale=prereq_why),
    ]

    implementation = re.findall(r"\b(?:implementation|source code|software dependencies|training loop|"
                                 r"hyperparameter|preprocess\w*|gpu|api|pytorch|tensorflow)\b", full, re.I)
    if implementation:
        scores.append(ComplexityScore(
            dimension="Implementation detail", score=clamp(1 + len(set(t.lower() for t in implementation)) / 2),
            rationale=f"{len(set(t.lower() for t in implementation))} distinct software/procedure terms detected; mentions do not establish that code is available."))
    experiment_terms = re.findall(r"\b(?:ablation|cross-?validation|evaluation protocol|"
                                   r"statistical significance|error bars?|confidence intervals?)\b", full, re.I)
    if experiment_terms:
        scores.append(ComplexityScore(
            dimension="Evaluation detail", score=clamp(1 + len(set(t.lower() for t in experiment_terms)) / 1.5),
            rationale=f"{len(set(t.lower() for t in experiment_terms))} distinct evaluation-protocol terms detected; they may describe cited work."))
    if datasets:
        scores.append(ComplexityScore(
            dimension="Data handling", score=clamp(1 + len(datasets) / 2),
            rationale=f"{len(datasets)} named datasets detected. Dataset size and access requirements must be checked in the document."))
    overall = clamp(sum(s.score for s in scores) / len(scores))

    return ComplexityResponse(
        scores=scores,
        overall=overall,
        prerequisites=prerequisites,
        disclaimer=("Heuristic reading aids computed from detected terminology, not scientific quality "
                    "or difficulty measurements. Background topics are suggestions. Implementation, "
                    "evaluation and data dimensions appear only when matching details are detected; "
                    "their absence does not mean those tasks are easy."),
    )
