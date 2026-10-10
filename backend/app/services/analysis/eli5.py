"""Dual-mode explanations: technical (as written in the paper) and ELI5
("explain it like I'm 10").

ELI5 generation:
  1. If any LLM provider is configured → ONE batched call rewriting all X-Ray
     fields at once (token-efficient; no per-field calls).
  2. Otherwise → deterministic simplifier: jargon glossary substitutions +
     sentence shortening. Always available, zero tokens.

Both modes are strictly derived from the extracted X-Ray content - nothing new
is invented.
"""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.models.entities import Paper
from app.rag.llm_providers import llm_configured
from app.services.analysis.xray import build_xray

# sentence boundary for the deterministic shortener (self-contained: xray
# never had its own splitter)
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\"'])")

# term → kid-friendly substitution (applied as whole-word, case-preserving-ish)
GLOSSARY: list[tuple[str, str]] = [
    (r"\btransformer(s)?\b", "a smart AI reader{1}"),
    (r"\battention mechanism\b", "a trick that helps AI focus on the important words"),
    (r"\bself-attention\b", "the AI looking at all words at once to see which matter"),
    (r"\battention\b", "focusing on the important parts"),
    (r"\bneural network(s)?\b", "a computer brain made of tiny connected pieces"),
    (r"\bdataset(s)?\b", "a big pile of examples"),
    (r"\bbenchmark(s)?\b", "a standard test"),
    (r"\baccuracy\b", "how often it gets things right"),
    (r"\bembedding(s)?\b", "turning words into number-codes that keep their meaning"),
    (r"\brecurrent\b", "word-by-word"),
    (r"\bencoder\b", "the reading part"),
    (r"\bdecoder\b", "the writing part"),
    (r"\bBLEU\b", "a translation score"),
    (r"\bperplexity\b", "a confusion score"),
    (r"\bhyperparameter(s)?\b", "settings chosen before training"),
    (r"\btraining\b", "practice"),
    (r"\btrain(ed|ing)?\b", "practiced"),
    (r"\b(?:neural|machine learning|language) model(s)?\b", "AI program{1}"),
    (r"\barchitecture\b", "design"),
    (r"\bstate-of-the-art\b", "the best anyone has made so far"),
    (r"\bablation\b", "a remove-one-piece experiment"),
    (r"\bmethodology\b", "the study plan"),
    (r"\blimitations?\b", "things it can't do well yet"),
    (r"\breproducibility\b", "whether others can redo it and get the same result"),
    (r"\breplication\b", "redoing a study to check it"),
    (r"\bhypothesis\b", "an educated guess"),
    (r"\b(?:model|trainable) parameter(s)?\b", "a setting adjusted during training"),
    (r"\bmodel inference\b", "using the trained AI"),
    (r"\bGPU(s)?\b", "a very powerful computer chip"),
    (r"\bcorpus\b", "a huge pile of text"),
    (r"\bsemantic\b", "meaning-based"),
    (r"\bsyntax\b", "grammar"),
]

FILLERS = re.compile(
    r"\b(furthermore|moreover|nonetheless|nevertheless|consequently|accordingly|"
    r"in addition|specifically|particularly|essentially|fundamentally)\b[, ]*", re.I)


def _substitute(text: str) -> str:
    out = text
    for pattern, repl in GLOSSARY:
        def _apply(m: re.Match, repl: str = repl) -> str:
            base = repl
            # keep plurality for groups like "model(s)"
            plural = bool(m.group(1)) if m.lastindex else False
            if plural and "{1}" in base:
                base = base.replace("{1}", "s")
            else:
                base = base.replace("{1}", "")
            return base
        out = re.sub(pattern, _apply, out, flags=re.I)
    out = FILLERS.sub("", out)
    out = re.sub(r"\s+", " ", out).strip()
    # article cleanup after substitutions ("the a smart reader", "a AI program")
    out = re.sub(r"\b(?:the|a|an)\s+(a|an|the)\s+", lambda m: m.group(1) + " ", out, flags=re.I)
    out = re.sub(r"\ba\s+(?=[aeiou])", "an ", out, flags=re.I)
    out = re.sub(r"\s+", " ", out).strip()
    return out


def _shorten(text: str, max_sentences: int = 2) -> str:
    sents = [s.strip() for s in SENT_SPLIT.split(text) if s.strip()]
    if not sents:
        return text
    # prefer the first sentence + the punchiest short one
    picked = [sents[0]]
    if max_sentences > 1:
        rest = [s for s in sents[1:] if 30 < len(s) < 180]
        if rest:
            picked.append(rest[0])
    out = " ".join(picked)
    if len(out) > 320:
        out = out[:320].rsplit(" ", 1)[0] + "…"
    return out


def _deterministic_eli5(xray) -> dict[str, str]:
    fields: dict[str, str] = {}
    mapping = {
        "research_question": "What the scientists wanted to find out",
        "main_claim": "What they say they discovered",
        "methodology": "How they studied it",
        "dataset": "What examples they used",
        "evaluation": "How they checked their work",
        "reproducibility": "Can other people redo this",
    }
    for key, label in mapping.items():
        f = getattr(xray, key, None)
        if f and f.text:
            fields[key] = _shorten(_substitute(f.text))

    results = []
    for f in (xray.main_results or [])[:4]:
        results.append(_shorten(_substitute(f.text), 1))
    if results:
        fields["main_results"] = " ".join(results)

    limits = []
    for f in (xray.limitations or [])[:3]:
        limits.append(_shorten(_substitute(f.text), 1))
    if limits:
        fields["limitations"] = " ".join(limits)

    fields["__intro__"] = (
        "This is a science paper explained simply. Scientists had a question, "
        "tried an experiment, and wrote down what they found."
    )
    return fields


