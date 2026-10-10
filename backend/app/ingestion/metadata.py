"""Metadata extraction: authors, affiliations, DOI, arXiv id, keywords, datasets,
methods, funding disclosures - all via transparent heuristics over the text."""
from __future__ import annotations

import re

from app.ingestion.pdf_extractor import ExtractedDoc
from app.ingestion.sectionizer import DetectedSection
from app.services.analysis.text_units import clean_pdf_text, sentences

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>\]]+)", re.I)
ARXIV_RE = re.compile(r"arXiv[:\s]*(\d{4}\.\d{4,5})(v\d+)?", re.I)
EMAIL_RE = re.compile(r"[\w.+-]+@([\w-]+\.)+[a-zA-Z]{2,}")
YEAR_RE = re.compile(r"\b(19[89]\d|20[0-2]\d)\b")

VENUE_HINTS = re.compile(
    r"(proceedings of|in\s+advances\s+in|journal of|ieee|acm|neurips|nips|icml|iclr|cvpr|acl|emnlp|nature|science|pnas|plos|transactions on)",
    re.I,
)

KNOWN_DATASETS = [
    "ImageNet", "CIFAR-10", "CIFAR-100", "MNIST", "Fashion-MNIST", "ImageNet-1k",
    "COCO", "Common Crawl", "Wikipedia", "BookCorpus", "C4", "The Pile", "LAION",
    "SQuAD", "GLUE", "SuperGLUE", "MMLU", "BIG-bench", "BigBench", "TriviaQA",
    "Natural Questions", "WikiText", "Penn Treebank", "PTB", "OpenWebText",
    "ACE", "CoNLL", "OntoNotes", "MS MARCO", "BEIR", "FEVER", "HotpotQA",
    "PubChem", "ChEMBL", "PDB", "UniProt", "MIMIC", "UK Biobank", "ADNI",
    "Kinetics", "Something-Something", "AVSpeech", "LibriSpeech", "AudioSet",
    "CelebA", "FFHQ", "LSUN", "STL-10", "SVHN", "Places365", "ADE20K",
    "VQA", "Visual Genome", "GQA", "NuScenes", "KITTI", "Waymo", "BDD100K",
]

KNOWN_METHODS = [
    "transformer", "attention mechanism", "self-attention", "multi-head attention", "convolutional neural network",
    "CNN", "recurrent neural network", "LSTM", "GRU", "residual network", "ResNet",
    "graph neural network", "GNN", "generative adversarial network", "GAN", "diffusion model",
    "variational autoencoder", "VAE", "BERT", "GPT", "large language model", "LLM",
    "reinforcement learning", "Q-learning", "policy gradient", "PPO", "DQN", "actor-critic",
    "random forest", "support vector machine", "SVM", "logistic regression", "linear regression",
    "k-means", "principal component analysis", "PCA", "t-SNE", "UMAP", "DBSCAN",
    "gradient boosting", "XGBoost", "LightGBM", "knowledge distillation", "fine-tuning",
    "transfer learning", "zero-shot", "few-shot", "prompt tuning", "LoRA", "RLHF",
    "retrieval-augmented generation", "RAG", "beam search", "dropout", "batch normalization",
    "data augmentation", "cross-validation", "ablation study", "ensembling", "meta-learning",
    "contrastive learning", "self-supervised learning", "curriculum learning",
    "neural architecture search", "mixture of experts", "quantization", "pruning",
    "Bayesian inference", "Markov chain Monte Carlo", "MCMC", "Monte Carlo",
    "finite element", "finite difference", "spectral method", "regression analysis",
]

FUNDING_RE = re.compile(
    r"(?:supported|funded|sponsored|acknowledg\w*|financed)?\s*(?:by|from|grant)?\s*"
    r"((?:the\s+)?(?:[A-Z][\w&.\- ]{2,60}?(?:Foundation|Institute|Institutes|Agency|Agencies|Council|"
    r"Ministry|Commission|Association|Society|Societies|Research Council|ERC|NIH|NSF|DARPA|EPSRC|"
    r"Wellcome Trust|European Union|EU Horizon[^\s,;]*|Google|Microsoft|Meta|Amazon|OpenAI|Anthropic|"
    r"Apple|NVIDIA|Intel|IBM|Adobe|Samsung|DeepMind))[^.]{0,120}?[.,;]\s*(?:grant|grants|award|contract|"
    r"agreement|project)?\s*(?:no\.?|number|#|:)?\s*([A-Z0-9\-/.]{3,30})?)",
    re.I,
)

