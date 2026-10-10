"""External coverage must be explainable and independent of PDF formatting."""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database.database import Base
from app.models.entities import OwnerSession, Paper, PaperSection
from app.services.analysis import distinctive, insights, news, timeline
from app.services.scholarly import arxiv


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_sparse_report_topics_use_body_instead_of_cover_word():
    with _db() as db:
        paper = Paper(id="nasa", owner="test", title="Exploration", status="ready")
        db.add(paper)
        db.add(PaperSection(paper_id=paper.id, name="strategy", position=1,
                            text="Human space exploration. Robotic exploration of Mars. " * 8))
        db.flush()
        queries = distinctive.paper_topic_queries(paper, db)
        assert any("space exploration" in query for query in queries)
        assert all(query != "exploration" for query in queries)


def test_quantum_topics_do_not_search_concatenated_pdf_header():
    paper = Paper(title="Research and Discussion of Quantum Theory "
                        "ResearchandDiscussionofQuantumTheory", meta={})
    queries = distinctive.paper_topic_queries(paper)
    assert "quantum theory" in queries
    assert not any("researchanddiscussion" in query for query in queries)


def test_arxiv_keywords_are_boolean_concepts_not_one_exact_phrase(monkeypatch):
    seen = []
    monkeypatch.setattr(arxiv, "_fetch", lambda params: seen.append(params) or None)
    arxiv.search("quantum theory mechanics")
    assert seen[0]["search_query"] == "all:quantum AND all:theory AND all:mechanics"


def test_arxiv_transport_failure_is_distinct_from_empty_results(monkeypatch):
    monkeypatch.setattr(arxiv, "_fetch", lambda params: None)
    result = arxiv.search_with_status("quantum theory")
    assert result.status == "unavailable"
    assert result.items == []


def test_news_provider_outage_is_not_reported_as_no_coverage(monkeypatch):
    import httpx
    monkeypatch.setattr(news, "_throttle_gdelt", lambda: None)
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: (_ for _ in ()).throw(httpx.ConnectError("down")))
    result = news._gdelt_news(["quantum theory", "quantum mechanics"])
    assert result.status == "unavailable"
    assert "unavailable" in (result.note or "").lower()
    assert "no recent" not in (result.note or "").lower()


def test_unknown_year_does_not_become_current_publication_year():
    with _db() as db:
        paper = Paper(id="temple", owner="test", title="Gudimallam Temple", status="ready")
        db.add(OwnerSession(owner="test"))
        db.add(paper)
        db.commit()
        timeline.build_timeline(db, paper)
        result = timeline.get_timeline(db, paper)
        assert len(result.entries) == 1
        assert result.entries[0].year is None
        assert "unknown" in (result.entries[0].description or "").lower()
        assert result.entries[0].confidence == "unknown"


def test_people_keeps_pdf_authors_without_openalex_match():
    with _db() as db:
        paper = Paper(id="policy", owner="test", title="Space policy", status="ready",
                      authors=[{"name": "A. Researcher", "affiliation": "Policy Institute"}],
                      meta={"match_status": "unmatched"})
        db.add(paper)
        db.flush()
        result = insights.get_people(db, paper)
        assert [p.name for p in result.original_authors] == ["A. Researcher"]
        assert result.original_authors[0].openalex_id is None
        assert "document" in result.note.lower()


def test_library_similarity_filters_owner_before_top_k(monkeypatch):
    import numpy as np
    from app.services.analysis import similar
    from app.services import scoped_index
    with _db() as db:
        paper = Paper(id="base", owner="mine", title="Space policy", status="ready")
        db.add(OwnerSession(owner="mine"))
        db.add(paper)
        db.add(Paper(id="mine", owner="mine", title="Space strategy", status="ready"))
        for index in range(8):
            db.add(Paper(id=f"foreign{index}", owner="other", title="Space policy", status="ready"))
        db.commit()
        ids = ["base", *(f"foreign{i}" for i in range(8)), "mine"]
        matrix = np.array([[1., 0.]] * 9 + [[0.8, 0.6]], dtype="float32")
        requested = []
        monkeypatch.setattr(scoped_index, "paper_matrix", lambda owner: requested.append(owner) or (ids, matrix))
        hits = similar.similar_papers(db, paper, owner="mine")
        assert requested == ["mine"]
        assert [hit["paper_id"] for hit in hits] == ["mine"]
        assert hits[0]["similarity"] == 80.0


