"""Regression checks for misleading paper analysis seen in the bundled demo."""
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database.database import Base
from app.ingestion.metadata import extract_datasets, extract_methods, extract_metrics
from app.ingestion.sectionizer import DetectedSection
from app.models.entities import Paper, PaperSection
from app.rag.answerer import _paper_insight_answer
from app.services.analysis.gaps import build_gaps
from app.services.analysis.xray import build_xray


def test_metadata_prefers_evaluation_sections_and_ignores_ordinary_is():
    sections = [
        DetectedSection("abstract", 0, 1,
                        "We propose the Transformer, based on attention mechanisms. "
                        "Existing recurrent neural networks are a comparison."),
        DetectedSection("experiments", 1, 3,
                        "We trained on WMT 2014 English-German and English-French data. "
                        "The dataset is large."),
        DetectedSection("results", 2, 5,
                        "On WMT 2014 English-to-German, our model achieved 28.4 BLEU. "
                        "The model is efficient. We also evaluated Penn Treebank with F1."),
    ]
    doc = SimpleNamespace(title="The Transformer", full_text=" ".join(s.text for s in sections))
    assert extract_methods(doc, sections) == ["transformer"]
    assert "WMT 2014 English–German" in extract_datasets(doc, sections)
    metrics = extract_metrics(doc, sections)
    assert "BLEU" in metrics and "F1" in metrics
    assert "is" not in metrics


def test_xray_and_directions_use_explicit_paper_evidence():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        paper = Paper(id="quality-paper", owner="test", title="The Transformer", status="ready",
                      meta={"methods": ["transformer"],
                            "datasets": ["WMT 2014 English–German"], "metrics": ["BLEU"]})
        db.add(paper)
        for position, (name, body) in enumerate([
            ("abstract", "The dominant systems rely on recurrent networks. "
             "We propose the Transformer, an architecture based on attention."),
            ("methods", "The Transformer uses stacked self-attention layers in its encoder and decoder."),
            ("experiments", "We trained on the WMT 2014 English-German dataset of sentence pairs."),
            ("results", "On WMT 2014 English-to-German, our model outperforms prior systems "
             "and achieves a BLEU score of 28.4."),
            ("conclusion", "We plan to extend the Transformer to image and audio inputs."),
        ]):
            db.add(PaperSection(paper_id=paper.id, position=position, name=name,
                                page_start=position + 1, text=body))
        db.commit()

        xray = build_xray(db, paper)
        assert xray.main_results and "28.4" in xray.main_results[0].text
        assert xray.main_results[0].evidence_section == "results"
        directions = build_gaps(db, paper)
        assert len(directions.gaps) == 1
        assert "image and audio" in directions.gaps[0].why
        assert "unaddressed" not in directions.gaps[0].why.lower()
        answer = _paper_insight_answer("What are the main results?", db, paper)
        assert answer and "28.4" in answer.text
