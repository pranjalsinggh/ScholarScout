"""Regressions for real report, history and theory document layouts."""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database.database import Base
from app.ingestion.metadata import extract_arxiv_id, extract_authors, extract_keywords
from app.ingestion.pdf_extractor import ExtractedDoc, ExtractedPage, _guess_title, extract_pdf
from app.ingestion.sectionizer import _heading_match, build_sections
from app.models.entities import Paper, PaperSection
from app.services.analysis.complexity import build_complexity
from app.services.analysis.gaps import build_gaps, build_reproduction
from app.services.analysis.text_units import sentences
from app.services.analysis.xray import build_xray


def _page(number, *lines):
    return ExtractedPage(number, "\n".join(t for t, _, _ in lines), list(lines))


def test_short_cover_lines_remain_in_title():
    page = _page(1, ("National Aeronautics and Space Administration", 16, 0),
                 ("The Vision", 40, 1), ("for Space", 40, 1), ("Exploration", 40, 1),
                 ("February 2004", 24, 1))
    assert _guess_title([page]) == "The Vision for Space Exploration"


def test_duplicate_compressed_cover_title_is_not_appended():
    page = _page(1, ("Research and Discussion of Quantum Theory", 18, 0),
                 ("By Zhi-Xun Huang", 13.9, 0),
                 ("ResearchandDiscussionofQuantumTheory", 17, 0))
    assert _guess_title([page]) == "Research and Discussion of Quantum Theory"


def test_inline_abstract_keywords_and_split_initial_heading_preserve_content():
    doc = ExtractedDoc(pages=[_page(1,
        ("Research and Discussion of Quantum Theory", 18, 0),
        ("Abstract- Quantum information technology includes communication and computing. The paper reviews theoretical disagreements.", 12, 0),
        ("Keywords: quantum mechanics, quantum entanglement states, Bell inequality, relativity.", 12, 0),
        ("I ntroduction", 12, 0),
        ("The historical development of the theory includes several experiments and competing interpretations of their meaning.", 10, 0))],
        title="Research and Discussion of Quantum Theory")
    sections = build_sections(doc)
    assert [s.name for s in sections] == ["front matter", "abstract", "keywords", "introduction"]
    assert sections[1].text.startswith("Quantum information")
    assert "Bell inequality" in extract_keywords(doc, sections)


def test_body_fragments_and_numeric_prose_are_not_headings():
    assert _heading_match(("The", 10, 0), 10, "wave function and uncertainty principle are discussed in this historical analysis.") is None
    assert _heading_match(("10 independent experiments not only violate the", 10, 0), 10,
                          "inequality, but also violate it in exactly the same way predicted by the theory.") is None
    assert _heading_match(("9 billion", 6, 1), 11, "The human population continues to grow across the world.") is None
    assert _heading_match(("experimental results (10 4-10 7)c. That is, Salart et al.", 11.9, 0), 10.1) is None
    assert _heading_match(("3. Discussions and Conclusion", 11, 1), 11) == "discussion"


def test_author_initials_honorifics_markers_and_publisher_headers():
    report = ExtractedDoc(pages=[_page(1, ("Dr. A.P.J. Abdul Kalam", 14, 0),
                          ("The Future of Space Exploration and Human Development", 22, 0))],
                          title="The Future of Space Exploration and Human Development")
    assert [a["name"] for a in extract_authors(report, [])] == ["A.P.J. Abdul Kalam"]
    theory = ExtractedDoc(pages=[_page(1, ("Space and Time", 28, 0),
        ("Sergey V. Shevchenko 1*and Vladimir V. Tokarevsky 2", 12, 1), ("Abstract", 12, 1))], title="Space and Time")
    assert [a["name"] for a in extract_authors(theory, [])] == ["Sergey V. Shevchenko", "Vladimir V. Tokarevsky"]
    journal = ExtractedDoc(pages=[_page(1, ("Physics and Space Science", 12, 0),
        ("By Zhi-Xun Huang", 14, 0), ("Abstract- A discussion of theory and experimental interpretation.", 12, 0))])
    assert [a["name"] for a in extract_authors(journal, [])] == ["Zhi-Xun Huang"]


def test_unheaded_history_report_gets_document_topics():
    text = "The Gudimallam temple preserves a distinctive sculpted lingam. The temple architecture and historical inscriptions describe its changing sanctum."
    doc = ExtractedDoc(pages=[_page(1, (text, 10, 0))], title="Gudimallam temple architecture")
    keywords = extract_keywords(doc, build_sections(doc))
    assert keywords and "temple" in keywords
    assert not any(word in keywords for word in ("transformer", "neural"))


def test_layout_fragments_join_without_mixing_columns(tmp_path):
    import pymupdf

    path = tmp_path / "columns.pdf"
    pdf = pymupdf.open()
    page = pdf.new_page(width=600, height=800)
    for row in range(18):
        y = 70 + row * 18
        page.insert_text((40, y), f"Left theory line {row}", fontsize=10)
        page.insert_text((320, y), f"Right evidence line {row}", fontsize=10)
    page.insert_text((40, 420), "Quantum", fontsize=10)
    page.insert_text((88, 420), "information", fontsize=10)
    page.insert_text((144, 420), "technology", fontsize=10)
    pdf.save(path)
    pdf.close()
    doc = extract_pdf(str(path), source_name="1110.0003v3.pdf")
    assert "Quantum information technology" in doc.pages[0].text
    assert doc.pages[0].text.index("Left theory line 17") < doc.pages[0].text.index("Right evidence line 0")
    assert extract_arxiv_id(doc) == "1110.0003"