def test_research_cache_cannot_reappear_after_erase(monkeypatch):
    from app.models.entities import ApiCache, EndedSession
    from app.services.scholarly import common
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    owner = "a" * 32
    key = f"research::{owner}::news"
    with Session(engine) as db:
        db.add(OwnerSession(owner=owner, research_lookup_opt_in=True))
        db.commit()
    monkeypatch.setattr(common, "SessionLocal", lambda: Session(engine))
    common.cache_set(key, {"headline": "private derived query"}, hours=1)
    with Session(engine) as db:
        assert db.get(ApiCache, key) is not None
        db.delete(db.get(OwnerSession, owner))
        db.delete(db.get(ApiCache, key))
        db.add(EndedSession(owner=owner))
        db.commit()
    common.cache_set(key, {"headline": "private derived query"}, hours=1)
    with Session(engine) as db:
        assert db.get(ApiCache, key) is None


def test_news_marks_failed_citation_provider_unavailable(monkeypatch):
    from app.security import session
    from app.services.scholarly.common import SearchResult
    monkeypatch.setattr(session, "research_lookup_allowed", lambda owner: True)
    monkeypatch.setattr(news, "cache_get", lambda key: None)
    monkeypatch.setattr(news, "cache_set", lambda *args, **kwargs: None)
    monkeypatch.setattr(news, "_arxiv_recent", lambda *args: SearchResult(provider="arXiv"))
    monkeypatch.setattr(news, "_real_news", lambda *args: (
        SearchResult(provider="GDELT"), [SearchResult(provider="GDELT")]))
    with _db() as db:
        paper = Paper(id="quantum", owner="test", title="Quantum theory", status="ready",
                      ecosystem_ok=False, ecosystem_note="Services unavailable.",
                      meta={"ecosystem_status": "unavailable"})
        db.add(paper)
        db.flush()
        result = news.get_news(db, paper)
        assert result.status == "unavailable"
        assert result.sources[0].status == "unavailable"


def test_topic_graph_edges_do_not_claim_citation(monkeypatch):
    from app.models.entities import Citation
    from app.services.analysis import graph
    with _db() as db:
        paper = Paper(id="essay", owner="test", title="Quantum theory", status="ready",
                      meta={"related_source": "topic_search"})
        db.add(paper)
        db.add(Citation(paper_id=paper.id, direction="related", title="Quantum foundations",
                        snippet="Topic match in OpenAlex; not a citation relationship"))
        db.flush()
        result = graph.build_graph(db, [paper.id], owner="test")
        assert [edge.kind for edge in result.edges] == ["related_to"]
        assert any(node.meta.get("topic_match") for node in result.nodes)


def test_enrichment_budget_stops_late_provider_calls(monkeypatch):
    from app.services.scholarly import common
    monkeypatch.setattr(common, "cache_get", lambda key: None)
    monkeypatch.setattr(common.httpx, "Client", lambda **kwargs:
                        (_ for _ in ()).throw(AssertionError("late remote call")))
    with common.lookup_budget(-1), common.track_lookups() as events:
        assert common.http_get_json("https://api.openalex.org/works") is None
    assert events[0]["provider"] == "OpenAlex"
    assert events[0]["status"] == "unavailable"
    assert "budget" in events[0]["note"]


def test_space_time_queries_prefer_relativistic_keywords_over_ambiguous_body():
    paper = Paper(title="Space and Time", keywords=["space", "time", "Matter",
                  "special relativity", "informational model", "arrow of time", "time flow"],
                  abstract=("Time flow. Absolute time. Empty set. " * 40), meta={})
    queries = distinctive.paper_topic_queries(paper)
    assert queries[:2] == ["special relativity", "arrow of time"]
    assert "time flow" not in queries
    assert "empty set" not in queries