METRIC_RE = re.compile(
    r"\b(accuracy|precision|recall|F1(?:[- ]score)?|AUC|ROC|BLEU|ROUGE|METEOR|CIDEr|perplexity|"
    r"top-[135] accuracy|mAP|IoU|Dice|PSNR|SSIM|FID|WER|CER|exact match|AUPRC|AUROC|"
    r"p-value|R\^?2|correlation|error rate|loss|latency|throughput|speedup)\b", re.I)


def get_section(sections: list[DetectedSection], *names: str) -> DetectedSection | None:
    lname = {n.lower() for n in names}
    for s in sections:
        if s.name.lower() in lname:
            return s
    return None


def extract_authors(doc: ExtractedDoc, sections: list[DetectedSection]) -> list[dict]:
    """Heuristic author list from the front matter (before Introduction/Abstract body)."""
    if not doc.pages:
        return []
    lines = doc.pages[0].lines[:35]
    title_lower = doc.title.lower() if doc.title else ""
    # footnote markers and superscripts that break name matching
    marker = re.compile(r"(?:\d+[∗*†‡§∥¶]*|[∗*†‡§∥¶]+)(?=\s*(?:and\b|&|,|;|$))|^\d+\s*")

    authors: list[dict] = []
    emails = EMAIL_RE.findall(doc.pages[0].text)
    for text, size, bold in lines:
        t = text.strip()
        if re.match(r"^(?:abstract|key\s*words?|index\s*terms?|\d*\.?\s*introduction)\b", t, re.I):
            break
        if not t or t.lower() in title_lower:
            continue
        if re.search(r"(university|institute|department|laboratory|lab\b|college|school of|center|centre|"
                     r"corporation|research|gmbh|inc\.|ltd|\bUSA\b|\bUK\b|professor|faculty|"
                     r"google|microsoft|meta\b|facebook|amazon|apple|nvidia|ibm|deepmind|openai|"
                     r"anthropic|baidu|tencent|alibaba|samsung|intel|brain\b|ai\b|labs?\b)", t, re.I):
            continue
        # candidate author line: names separated by commas/and, each 2-4 capitalized words
        cleaned = re.sub(marker, " ", t).strip()
        cleaned = re.sub(r"^[Bb]y\s+", "", cleaned).strip()
        cleaned = re.sub(r"^(?:Dr\.?|Prof\.?|Professor)\s+", "", cleaned, flags=re.I)
        parts = re.split(r",|;| and | & ", cleaned)
        names = []
        for p in parts:
            p = p.strip().strip(".")
            if re.fullmatch(r"(?:[A-Z](?:[\w'\-]+|(?:\.[A-Z])*\.)?\s*){2,6}", p):
                if not is_junk_author_name(p) and not re.search(r"\b(science|physics|journal|frontier|publisher|issn|volume|issue|version|copyright)\b", p, re.I):
                    names.append(p)
        if names and len(cleaned) < 200 and not re.match(r"^(abstract|introduction|keywords)", cleaned, re.I):
            for n in names:
                if not any(a["name"].lower() == n.lower() for a in authors):
                    authors.append({"name": n, "affiliation": None})

    # attach affiliation hints (first email domain or nearby institution line)
    if emails and authors:
        domain = emails[0].rsplit(".", 1)[0].split(".")[-1]
        for a in authors[:1]:
            if not a["affiliation"]:
                a["affiliation"] = f"({domain}.…)" if domain else None

    return authors[:15]


def extract_doi(doc: ExtractedDoc) -> str | None:
    m = DOI_RE.search(doc.pages[0].text[:4000]) if doc.pages else None
    if not m and doc.pages:
        m = DOI_RE.search(doc.pages[0].text)
    return m.group(1).rstrip(".,;)") if m else None


