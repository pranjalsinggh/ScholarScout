"""Sentence-aware chunking with overlap."""
from __future__ import annotations

import re
from app.services.analysis.text_units import clean_pdf_text

SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(])")


def chunk_text(text: str, size: int = 900, overlap: int = 150) -> list[str]:
    text = clean_pdf_text(text)
    if len(text) <= size:
        return [text] if text else []

    sentences = []
    for sentence in SENT_SPLIT.split(text):
        while len(sentence) > size:
            cut = sentence.rfind(" ", 0, size + 1)
            if cut <= 0:
                cut = size
            sentences.append(sentence[:cut])
            sentence = sentence[cut:].lstrip()
        if sentence:
            sentences.append(sentence)
    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for s in sentences:
        if cur_len + len(s) + 1 > size and cur:
            chunks.append(" ".join(cur))
            # keep tail for overlap
            tail = " ".join(cur)
            if overlap > 0 and len(tail) > overlap:
                start = len(tail) - overlap
                if start > 0 and not tail[start - 1].isspace():
                    next_space = tail.find(" ", start)
                    start = next_space + 1 if next_space >= 0 else len(tail)
                cur = [tail[start:]] if tail[start:] else []
            else:
                cur = []
            cur_len = len(cur[0]) if cur else 0
        cur.append(s)
        cur_len += len(s) + 1
    if cur:
        chunks.append(" ".join(cur))
    return [c for c in chunks if len(c.strip()) > 40]
