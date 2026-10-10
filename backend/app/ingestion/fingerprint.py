"""Paper fingerprinting for duplicate detection.

A fingerprint is the strongest identifier extractable from the PDF's first
pages, tried in order:
  1. arXiv id   → "arxiv:1706.03762"
  2. DOI        → "doi:10.5555/..."
  3. Title      → "title:<sha256>" (normalized title is never stored)

The key is stable across re-uploads of the same paper, so uploads can warn on
duplicates and the demo paper can be recognized instantly.
"""
from __future__ import annotations

import hashlib
import re

from app.ingestion import metadata as meta
from app.ingestion.pdf_extractor import ExtractedDoc, PDFLimitError, extract_pdf


def _normalize_title(title: str) -> str:
    t = (title or "").lower()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()[:96]


def fingerprint_of_doc(doc: ExtractedDoc) -> str | None:
    arxiv = meta.extract_arxiv_id(doc)
    if arxiv:
        return f"arxiv:{arxiv}"
    doi = meta.extract_doi(doc)
    if doi:
        return f"doi:{doi.lower()}"
    if doc.title and len(doc.title) >= 8:
        normalized = _normalize_title(doc.title)
        if normalized:
            return "title:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return None


def fingerprint_pdf(path: str, *, owner: str = "") -> str | None:
    """Fingerprint a PDF file; never raises (worst case: no key)."""
    try:
        doc = extract_pdf(path, owner=owner) if owner else extract_pdf(path)
        return fingerprint_of_doc(doc)
    except PDFLimitError:
        # Reject resource exhaustion at admission; don't queue the same bad PDF
        # to run a second, equally expensive parse in the analysis pipeline.
        raise
    except Exception:
        return None