def extract_arxiv_id(doc: ExtractedDoc) -> str | None:
    for p in doc.pages[:1]:
        m = ARXIV_RE.search(p.text)
        if m:
            return m.group(1)
    # The original upload name can carry the identifier when a downloaded
    # arXiv PDF omits the sidebar banner. Never take an ID from its bibliography.
    filename = getattr(doc, "source_name", "")
    match = re.fullmatch(r"(?:arxiv[-_])?(\d{4}\.\d{4,5})(?:v\d+)?\.pdf", filename, re.I)
    if match:
        return match.group(1)
    return None


def extract_year(doc: ExtractedDoc, sections: list[DetectedSection]) -> int | None:
    text = doc.pages[0].text[:3000] if doc.pages else ""
    years = [int(y) for y in YEAR_RE.findall(text)]
    years = [y for y in years if 1990 <= y <= 2035]
    if not years:
        all_years = [int(y) for y in YEAR_RE.findall(doc.full_text[:6000])]
        years = [y for y in all_years if 1990 <= y <= 2035]
    if not years:
        return None
    from collections import Counter
    return Counter(years).most_common(1)[0][0]


def extract_keywords(doc: ExtractedDoc, sections: list[DetectedSection]) -> list[str]:
    sec = get_section(sections, "keywords")
    body = sec.text if sec and sec.text else ""
    if not body:
        for page in getattr(doc, "pages", [])[:2]:
            match = re.search(r"(?:^|\n)\s*(?:key\s*words?|index\s*terms?)\s*[:\-–—]\s*([^\n]+)", page.text, re.I)
            if match:
                body = match.group(1)
                break
    if body:
        body = re.sub(r"^\s*(?:key\s*words?|index\s*terms?)\s*[:\-–—]?\s*", "", body, flags=re.I)
        body = re.split(r"\n\s*(?:PACS|PACC|classification|author|copyright)\b|\.\s*(?:\n|$)", body, maxsplit=1, flags=re.I)[0]
        parts = re.split(r"[;,·•]|\band\b", body)
        kws = [p.strip(" .") for p in parts if 2 < len(p.strip()) < 60]
        return [k for k in kws if k and not k.lower().startswith("keyword")][:12]
    # fallback: most frequent known methods/datasets in abstract
    abstract = (get_section(sections, "abstract").text if get_section(sections, "abstract") else doc.full_text[:1500])
    found = [m for m in KNOWN_METHODS if re.search(rf"\b{re.escape(m)}\b", abstract, re.I)]
    if found:
        return found[:8]
    # A history document or physics essay is still about identifiable topics.
    # These are transparent inferred terms, not a claim of author keywords.
    from app.services.analysis.distinctive import distinctive_terms
    publisher_prose = re.compile(r"papers series|papers in this series|pardee (?:papers|center)|"
                                 r"this series|programs of research, publications|series editor|"
                                 r"views expressed|publisher|copyright|trustees of", re.I)
    body_sections = " ".join(sentence for s in sections
                             if s.name not in {"front matter", "references", "keywords", "preface", "about the author", "image credits"}
                             for sentence in sentences(s.text, max_len=700)
                             if not publisher_prose.search(sentence))[:9000]
    abstract = " ".join(sentence for sentence in sentences(abstract, max_len=700)
                        if not publisher_prose.search(sentence))
    return distinctive_terms(doc.title, doc.title, doc.title, abstract, body_sections, n=8, min_len=4)


