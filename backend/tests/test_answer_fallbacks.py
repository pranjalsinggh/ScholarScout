"""A missing abstract must never produce an empty overview bubble."""
from __future__ import annotations

from types import SimpleNamespace

from app.rag import answerer
from app.rag.retriever import Retrieved


def test_overview_without_abstract_uses_document_intro(monkeypatch):
    paper = SimpleNamespace(id="policy-1", title="National Research Strategy",
                            abstract="", status="ready")

    class Query:
        def filter(self, *args):
            return self

        def all(self):
            return [paper]

    class DB:
        closed = False

        def query(self, *args):
            return Query()

        def close(self):
            self.closed = True

    db = DB()
    monkeypatch.setattr("app.services.analysis.xray.build_xray",
                        lambda *_args: SimpleNamespace(research_question=None, main_claim=None))
    monkeypatch.setattr(answerer, "retrieve", lambda *args, **kwargs: [
        Retrieved(7, "policy-1", paper.title, "Introduction", 2,
                  "This strategy sets a ten-year research plan for public universities. "
                  "Its first priority is open access to publicly funded work.", 0.9)])
    result = answerer._abstract_answer("What is this paper about?", [paper.id], db=db,
                                       use_llm=False)
    assert result and "ten-year research plan" in result.text
    assert result.evidence[0].section == "Introduction"
    assert not db.closed


def test_intent_router_closes_its_own_database_session(monkeypatch):
    class DB:
        closed = False

        def close(self):
            self.closed = True

    db = DB()
    monkeypatch.setattr(answerer, "SessionLocal", lambda: db)
    monkeypatch.setattr(answerer, "_author_answer", lambda *args: None)
    answerer._resolve_meta("A narrow question with no intent route", None)
    assert db.closed


def test_query_expansion_uses_this_document_vocabulary():
    paper = SimpleNamespace(meta={"methods": ["stakeholder consultation"],
                                  "datasets": ["public consultation responses"],
                                  "metrics": ["response rate"]})
    method_query = answerer._expand_query("How did their methodology shape the policy?", paper)
    data_query = answerer._expand_query("Which data did they analyze?", paper)
    assert "stakeholder consultation" in method_query
    assert "public consultation responses" in data_query
    assert "response rate" not in method_query