def test_long_source_statements_and_initials_are_not_lost():
    statement = "The exploration roadmap " + "describes missions and their scientific objectives; " * 14 + "it remains a planned programme."
    selected = sentences(statement, min_len=30, max_len=180)
    assert selected and all(len(s) <= 180 for s in selected)
    assert "planned programme" in selected[-1]
    assert all(s in statement for s in selected)
    assert sentences("Dr. A.P.J. Abdul Kalam discusses the future of space exploration and human development.") == ["Dr. A.P.J. Abdul Kalam discusses the future of space exploration and human development."]


def test_offset_report_columns_are_read_down_each_column(tmp_path):
    import pymupdf
    path = tmp_path / "offset-columns.pdf"
    pdf = pymupdf.open()
    page = pdf.new_page(width=612, height=792)
    for row in range(18):
        y = 90 + row * 18
        page.insert_text((74, y), f"Left exploration statement {row} and its plans", fontsize=10)
        page.insert_text((296, y), f"Right research statement {row} with its goals", fontsize=10)
    pdf.save(path)
    pdf.close()
    doc = extract_pdf(str(path))
    assert doc.pages[0].text.index("Left exploration statement 17") < doc.pages[0].text.index("Right research statement 0")


def test_overview_and_xray_skip_publisher_text_and_quoted_motivation():
    from app.models.entities import Chunk
    from app.rag.answerer import _abstract_answer
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        paper = Paper(id="speech", owner="test", title="Space exploration and human development", status="ready", meta={})
        db.add(paper)
        db.add(Chunk(paper_id=paper.id, section_name="introduction", page=3,
                     text="The Papers series features reports from the Center. This series includes papers on human development. Through its programs of research, publications and events, the Center seeks human progress."))
        db.add(Chunk(paper_id=paper.id, section_name="space programme", page=6,
                     text="Space exploration and technology have improved human development through communications and Earth observation."))
        db.add(PaperSection(paper_id=paper.id, position=0, name="conclusion", page_start=17,
                            text="Your mind transcends limitations, your consciousness expands in every direction, and you find yourself in a new, great, and wonderful world."))
        db.commit()
        overview = _abstract_answer("What is this document about?", [paper.id], db=db, use_llm=False)
        assert overview and "Earth observation" in overview.text and "This series" not in overview.text
        assert overview.evidence[0].page == 6
        assert not build_xray(db, paper).limitations


def test_review_does_not_label_other_teams_experiments_as_original_results():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        paper = Paper(id="review", owner="test", title="Quantum history", status="ready", meta={})
        db.add(paper)
        db.add(PaperSection(paper_id=paper.id, position=0, name="abstract", page_start=1,
             text="This paper reviews the history of quantum experiments. In 2017, another team achieved thousand-kilometer entanglement in an experiment."))
        db.commit()
        assert build_xray(db, paper).main_results == []


def test_wrapped_words_remain_searchable_without_destroying_compound_terms():
    from app.services.analysis.text_units import clean_pdf_text
    assert clean_pdf_text("inter-\nnational explo-\nration uses self-\nattention") == "international exploration uses self-attention"


def test_general_report_has_focus_and_plan_but_no_invented_experiment():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        paper = Paper(id="vision", owner="test", title="The Vision for Space Exploration", status="ready", meta={})
        db.add(paper)
        db.add(PaperSection(paper_id=paper.id, position=0, name="introduction", page_start=5,
                            text="The fundamental goal of this vision is to advance scientific knowledge through sustained exploration of the solar system."))
        db.add(PaperSection(paper_id=paper.id, position=1, name="exploration roadmap", page_start=13,
                            text="The plan uses robotic missions to establish the conditions for later human exploration. The proposed mission may face budget limitations that cannot be resolved by technology alone."))
        db.commit()
        xray = build_xray(db, paper)
        assert xray.research_question and "fundamental goal" in xray.research_question.text
        assert xray.main_claim and "fundamental goal" in xray.main_claim.text
        assert xray.methodology and xray.methodology.evidence_section == "exploration roadmap"
        assert xray.main_results == [] and xray.dataset is None and xray.evaluation is None
        assert xray.limitations and xray.limitations[0].evidence_section == "exploration roadmap"
        reproduction = build_reproduction(db, paper)
        assert "empirical" in reproduction.summary.lower() or "strategy" in reproduction.summary.lower()
        assert not any("GPU" in (check.detail or "") for check in reproduction.checklist)
        complexity = build_complexity(db, paper)
        assert not any(p["topic"] in {"Neural networks", "Transformers", "CNNs"} for p in complexity.prerequisites)


def test_explicit_future_work_in_noncanonical_section_remains_sourced():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        paper = Paper(id="history", owner="test", title="Temple archaeology", status="ready", meta={})
        db.add(paper)
        db.add(PaperSection(paper_id=paper.id, position=0, name="temple architecture", page_start=16,
                            text="Future research should compare the inscriptions with nearby archaeological sites to establish the chronology."))
        db.commit()
        gaps = build_gaps(db, paper)
        assert len(gaps.gaps) == 1
        assert gaps.gaps[0].supporting[0]["source"].startswith("temple architecture")
