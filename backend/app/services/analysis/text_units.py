"""Sentence boundaries for PDF text with missing spaces and wrapped words."""
from __future__ import annotations

import re


def clean_pdf_text(value: str) -> str:
    hyphen_prefixes = {"self", "cross", "multi", "non", "semi", "long", "short", "fine", "zero", "few"}
    text = re.sub(r"\b([A-Za-z]{3,})-\s*\n\s*([a-z]{2,})",
                  lambda m: m.group(1) + ("-" if m.group(1).lower() in hyphen_prefixes else "") + m.group(2), value or "")
    text = re.sub(r"(?<=[a-z0-9)])\.(?=[A-Z])", ". ", text)
    text = re.sub(r"(?<=[a-z]),(?=[A-Z])", ", ", text)
    return re.sub(r"\s+", " ", text).strip()


def sentences(value: str, *, min_len: int = 30, max_len: int = 450) -> list[str]:
    text = clean_pdf_text(value)
    # Initials and common attribution abbreviations are not sentence endings.
    # Restore every character after splitting so source clauses remain quotes.
    protected = re.sub(r"\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|vs|Fig|Figs|No|Eq|Eqs|St)\.",
                       lambda m: m.group(0).replace(".", "\uf000"), text)
    protected = re.sub(r"\b(?:[A-Z]\.)+(?=\s*[A-Z])",
                       lambda m: m.group(0).replace(".", "\uf000"), protected)
    protected = re.sub(r"\bet al\.", lambda m: m.group(0).replace(".", "\uf000"), protected)
    parts = [part.replace("\uf000", ".").strip()
             for part in re.split(r"(?:(?<=[.!?])|(?<=[.!?][\"”’']))\s+(?=[A-Z0-9(\[])", protected)]
    out = []
    for part in parts:
        if len(part) < min_len:
            continue
        if len(part) <= max_len:
            out.append(part)
            continue
        # Long bullets/clauses are common in reports and theory PDFs. Return
        # bounded source excerpts instead of discarding the entire statement.
        chunks = []
        remaining = part
        while len(remaining) > max_len:
            limit = remaining[:max_len + 1]
            boundaries = [m.end() for m in re.finditer(r"[;:,]\s+", limit)
                          if m.end() >= min_len]
            cut = boundaries[-1] if boundaries else limit.rfind(" ")
            if cut < min_len:
                cut = max_len
            chunks.append(remaining[:cut].strip())
            remaining = remaining[cut:].strip()
        if len(remaining) < min_len and chunks:
            previous = chunks.pop()
            combined = previous + " " + remaining
            if len(combined) <= max_len:
                chunks.append(combined)
                remaining = ""
            else:
                # Move the boundary back to keep the final source clause.
                cut = combined.rfind(" ", 0, len(combined) - min_len + 1)
                chunks.append(combined[:cut].strip())
                remaining = combined[cut:].strip()
        if remaining:
            chunks.append(remaining)
        out.extend(chunk for chunk in chunks if chunk)
    return out
