"""Learning aids should describe the uploaded subject and available evidence."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database.database import Base
from app.models.entities import Chunk, Citation, Paper, PaperSection
from app.services.analysis.complexity import build_complexity
from app.services.analysis.gaps import build_reproduction
from app.services.analysis.insights import get_replication


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _paper(db, title, text, name="introduction", **values):
    paper = Paper(id="document", owner="test", title=title, status="ready", meta={}, **values)
    db.add(paper)
    db.add(PaperSection(paper_id=paper.id, name=name, position=0, page_start=2, text=text))
    db.commit()
    return paper


def test_history_and_quantum_have_different_supported_prerequisites(db):
    history = _paper(db, "Gudimallam temple", "The ancient temple has historical inscriptions and sculptured iconography in its sanctum.")
    history_profile = build_complexity(db, history)
    history_topics = {p["topic"] for p in history_profile.prerequisites}
    assert "Temple architecture" in history_topics
    assert "Evaluating archaeological sources" in history_topics
    assert not history_topics & {"Neural networks", "Quantum mechanics", "Transformers"}
    db.query(PaperSection).filter(PaperSection.paper_id == history.id).update({"text": (
        "Quantum theory uses wave functions, Hilbert spaces and probability distributions. "
        "The Hamiltonian derivation involves matrix eigenvalues, integrals and derivatives. "
        "Special relativity uses Lorentz transformations and reference frames.")})
    history.title = "Quantum theory and relativity"
    db.commit()
    quantum_profile = build_complexity(db, history)
    quantum_topics = {p["topic"] for p in quantum_profile.prerequisites}
    assert {"Quantum mechanics", "Linear algebra", "Special relativity"} <= quantum_topics
    assert quantum_profile.overall > history_profile.overall
    assert not quantum_topics & {"Temple architecture", "Neural networks"}


def test_unsupported_experimental_dimensions_are_omitted(db):
    paper = _paper(db, "Space exploration strategy", "The strategy describes robotic missions to the solar system and a roadmap for lunar exploration.")
    profile = build_complexity(db, paper)
    assert "Space missions and orbital motion" in {p["topic"] for p in profile.prerequisites}
    assert not {s.dimension for s in profile.scores} & {"Implementation detail", "Evaluation detail", "Data handling"}
    assert "absence does not mean" in profile.disclaimer


def test_incidental_monument_inscription_does_not_add_archaeology_background(db):
    paper = _paper(db, "The Vision for Space Exploration", (
        "An inscription on the museum monument reads: exploration inspires us. "
        "This strategy describes robotic missions to the solar system, spaceflight engineering and lunar exploration."))
    profile = build_complexity(db, paper)
    topics = {p["topic"] for p in profile.prerequisites}
    assert "Space missions and orbital motion" in topics
    assert not topics & {"Historical chronology", "Evaluating archaeological sources"}


def test_unknown_subject_does_not_get_default_machine_learning_courses(db):
    paper = _paper(db, "A brief reflection", "This essay describes the author's changing perspective during a quiet walk.")
    profile = build_complexity(db, paper)
    assert profile.prerequisites == []
    assert "No supported background" in next(s.rationale for s in profile.scores if s.dimension == "Background knowledge")


def test_speech_has_verification_checklist_and_source_passages(db):
    text = "The goal of this plan is to advance space exploration. The plan uses robotic missions to prepare for later human visits. Budget constraints remain unresolved."
    paper = _paper(db, "Exploration roadmap", text, name="exploration roadmap")
    result = build_reproduction(db, paper)
    assert "No identifiable original empirical protocol" in result.summary
    assert any(c.item == "Actions or recommendations stated" for c in result.checklist)
    assert not any("Hardware" in c.item or "Hyperparameters" in c.item for c in result.checklist)
    assert any(c.found and "p. 2, exploration roadmap:" in c.detail for c in result.checklist)


def test_discussion_of_other_experiments_is_not_own_protocol(db):
    paper = _paper(db, "Quantum interpretation", "Earlier researchers performed measurements of quantum entanglement. Their experiment is discussed in this paper as background evidence.")
    result = build_reproduction(db, paper)
    assert "No identifiable original empirical protocol" in result.summary
    assert "conceptual argument" in result.summary
    assert not any("Dataset" in c.item or "Hardware" in c.item for c in result.checklist)


def test_empirical_checklist_uses_original_source_page(db):
    statement = "We evaluated the model on a held-out dataset and measured its accuracy over five trials."
    paper = _paper(db, "Classifier evaluation", statement, name="evaluation")
    db.add(Chunk(paper_id=paper.id, section_name="evaluation", page=7, text=statement))
    db.commit()
    result = build_reproduction(db, paper)
    assert "An empirical procedure was identified" in result.summary
    outcome = next(c for c in result.checklist if c.item == "Measured outcomes or metrics mentioned")
    assert outcome.found and "p. 7, evaluation:" in outcome.detail


@pytest.mark.parametrize("state,okay,note,matched,expected", [
    ("skipped", False, "Connected research lookups are off for this session.", False, "have not been searched"),
    ("active", True, None, False, "still being refreshed"),
    ("done", False, "Provider is temporarily unavailable.", False, "could not be established"),
    ("done", True, "No verified scholarly record was matched.", False, "absence of replication studies cannot be assessed"),
    ("done", True, None, True, "indexed citing works"),
])
def test_replication_empty_note_matches_lookup_state(db, state, okay, note, matched, expected):
    paper = _paper(db, "Test study", "A short description of the study's reasoning and scope.",
                   stages=[{"key": "ecosystem", "state": state}], ecosystem_ok=okay,
                   ecosystem_note=note, openalex_id="W123" if matched else None)
    result = get_replication(db, paper)
    assert result.status == "insufficient" and not result.items
    assert expected in result.note


def test_a_neutral_replication_title_does_not_imply_success(db):
    paper = _paper(db, "Original study", "The study reports a comparison of the experimental outcomes.")
    db.add(Citation(paper_id=paper.id, title="A replication study of the original result",
                    direction="citing", is_replication=True))
    db.commit()
    result = get_replication(db, paper)
    assert result.status == "insufficient"
    assert result.items[0].category == "insufficient"
