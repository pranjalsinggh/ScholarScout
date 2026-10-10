"""Provider candidates must be corroborated before they own a paper's graph."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database.database import Base
from app.models.entities import Citation, OwnerSession, Paper
from app.services.scholarly import aggregator, openalex


@pytest.fixture(autouse=True)
def _isolate_new_topic_lookup(monkeypatch):
    monkeypatch.setattr(openalex, "search_topic", lambda *_args, **_kwargs: [])


def _paper(db: Session, **overrides) -> Paper:
    values = dict(id="candidate-paper", owner="test", status="ready",
                  title="Attention Is All You Need", year=2017,
                  authors=[{"name": "Ashish Vaswani", "affiliation": None}],
                  meta={"methods": ["transformer"]})
    values.update(overrides)
    if db.get(OwnerSession, values["owner"]) is None:
        db.add(OwnerSession(owner=values["owner"], research_lookup_opt_in=True))
    paper = Paper(**values)
    db.add(paper)
    # Remote boundaries release read snapshots; the live owner and uploaded
    # document must already be committed before enrichment starts.
    db.commit()
    return paper


def _work(*, title="Attention Is All You Need", author="Ashish Vaswani",
          doi="https://doi.org/10.1234/attention", year=2017,
          work_id="Wcorrect") -> dict:
    return {
        "id": f"https://openalex.org/{work_id}", "display_name": title,
        "publication_year": year, "doi": doi, "cited_by_count": 140,
        "authorships": [{"author": {"display_name": author}, "institutions": []}],
        "topics": [], "referenced_works": [],
    }


def test_wrong_openalex_author_does_not_replace_pdf_or_attach_graph(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(openalex, "find_work_by_title", lambda title: (
        _work(author="Jane Roe", year=2025, work_id="Wwrong"), "exact"))
    monkeypatch.setattr(aggregator.crossref, "find_by_title", lambda title: None)
    monkeypatch.setattr(openalex, "find_work_by_doi", lambda doi: None)
    with Session(engine) as db:
        paper = _paper(db)
        aggregator.enrich_ecosystem(db, paper)
        assert paper.title == "Attention Is All You Need"
        assert paper.authors == [{"name": "Ashish Vaswani", "affiliation": None}]
        assert paper.year == 2017
        assert paper.openalex_id is None
        assert paper.meta["methods"] == ["transformer"]
        assert paper.meta["match_confidence"] == "weak"
        assert paper.meta["match_source"] == "openalex_title"
        assert paper.meta["match_status"] == "rejected"
        assert db.query(Citation).count() == 0


def test_doi_hit_with_unrelated_title_is_rejected(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(openalex, "find_work_by_doi", lambda doi: _work(
        title="An unrelated study of agricultural tariffs", work_id="Wunrelated"))
    monkeypatch.setattr(openalex, "find_work_by_title", lambda title: None)
    monkeypatch.setattr(aggregator.crossref, "find_by_title", lambda title: None)
    with Session(engine) as db:
        paper = _paper(db, doi="10.1234/attention")
        aggregator.enrich_ecosystem(db, paper)
        assert paper.openalex_id is None
        assert paper.doi == "10.1234/attention"
        assert paper.meta["match_status"] == "rejected"
        assert db.query(Citation).count() == 0


def test_exact_corroborated_match_fills_only_missing_pdf_fields(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(openalex, "find_work_by_title", lambda title: (
        _work(title="Attention is all you need", year=2020), "exact"))
    monkeypatch.setattr(aggregator, "_insert_citations", lambda *args: None)
    monkeypatch.setattr(aggregator, "_enrich_authors", lambda *args: None)
    with Session(engine) as db:
        paper = _paper(db, venue="PDF venue")
        aggregator.enrich_ecosystem(db, paper)
        assert paper.openalex_id == "Wcorrect"
        assert paper.title == "Attention Is All You Need"
        assert paper.authors == [{"name": "Ashish Vaswani", "affiliation": None}]
        assert paper.year == 2017
        assert paper.venue == "PDF venue"
        assert paper.doi == "10.1234/attention"
        assert paper.meta["match_source"] == "openalex_title"
        assert paper.meta["match_confidence"] == "exact"
        assert paper.meta["match_status"] == "accepted"


def test_openalex_title_search_chooses_exact_over_first_weak_result(monkeypatch):
    monkeypatch.setattr(openalex, "http_get_json", lambda *_args, **_kwargs: {
        "results": [
            _work(title="Exploration and exploitation in recommender systems", work_id="Wweak"),
            _work(title="Exploration", work_id="Wexact"),
        ]
    })
    work, grade = openalex.find_work_by_title("Exploration")
    assert work["id"].endswith("Wexact")
    assert grade == "exact"


def test_unrelated_crossref_first_hit_is_not_added_as_context(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(openalex, "find_work_by_title", lambda title: None)
    monkeypatch.setattr(aggregator.crossref, "find_by_title", lambda title: {
        "title": "Attention and memory in classroom learning", "authors": ["Jane Roe"],
        "year": 2022, "venue": "Education Review", "doi": "10.1234/wrong",
        "cited_by_count": 10,
    })
    with Session(engine) as db:
        paper = _paper(db)
        aggregator.enrich_ecosystem(db, paper)
        assert paper.meta["match_source"] == "crossref_title"
        assert paper.meta["match_status"] == "rejected"
        assert db.query(Citation).count() == 0


def test_short_exact_title_needs_author_or_doi_corroboration(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(openalex, "find_work_by_title", lambda title: (
        _work(title="Exploration", author="Jane Roe", year=2022), "exact"))
    monkeypatch.setattr(aggregator.crossref, "find_by_title", lambda title: None)
    with Session(engine) as db:
        paper = _paper(db, title="Exploration", authors=[], year=2022)
        aggregator.enrich_ecosystem(db, paper)
        assert paper.openalex_id is None
        assert paper.meta["match_confidence"] == "weak"
        assert paper.meta["match_status"] == "rejected"


def test_rejected_identity_survives_subsequent_provider_outage(monkeypatch):
    from app.services.scholarly.common import record_lookup
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(openalex, "find_work_by_title", lambda title: (
        _work(author="Wrong Author"), "exact"))
    monkeypatch.setattr(aggregator.crossref, "find_by_title", lambda title: None)

    def unavailable_topic(*args, **kwargs):
        record_lookup("OpenAlex", "unavailable")
        return []

    monkeypatch.setattr(openalex, "search_topic", unavailable_topic)
    with Session(engine) as db:
        paper = _paper(db)
        aggregator.enrich_ecosystem(db, paper)
        assert paper.meta["match_status"] == "rejected"
        assert paper.meta["ecosystem_status"] == "unavailable"
        assert not paper.ecosystem_ok


def test_partial_citation_refresh_keeps_verified_rows_without_duplicates(monkeypatch):
    import time
    from app.services.scholarly.common import record_lookup
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    def partial_citing(*args, **kwargs):
        record_lookup("OpenAlex", "ok")
        record_lookup("OpenAlex", "unavailable")
        return [_work(work_id="Wknown"), _work(work_id="Wnew")]

    monkeypatch.setattr(openalex, "get_citing_works", partial_citing)
    monkeypatch.setattr(openalex, "get_related_works", lambda *args: [])
    token = aggregator._deadline.set(time.monotonic() + 25)
    try:
        with Session(engine) as db:
            paper = _paper(db, openalex_id="Wcorrect")
            db.add_all([Citation(paper_id=paper.id, direction="citing", title="Old verified work",
                                 openalex_id="Wold"),
                        Citation(paper_id=paper.id, direction="citing", title="Known work",
                                 openalex_id="Wknown")])
            db.commit()
            aggregator._insert_citations(db, paper, {"referenced_works": []}, {})
            aggregator._insert_citations(db, paper, {"referenced_works": []}, {})
            ids = [row.openalex_id for row in db.query(Citation).filter(
                Citation.direction == "citing").all()]
            assert sorted(ids) == ["Wknown", "Wnew", "Wold"]
    finally:
        aggregator._deadline.reset(token)