def extract_datasets(doc: ExtractedDoc, sections: list[DetectedSection]) -> list[str]:
    evaluation = " ".join(s.text for s in sections if s.name.lower() in {"experiments", "results", "evaluation"})
    text = clean_pdf_text(evaluation or doc.full_text[:30000])
    hits = []
    for m in re.finditer(
        r"\bWMT\s*(20\d{2})(?:\s+English\s*[-–]?\s*(?:to\s*[-–]?\s*)?"
        r"(German|French|Spanish|Chinese|Russian))?\b", text, re.I
    ):
        hits.append(f"WMT {m.group(1)} English–{m.group(2).title()}" if m.group(2)
                    else f"WMT {m.group(1)}")
    for ds in KNOWN_DATASETS:
        if re.search(rf"(?<![a-zA-Z]){re.escape(ds)}(?![a-zA-Z0-9])", text, re.I):
            hits.append(ds)
    # generic "the XYZ dataset" patterns
    for m in re.finditer(r"(?:the|on|using|evaluated on)\s+([A-Z][\w\-]*[A-Za-z0-9](?:\s+[A-Z][\w\-]*){0,3})\s+(?:dataset|corpus|benchmark)", text):
        name = m.group(1).strip()
        if name.lower() not in {"a", "the", "this", "our"} and len(name) > 2:
            hits.append(name)
    seen, out = set(), []
    for h in hits:
        k = h.lower()
        if k not in seen:
            seen.add(k)
            out.append(h)
    return out[:12]


def extract_methods(doc: ExtractedDoc, sections: list[DetectedSection]) -> list[str]:
    abstract = get_section(sections, "abstract")
    abstract_text = abstract.text if abstract else doc.full_text[:1800]
    contribution = " ".join(s for s in sentences(abstract_text) if re.search(
        r"\b(we|our|this paper|the proposed|we introduce|we present|we use)\b", s, re.I))
    primary = f"{doc.title or ''} {contribution}"
    hits = [m for m in KNOWN_METHODS if re.search(rf"\b{re.escape(m)}\b", primary, re.I)]
    if hits:
        return hits[:8]
    # When the abstract does not state a contribution, use the early method
    # description instead of matching every baseline and training detail.
    method_section = get_section(sections, "methods", "methodology", "approach")
    fallback = method_section.text[:3500] if method_section else abstract_text
    return [m for m in KNOWN_METHODS if re.search(rf"\b{re.escape(m)}\b", fallback, re.I)][:6]


def _funding_sentences(text: str) -> list[str]:
    """Sentences that actually talk about funding - the funding verb is
    REQUIRED, so random abstract sentences never qualify."""
    kept = []
    for sent in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", text or "")):
        s = sent.strip()
        if not (25 < len(s) < 400):
            continue
        if re.search(r"\b(?:support(?:ed|s)?|fund(?:ed|ing|s)?|financ(?:ed|ing|es)?|"
                     r"sponsor(?:ed|s|ship)?)\s+(?:in\s+part\s+)?(?:by|under|from)\b", s, re.I)            or re.search(r"\bgrants?\s+(?:no\.?|number|#|from)\b", s, re.I)            or re.search(r"\backnowledg\w*\s+(?:the\s+)?(?:financial\s+)?support\b", s, re.I):
            kept.append(s)
    return kept


FUNDER_ENTITY_RE = re.compile(
    r"((?:[A-Z][\w&]*[\s&]+){0,3}(?:Foundation|Institutes?|Agency|Agencies|Council|Counsel|"
    r"Ministry|Commission|Association|Society|Societies|Trust|Programme|Program|"
    r"Directorate|Bureau|Administration|Research\s+Council|Science\s+Foundation)"
    r"(?:\s+(?:of|for)\s+[A-Z][\w& ]{2,40})?)"
    r"|\b(?:NIH|NSF|DARPA|EPSRC|DFG|CNRS|ANR|DOE|ONR|AFOSR|ERC|Wellcome\s+Trust|"
    r"NASA|ESA|ISRO|European\s+Research\s+Council|European\s+Union)\b"
    r"|\b(?:Google|Microsoft|Meta|Amazon|OpenAI|Anthropic|NVIDIA|Intel|IBM|Samsung|Adobe)\b"
    r"|(?:grant|grants|award|contract)\s*(?:no\.?|number|#|:)?\s*([A-Z0-9][A-Z0-9\-/]{2,24})",
    re.I)


