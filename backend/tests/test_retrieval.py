"""Tests: BM25 ranking, feedback-driven retrieval learning, ELI5 fallback."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "sqlite:///./data/test_retrieval.db")

from app.database.database import Base, SessionLocal, engine  # noqa: E402
from app.ingestion.pdf_extractor import _clean_line  # noqa: E402  (sanity import)
from app.rag.answerer import answer_question  # noqa: E402
from app.services.analysis import feedback as fb  # noqa: E402
from app.services.analysis.eli5 import _substitute, _shorten  # noqa: E402
from app.services.vector_store import BM25, _tokenize  # noqa: E402


def setup_module(module=None):
    Base.metadata.drop_all(bind=engine)   # reset stale schemas from older runs
    Base.metadata.create_all(bind=engine)


# -------------------------------------------------------------------- BM25 --

def test_bm25_ranks_relevant_doc_first():
    docs = [
        _tokenize("The cat sat on the mat and purred softly all day long"),
        _tokenize("Transformers use attention mechanisms for sequence transduction tasks"),
        _tokenize("Groceries and vegetables are healthy for dinner at home"),
    ]
    bm25 = BM25(docs)
    scores = bm25.scores(_tokenize("attention transformers sequence"))
    assert scores[1] > scores[0] and scores[1] > scores[2]
    assert scores[0] == 0 and scores[2] == 0


def test_bm25_empty_query():
    bm25 = BM25([_tokenize("hello world"), _tokenize("goodbye world")])
    assert list(bm25.scores([])) == [0.0, 0.0]


def test_tokenizer_stops_common_words():
    toks = _tokenize("The Attention Mechanism is used!")
    assert "the" not in toks and "is" not in toks
    assert "attention" in toks and "mechanism" in toks


# ---------------------------------------------------------------- feedback --

def test_feedback_updates_profile_and_decays():
    db = SessionLocal()
    # fresh slate (test DB persists between runs)
    from app.models.entities import RetrievalProfile
    for pid in ("test-paper-fb", "test-paper-clamp"):
        if db.get(RetrievalProfile, pid):
            db.delete(db.get(RetrievalProfile, pid))
    db.commit()

    from app.models.entities import Paper
    if db.get(Paper, "test-paper-fb") is None:
        db.add(Paper(id="test-paper-fb", title="Feedback fixture", status="ready"))
        db.commit()

    evidence = [{ "section": "methods", "text": "attention mechanisms scaled" }]

    p1 = fb.apply_feedback(db, "test-paper-fb", evidence, "how does attention work?", "up")
    assert p1.up_count == 1
    assert p1.sections.get("methods", 0) > 0
    assert p1.terms.get("attention", 0) > 0

    # second up should compound (post-decay but larger than before)
    v1 = p1.sections["methods"]
    p2 = fb.apply_feedback(db, "test-paper-fb", evidence, "attention again", "up")
    assert p2.sections["methods"] > v1 * fb.DECAY  # grew overall

    # a down-vote should pull it back down
    before_down = dict(p2.sections)
    p3 = fb.apply_feedback(db, "test-paper-fb", evidence, "attention", "down")
    assert p3.sections["methods"] < before_down["methods"]
    assert p3.down_count == 1

    summary = fb.profile_summary(p3)
    assert summary["learned"] is True
    db.close()


def test_feedback_clamps_bounds():
    db = SessionLocal()
    from app.models.entities import RetrievalProfile
    if db.get(RetrievalProfile, "test-paper-clamp"):
        db.delete(db.get(RetrievalProfile, "test-paper-clamp"))
    db.commit()
    from app.models.entities import Paper
    if db.get(Paper, "test-paper-clamp") is None:
        db.add(Paper(id="test-paper-clamp", title="Clamp fixture", status="ready"))
        db.commit()
    ev = [{ "section": "results", "text": "accuracy improved" }]
    for _ in range(30):
        p = fb.apply_feedback(db, "test-paper-clamp", ev, "accuracy", "up")
    assert p.sections["results"] <= fb.WEIGHT_MAX
    db.close()


# ------------------------------------------------------------------- ELI5 --

def test_eli5_substitution_simplifies_jargon():
    out = _substitute("The transformer model uses an attention mechanism to achieve accuracy.")
    assert "transformer" not in out.lower() or "smart AI reader" in out
    assert "attention mechanism" not in out.lower()
    assert "how often it gets things right" in out


def test_eli5_shorten_limits_sentences():
    long = ("First sentence is here. Second sentence follows right after it. "
            "Third sentence is also present. Fourth one trails on.")
    out = _shorten(long, 2)
    assert out.count(".") <= 2


# ---------------------------------------------------- end-to-end retrieval --

def test_answer_question_uses_hybrid_retrieval():
    """A dedicated live owner's passages ground an answer independently of order."""
    import uuid
    from app.models.entities import Chunk, Paper, PaperSection
    from app.security.owner import new_token, owner_hash
    from app.security.session import purge_owner, touch_session

    token = new_token()
    owner = owner_hash(token)
    paper_id = "hybrid-" + uuid.uuid4().hex
    passage = ("Quantum detectors are calibrated using reference pulses to measure timing offsets. "
               "A calibration pulse supplies a known signal for measuring the detector response.")
    touch_session(owner)
    try:
        with SessionLocal() as db:
            db.add(Paper(id=paper_id, owner=owner, title="Calibration of quantum detectors",
                         abstract=passage, status="ready"))
            db.flush()
            db.add(PaperSection(paper_id=paper_id, name="Methods", position=1,
                                page_start=4, text=passage))
            db.add(Chunk(paper_id=paper_id, section_name="Methods", page=4, text=passage))
            db.add(Chunk(paper_id=paper_id, section_name="Results", page=5,
                         text="The calibrated quantum detectors show stable timing across repeated measurements."))
            db.commit()
        ans = answer_question("What reference pulses calibrate the quantum detectors?", paper_ids=[paper_id],
                              owner=owner, token=token)
        assert ans.text
        assert not ans.insufficient
        assert ans.evidence
        assert all(evidence.paper_id == paper_id for evidence in ans.evidence)
        assert any(evidence.page == 4 and "reference pulses" in evidence.text for evidence in ans.evidence)
    finally:
        with SessionLocal() as db:
            purge_owner(db, owner, rebuild=False)
