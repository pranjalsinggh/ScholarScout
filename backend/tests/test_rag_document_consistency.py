from types import SimpleNamespace

import pytest

from app.rag import answerer
from app.rag.retriever import Retrieved


@pytest.mark.parametrize("query", [
    "What is this document about?", "Summarize this report.",
    "What is this essay about?", "Give me an overview of the speech.",
])
def test_overview_intent_is_not_limited_to_academic_papers(query):
    assert answerer.META_INTENT_RE.search(query)


def test_unrelated_retrieved_passage_is_not_presented_as_an_answer():
    passage = Retrieved(4, "temple", "Temple history", "history", 9,
                        "The chamber flooded for several hours and villagers remembered "
                        "a similar event from their childhood.", 0.9)
    result = answerer._extractive("What methodology did the authors use?", [passage])
    assert result.insufficient
    assert "closest" in result.text.lower() or "sufficient" in result.text.lower()


def test_missing_method_is_explained_instead_of_searching_anecdotes(monkeypatch):
    from app.services.analysis import xray

    monkeypatch.setattr(xray, "build_xray", lambda *_args: SimpleNamespace(methodology=None))
    paper = SimpleNamespace(id="temple", title="Temple history", meta={})
    result = answerer._paper_insight_answer("What methodology or approach did it use?", None, paper)
    assert result and result.text and result.insufficient
    assert "method" in result.text.lower()


def test_overview_prefers_uploaded_abstract_over_title_matching_footer():
    paper = SimpleNamespace(id="speech", title="Space Exploration and Human Development",
                            chunks=[
                                SimpleNamespace(id=1, section_name="front matter", page=1,
                                                text="Publisher address and motivational quotation."),
                                SimpleNamespace(id=2, section_name="abstract", page=3,
                                                text="[abstract] Space exploration can support human development "
                                                     "through communications and observations of Earth."),
                            ])
    passages = answerer._overview_passages(paper)
    assert passages and passages[0].chunk_id == 2
    assert passages[0].page == 3


def test_claim_support_does_not_treat_a_theory_argument_as_verification(monkeypatch):
    from app.services.analysis import xray
    field = SimpleNamespace(text="This essay argues that space and time follow an informational model.",
                            evidence_section="abstract", evidence_page=1, evidence_snippet="This essay argues that space and time follow an informational model.")
    monkeypatch.setattr(xray, "build_xray", lambda *_: SimpleNamespace(main_claim=field, dataset=None,
                        evaluation=None, main_results=[]))
    result = answerer._paper_insight_answer("What evidence supports its main claim?", None,
                         SimpleNamespace(id="theory", title="Space and Time", meta={}))
    assert result.insufficient
    assert "do not establish" in result.text
    assert result.evidence[0].page == 1


def test_plain_explanation_does_not_turn_a_physical_model_into_ai():
    from app.services.analysis.eli5 import _substitute
    text = "The informational model is a theoretical inference about physical time."
    assert _substitute(text) == text


def test_passage_overlap_does_not_split_a_word():
    from app.services.chunker import chunk_text
    text = "The mechanism controls the observations through a sequence of measurements. " * 20
    chunks = chunk_text(text, size=220, overlap=50)
    words = set(text.split())
    assert len(chunks) > 1
    assert all(chunk.split()[0] in words for chunk in chunks)
    long_text = "A long statement " + "explains the scientific observations without a sentence boundary " * 40
    assert max(map(len, chunk_text(long_text, size=220, overlap=50))) < 300


def test_synthesis_cache_changes_when_the_same_passage_id_has_new_content():
    from app.rag.llm_providers import _cache_key
    old = Retrieved(7, "paper", "Report", "introduction", 2, "Publisher description.", 1)
    new = Retrieved(7, "paper", "Report", "introduction", 6, "The actual document's central purpose.", 1)
    assert _cache_key("What is this document about?", [old], "owner") != _cache_key("What is this document about?", [new], "owner")