def test_speech_queries_ignore_frequent_publisher_bigrams():
    paper = Paper(title="The Future of Space Exploration and Human Development",
                  keywords=["space", "pardee", "future", "center", "exploration"],
                  abstract=("The Pardee Center publishes Pardee Papers. " * 50
                            + "Space exploration and solar system missions. " * 3), meta={})
    queries = distinctive.paper_topic_queries(paper)
    assert queries[0] == "space exploration"
    assert not any("pardee" in query or "papers" in query for query in queries)


def test_topic_filter_rejects_incidental_time_phrases_outside_relativity():
    paper = Paper(title="Space and Time", keywords=["special relativity", "arrow of time"], meta={})
    relevant = distinctive.topic_relevance_filter(paper)
    assert not relevant("Improving Function Space Flow Matching", "Time flow with kernel optimal transport")
    assert not relevant("GRFBrain: EEG Dynamic Modeling", "Time-varying functional connectivity")
    assert not relevant("willow-corpus-lens 0.7.4", "Keeps the absolute time anchor out of analysis")
    assert relevant("Relativistic spacetime and time flow", "An informational model of special relativity")


def test_arxiv_preprints_apply_subject_gate_to_incidental_query_matches(monkeypatch):
    from app.services.scholarly.common import SearchResult
    monkeypatch.setattr(arxiv, "search_with_status", lambda *args, **kwargs: SearchResult(
        provider="arXiv", status="ok", items=[
            {"title": "Function Space Flow Matching", "abstract": "time flow for neural models",
             "published": "2026-09-29", "url": "https://arxiv.org/abs/example"}]))
    paper = Paper(title="Space and Time", keywords=["special relativity"], meta={})
    result = news._arxiv_recent(paper, ["time flow"])
    assert result.items == []
    assert result.status == "no_results"


def test_partial_ecosystem_does_not_claim_empty_timeline():
    with _db() as db:
        paper = Paper(id="physics", owner="test", title="Space and Time", status="ready",
                      year=2011, ecosystem_ok=True, ecosystem_note="Citation lookup incomplete.",
                      meta={"ecosystem_status": "partial", "match_status": "unmatched"})
        db.add(OwnerSession(owner="test"))
        db.add(paper)
        db.commit()
        timeline.build_timeline(db, paper)
        result = timeline.get_timeline(db, paper)
        assert result.status == "unavailable"
        assert "incomplete" in result.note


def test_openalex_key_is_sent_only_to_openalex(monkeypatch):
    from app.services.scholarly import common
    seen = []

    class Client:
        def __init__(self, **kwargs):
            seen.append(kwargs["headers"])

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, *args, **kwargs):
            from types import SimpleNamespace
            return SimpleNamespace(status_code=200, json=lambda: {})

    monkeypatch.setattr(common.httpx, "Client", Client)
    monkeypatch.setattr(common.settings, "openalex_api_key", "test-credential")
    monkeypatch.setattr(common, "cache_get", lambda key: None)
    monkeypatch.setattr(common, "cache_set", lambda *args: None)
    common.http_get_json("https://api.openalex.org/works")
    common.http_get_json("https://api.crossref.org/works")
    assert seen[0]["Authorization"] == "Bearer test-credential"
    assert "Authorization" not in seen[1]


def test_partial_citation_source_is_not_marked_no_results(monkeypatch):
    from app.security import session
    from app.services.scholarly.common import SearchResult
    monkeypatch.setattr(session, "research_lookup_allowed", lambda owner: True)
    monkeypatch.setattr(news, "cache_get", lambda key: None)
    monkeypatch.setattr(news, "cache_set", lambda *args, **kwargs: None)
    monkeypatch.setattr(news, "_arxiv_recent", lambda *args: SearchResult(provider="arXiv"))
    monkeypatch.setattr(news, "_real_news", lambda *args: (
        SearchResult(provider="GDELT"), [SearchResult(provider="GDELT")]))
    with _db() as db:
        paper = Paper(id="partial", owner="test", title="Quantum theory", status="ready",
                      ecosystem_ok=True, meta={"ecosystem_status": "partial"})
        db.add(paper)
        db.flush()
        result = news.get_news(db, paper)
        assert result.sources[0].status == "unavailable"
        assert result.status == "unavailable"