_ELI5_PROMPT = (
    "You rewrite academic findings for a curious 10-year-old. Use ONLY the meaning "
    "contained in each numbered item - never add new facts, names, or numbers. For each "
    "item write 1-2 very short sentences a child understands. Compare things to everyday "
    "life when helpful. Output exactly one line per item in this format:\n"
    "N| your simple explanation\n\nItems:\n{items}"
)


def _llm_eli5(xray, owner: str = "") -> dict[str, str] | None:
    """One batched call for all fields; returns {field_key: text} or None."""
    from app.rag import llm_providers

    items: list[tuple[str, str]] = []
    simple_keys = {
        "research_question": "what they wanted to find out",
        "main_claim": "what they say they found",
        "methodology": "how they studied it",
        "dataset": "what examples they used",
        "evaluation": "how they checked it",
        "reproducibility": "whether others can redo it",
    }
    for key, desc in simple_keys.items():
        f = getattr(xray, key, None)
        if f and f.text:
            items.append((key, f"{desc}: {f.text[:400]}"))
    for i, f in enumerate((xray.main_results or [])[:3]):
        items.append((f"result_{i}", f"a finding: {f.text[:300]}"))
    for i, f in enumerate((xray.limitations or [])[:2]):
        items.append((f"limit_{i}", f"a limitation: {f.text[:300]}"))
    if not items:
        return None

    item_block = "\n".join(f"{i+1}. [{key}] {txt}" for i, (key, txt) in enumerate(items))
    query = _ELI5_PROMPT.format(items=item_block)

    # reuse provider chain; single batched call (token-efficient)
    import httpx
    from app.config import settings

    eli5_system = (
        "You rewrite academic findings for a curious 10-year-old using only the given "
        "meaning. Never invent facts. Output one line per item in the format: N| explanation."
    )

    def _parse(text: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for line in text.splitlines():
            clean = line.replace("**", "").strip()
            m = re.match(r"^(\d+)\s*[.|:)\-]\s*(.+)$", clean)
            if m:
                idx = int(m.group(1)) - 1
                if 0 <= idx < len(items):
                    out[items[idx][0]] = m.group(2).strip()
        return out

    for provider in llm_providers.build_chain(owner):
        try:
            text = ""
            if provider == "gemini":
                url = ("https://generativelanguage.googleapis.com/v1beta/models/"
                       + settings.gemini_model + ":generateContent")
                payload = {
                    "system_instruction": {"parts": [{"text": eli5_system}]},
                    "contents": [{"role": "user", "parts": [{"text": query}]}],
                    "generationConfig": {"temperature": 0.3, "maxOutputTokens": 700},
                }
                resp = httpx.post(url, headers={"x-goog-api-key": settings.gemini_api_key or ""},
                                  json=payload, timeout=llm_providers.TIMEOUT)
                resp.raise_for_status()
                candidates = resp.json().get("candidates") or []
                cparts = ((candidates[0].get("content") or {}).get("parts")) or [] if candidates else []
                text = "".join(p.get("text", "") for p in cparts)
            else:
                if provider == "groq":
                    base, api_key, model = ("https://api.groq.com/openai/v1",
                                            settings.groq_api_key, settings.groq_model)
                else:
                    base, api_key, model = (settings.openai_base_url or "https://api.openai.com/v1",
                                            settings.openai_api_key, settings.openai_model)
                payload = {
                    "model": model,
                    "temperature": 0.3,
                    "max_tokens": 1500,
                    "messages": [
                        {"role": "system", "content": eli5_system},
                        {"role": "user", "content": query},
                    ],
                }
                if provider == "groq":
                    payload["reasoning_effort"] = "low"
                resp = httpx.post(base + "/chat/completions",
                                  headers={"Authorization": "Bearer " + (api_key or "")},
                                  json=payload, timeout=llm_providers.TIMEOUT)
                resp.raise_for_status()
                text = resp.json()["choices"][0]["message"]["content"]

            text = (text or "").strip()
            if not text:
                raise ValueError("empty eli5 response")
            parsed = _parse(text)
            if len(parsed) < max(len(items) // 2, 1):
                raise ValueError("eli5 parse yield too low")
            llm_providers._record_success(provider)
            return parsed
        except Exception:
            llm_providers._record_failure(provider)
            continue
    return None



def build_eli5(db: Session, paper: Paper, owner: str = "") -> dict:
    """Returns {fields, mode, disclaimer} - mode: llm | deterministic."""
    from app.services.scholarly.common import cache_get, cache_set
    import hashlib

    key = f"eli5::v4::{owner}::" + hashlib.sha256(paper.id.encode()).hexdigest()
    cached = cache_get(key)
    if (cached and isinstance(cached, dict) and cached.get("fields")
            and llm_configured(owner)):
        return cached

    xray = build_xray(db, paper)

    fields = None
    mode = "deterministic"
    if llm_configured(owner):
        fields = _llm_eli5(xray, owner)
        if fields:
            mode = "llm"
    if fields is None:
        fields = _deterministic_eli5(xray)
        fields.pop("__intro__", None)

    payload = {
        "fields": fields,
        "mode": mode,
        "disclaimer": ("Simple explanations restate the extracted findings in easier words - "
                       "they never add facts. The Technical view shows the paper's own wording."),
    }
    if mode == "llm":  # cache only LLM rewrites; the deterministic pass is
        cache_set(key, payload, hours=24)  # instant, so it never needs caching
    return payload
