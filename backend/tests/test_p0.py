"""P0 feature tests: BibTeX export, fingerprinting, arXiv year corroboration."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite:///./data/test_p0.db")

from app.services.analysis.cite import bibtex_for, ris_for  # noqa: E402
from app.ingestion.fingerprint import _normalize_title, fingerprint_of_doc  # noqa: E402
from app.services.scholarly.aggregator import _arxiv_year  # noqa: E402


class FakePaper:
    """Duck-typed Paper for citation builders."""
    title = "Attention Is All You Need"
    authors = [{"name": "Ashish Vaswani"}, {"name": "Noam Shazeer"}]
    year = 2017
    venue = "NeurIPS"
    doi = "10.5555/3295222.3295349"
    arxiv_id = "1706.03762"
    openalex_id = "W2626778328"
    filename = "attention.pdf"
    abstract = "We propose the Transformer."


def test_bibtex_contains_core_fields():
    bib = bibtex_for(FakePaper())
    assert bib.startswith("@article{vaswani2017,")
    assert "title = {Attention Is All You Need}" in bib
    assert "Ashish Vaswani and Noam Shazeer" in bib
    assert "year = 2017" in bib
    assert "doi = 10.5555/3295222.3295349" in bib
    assert "eprint = 1706.03762" in bib


def test_bibtex_handles_missing_metadata():
    class Empty(FakePaper):
        title = None
        authors = []
        year = None
        doi = None
        venue = None
        arxiv_id = None
        openalex_id = None

    bib = bibtex_for(Empty())
    assert bib.startswith("@article{unknownnd,")
    assert "Unknown" in bib


def test_ris_format():
    ris = ris_for(FakePaper())
    assert ris.startswith("TY  - JOUR")
    assert "TI  - Attention Is All You Need" in ris
    assert "AU  - Ashish Vaswani" in ris
    assert ris.rstrip().endswith("ER  -")


def test_fingerprint_prefers_arxiv():
    class Doc:
        title = "Some Title"

    doc = Doc()
    doc.title = "Attention Is All You Need"
    # monkey-ish: call with a doc exposing the attrs used
    from app.ingestion import metadata as meta
    orig_arxiv, orig_doi = meta.extract_arxiv_id, meta.extract_doi
    meta.extract_arxiv_id = lambda d: "1706.03762"
    meta.extract_doi = lambda d: None
    try:
        assert fingerprint_of_doc(doc) == "arxiv:1706.03762"
    finally:
        meta.extract_arxiv_id, meta.extract_doi = orig_arxiv, orig_doi


def test_fingerprint_falls_back_to_title():
    from app.ingestion import metadata as meta
    class Doc:
        title = "Attention Is All You Need"
    orig_arxiv, orig_doi = meta.extract_arxiv_id, meta.extract_doi
    meta.extract_arxiv_id = lambda d: None
    meta.extract_doi = lambda d: None
    try:
        key = fingerprint_of_doc(Doc())
    finally:
        meta.extract_arxiv_id, meta.extract_doi = orig_arxiv, orig_doi
    import hashlib
    expected = hashlib.sha256(_normalize_title("Attention Is All You Need").encode()).hexdigest()
    assert key == "title:" + expected


def test_arxiv_year_extraction():
    assert _arxiv_year("1706.03762") == 2017
    assert _arxiv_year("9908.12345") == 1999
    assert _arxiv_year(None) is None
    assert _arxiv_year("garbage") is None