def test_remote_topic_label_cannot_outrank_document_title():
    paper = Paper(title="The Vision for Space Exploration", keywords=[], meta={
        "topics": ["Space Exploration and Regulation", "Satellite remote sensing and imaging"]})
    assert distinctive.paper_topic_queries(paper)[0] == "space exploration"


def test_research_news_headlines_reject_promotions_and_incidental_summaries():
    paper = Paper(title="The Future of Space Exploration", keywords=["satellites"], meta={})
    relevant = distinctive.topic_relevance_filter(paper, headline=True)
    queries = ["space exploration", "satellite connectivity"]
    junk = [
        "modis-majortom added to PyPI",
        "York Space Legal Alert: York Space Systems Investors Notified of Securities Fraud Class Action",
        "5 Bengaluru doctors rescued after 3 days of snow at Uttarkashi",
        "Amazon - LEGO Creator 3 in 1 Space Shuttle Building Kit (31134) 144-pc $7.99",
        "Arboretum Update Brings The Holodeck To Solara One On Quest",
        "Satellite images show Gaza in ruins three years into the war",
    ]
    assert all(not news._news_headline_relevant(title, queries, relevant) for title in junk)
    assert news._news_headline_relevant("Space Force Developing New Satellite-Tracking Software", queries, relevant)
    assert news._news_headline_relevant("SpaceX puts Starship into orbit for the first time", queries, relevant)


def test_newsapi_all_filtered_candidates_become_no_results_and_try_fallback(monkeypatch):
    from types import SimpleNamespace
    from app.services.scholarly.common import SearchResult
    paper = Paper(title="Space Exploration", keywords=["satellites"], meta={})
    relevant = distinctive.topic_relevance_filter(paper, headline=True)
    monkeypatch.setattr(news.settings, "newsapi_key", "test-credential")
    titles = ["modis-majortom added to PyPI",
              "York Space Legal Alert: Investors Notified of Securities Fraud Class Action",
              "5 Bengaluru doctors rescued after 3 days of snow at Uttarkashi",
              "Arboretum Update Brings The Holodeck To Solara One On Quest"]
    articles = [{"title": title, "description": "A summary mentions space exploration.",
                 "url": f"https://example.org/{index}", "source": {"name": "Example"}}
                for index, title in enumerate(titles)]
    monkeypatch.setattr(news.httpx, "get", lambda *args, **kwargs: SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: {"status": "ok", "articles": articles}))
    fallbacks = []
    monkeypatch.setattr(news, "_gdelt_news", lambda *args: fallbacks.append(True) or
                        SearchResult(provider="GDELT", status="no_results"))
    result, attempts = news._real_news(["space exploration"], topic_filter=relevant)
    assert fallbacks == [True]
    assert result.items == []
    assert result.status == "no_results"
    assert attempts[0].status == "no_results"


def test_newsapi_keeps_mission_and_satellite_technology_headlines(monkeypatch):
    from types import SimpleNamespace
    paper = Paper(title="Space Exploration", keywords=[], meta={})
    relevant = distinctive.topic_relevance_filter(paper, headline=True)
    titles = ["Space Force Developing New Satellite-Tracking Software",
              "SpaceX puts Starship into orbit for the first time, deploys 26 Starlink satellites"]
    articles = [{"title": title, "description": "Space exploration and satellite connectivity.",
                 "url": f"https://example.org/{index}", "source": {"name": "Example"}}
                for index, title in enumerate(titles)]
    monkeypatch.setattr(news.settings, "newsapi_key", "test-credential")
    monkeypatch.setattr(news.httpx, "get", lambda *args, **kwargs: SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: {"status": "ok", "articles": articles}))
    result, attempts = news._real_news(["space exploration"], topic_filter=relevant)
    assert result.status == "ok"
    assert [item.headline for item in result.items] == titles