def _extract_funders(sentence: str) -> list[tuple[str, str | None]]:
    """(funder, grant) pairs from one funding sentence."""
    found: list[tuple[str, str | None]] = []
    seen = set()
    for m in FUNDER_ENTITY_RE.finditer(sentence):
        if m.group(1):
            name = re.sub(r"\s+", " ", m.group(1)).strip(" ,;&")
            name = re.sub(r"^(?:by|from|under|and)\s+", "", name, flags=re.I) or name
            if len(name) < 4:
                continue
            k = name.lower()
            if k not in seen:
                seen.add(k)
                grant_m = re.search(
                    r"\b(?:grant|grants|award|contract)\s*(?:no\.?|number|#|:)?\s*"
                    f"([A-Z0-9][A-Z0-9{chr(92)}/-]{{2,24}})", sentence[m.end():], re.I)
                found.append((name, grant_m.group(1) if grant_m else None))
        elif m.group(2) and not found:
            found.append((m.group(0).upper(), None))
    return found


def extract_funding(doc: ExtractedDoc, sections: list[DetectedSection]) -> list[dict]:
    """Funding statements: scan the acknowledgements/funding section first, then
    any sentence elsewhere with explicit funding language. Every result traces
    to a real sentence - no loose matches."""
    sec = get_section(sections, "acknowledgements", "funding", "limitations")
    pool = ""
    if sec and sec.text:
        pool = sec.text
    sentences = _funding_sentences(pool)
    if not sentences:  # nothing in the likely sections → whole doc, still verb-gated
        sentences = _funding_sentences(doc.full_text[:30000])

    out, seen = [], set()
    for sent in sentences:
        for funder, grant in _extract_funders(sent):
            k = funder.lower()
            if k in seen:
                continue
            seen.add(k)
            out.append({"funder": funder[:120], "grant": grant,
                        "quote": sent[:280]})
            if len(out) >= 8:
                return out
    return out


COI_RE = re.compile(
    r"[^.]*(?:conflicts?\s+of\s+interest|competing\s+(?:financial\s+)?(?:interests?|"
    r"benefits?))[^.]*\.", re.I)


def extract_conflict_statements(doc: ExtractedDoc, sections: list[DetectedSection]) -> list[str]:
    """Verbatim conflict-of-interest declarations, if the paper makes any."""
    sec = get_section(sections, "acknowledgements", "funding", "declarations")
    pool = (sec.text if sec and sec.text else doc.full_text[:30000])
    out, seen = [], set()
    for m in COI_RE.finditer(re.sub(r"\s+", " ", pool)):
        s = m.group(0).strip()
        if not (30 < len(s) < 300):
            continue
        k = s[:60].lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
        if len(out) >= 3:
            break
    return out


def extract_metrics(doc: ExtractedDoc, sections: list[DetectedSection]) -> list[str]:
    chosen = [s for name in ("results", "experiments", "evaluation", "findings")
              for s in sections if s.name.lower() == name]
    if chosen:
        text = clean_pdf_text(" ".join(s.text for s in chosen))
    else:
        # On essays/reports, words such as "accuracy" or "precision" can be
        # ordinary prose. Only count them outside an evaluation section when a
        # sentence states a measured numeric outcome.
        text = " ".join(s for s in sentences(doc.full_text[:10000])
                        if re.search(r"\b\d+(?:\.\d+)?\s*(?:%(?!\w)|points?\b|score\b)", s, re.I)
                        and re.search(r"\b(measured|achieved|reported|evaluated|scored|found)\b", s, re.I))
    found = []
    for match in METRIC_RE.finditer(text[:25000]):
        m = match.group(0)
        f = m.upper() if m.upper() in {"F1", "AUC", "ROC", "BLEU", "ROUGE", "METEOR", "CIDER", "WER", "CER", "FID", "PSNR", "SSIM"} else m.lower()
        if f not in found:
            found.append(f)
    return found[:10]


# names that are really institutional fragments or headings, not people
JUNK_NAME_WORDS = {
    "administration", "agency", "department", "ministry", "association",
    "society", "council", "committee", "foundation", "institute",
    "university", "college", "center", "centre", "laboratory",
    "directorate", "program", "office", "national", "federal", "vision",
    "mission", "exploration", "foreword", "preface", "nasa", "esa",
}


def is_junk_author_name(name: str) -> bool:
    n = (name or "").strip()
    if not n:
        return True
    if re.match(r"^The\s+", n, re.I):
        return True
    return any(w in JUNK_NAME_WORDS for w in re.findall(r"[a-zA-Z]{3,}", n.lower()))
