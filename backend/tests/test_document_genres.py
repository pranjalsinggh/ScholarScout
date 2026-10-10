"""Document-genre regressions for structural parsing and honest dossiers."""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database.database import Base
from app.ingestion.metadata import extract_datasets, extract_methods, extract_metrics
from app.ingestion.pdf_extractor import ExtractedDoc, ExtractedPage, extract_pdf
from app.ingestion.sectionizer import build_sections
from app.models.entities import Paper, PaperSection
from app.services.analysis.xray import build_xray


def _page(number: int, *lines: str) -> ExtractedPage:
    return ExtractedPage(number, "\n".join(lines), [(line, 10.0, 0.0) for line in lines])


def test_unnumbered_report_headings_skip_contents_and_running_matter():
    doc = ExtractedDoc(pages=[
        _page(1, "Regional Research Strategy", "Table of Contents",
              "Executive Summary 2", "Policy Options ........ 3",
              "Recommendations ........ 4", "Risks ........ 5"),
        _page(2, "Regional Research Strategy", "Executive Summary",
              "The council recommends open access to research data so that independent teams can check public claims and reuse reliable evidence.",
              "This strategy describes the choices facing regional institutions and the trade-offs attached to each option.", "2"),
        _page(3, "Regional Research Strategy", "Policy Options",
              "A shared repository would reduce duplicated collection while preserving agency control over sensitive records and release decisions.",
              "Each participating institution would publish a data inventory before agreeing on any transfer of records.", "3"),
        _page(4, "Regional Research Strategy", "Recommendations",
              "We recommend a six-month pilot with voluntary participation, a public review board, and quarterly privacy assessments.",
              "The board should publish a plain-language decision for each proposed dataset before the pilot begins.", "4"),
        _page(5, "Regional Research Strategy", "Risks",
              "The pilot could expose sensitive records if access controls are not reviewed before publication and after each policy change.",
              "Small agencies may lack staff to review requests, so the budget must include technical support.", "5"),
    ], title="Regional Research Strategy")

    sections = build_sections(doc)
    names = [section.name for section in sections]
    assert names == ["abstract", "policy options", "recommendations", "risks"]
    assert "Table of Contents" not in " ".join(s.text for s in sections)
    assert "Regional Research Strategy" not in " ".join(s.text for s in sections)
    assert all(s.page_start >= 2 for s in sections)


def test_policy_essay_does_not_invent_methods_datasets_or_results():
    doc = ExtractedDoc(pages=[
        _page(1, "Executive Summary",
              "This essay argues that research data governance needs a public appeal process rather than opaque administrative decisions.",
              "We recommend an independent review board to improve the accuracy of public reporting about government research data."),
        _page(2, "Risks", "The proposal may increase staffing costs and delay urgent decisions.")
    ])
    sections = build_sections(doc)
    assert extract_methods(doc, sections) == []
    assert extract_datasets(doc, sections) == []
    assert extract_metrics(doc, sections) == []

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        paper = Paper(id="policy-essay", owner="test", title="Data governance essay",
                      status="ready", meta={"methods": [], "datasets": [], "metrics": []})
        db.add(paper)
        for sec in sections:
            db.add(PaperSection(paper_id=paper.id, position=sec.position,
                                name=sec.name, page_start=sec.page_start, text=sec.text))
        db.commit()
        xray = build_xray(db, paper)
        assert xray.main_claim and "appeal process" in xray.main_claim.text
        assert xray.main_claim.evidence_section == "abstract"
        assert xray.methodology is None
        assert xray.dataset is None
        assert xray.evaluation is None
        assert xray.main_results == []
        assert xray.limitations and xray.limitations[0].evidence_section == "risks"


def test_unheaded_pages_drop_repeated_header_and_page_numbers():
    doc = ExtractedDoc(pages=[
        _page(1, "Annual council memo",
              "Local agencies collect administrative records for service planning."
              " The records require a clear retention rule before release.", "1"),
        _page(2, "Annual council memo",
              "The proposed review board would assess each request using a published privacy checklist."
              " Its decisions would be open to public appeal.", "2"),
    ], title="Annual council memo")
    sections = build_sections(doc)
    assert [s.name for s in sections] == ["page 1", "page 2"]
    assert all("Annual council memo" not in s.text for s in sections)
    assert all(not s.text.endswith(("\n1", "\n2")) for s in sections)


def test_findings_with_caption_keep_result_and_source():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        paper = Paper(id="strategy-report", owner="test", title="Transport strategy",
                      status="ready", meta={"methods": [], "datasets": [], "metrics": []})
        db.add(paper)
        db.add(PaperSection(paper_id=paper.id, position=0, name="abstract", page_start=1,
                            text="The report examines bus access across three districts and identifies long waits as a barrier."))
        db.add(PaperSection(paper_id=paper.id, position=1, name="results", page_start=4,
                            text="Figure 1: Average weekday waits by district.\n"
                                 "The survey found that average waits increased by 18% between 2020 and 2024.\n"
                                 "Table 2: Planned service increases next year."))
        db.commit()
        xray = build_xray(db, paper)
        assert xray.main_results
        assert "18%" in xray.main_results[0].text
        assert "Figure 1" not in xray.main_results[0].text
        assert xray.main_results[0].evidence_section == "results"
        assert xray.main_results[0].evidence_page == 4


def test_strategy_report_preserves_action_sections_after_methods_and_findings():
    doc = ExtractedDoc(pages=[
        _page(1, "Executive Summary",
              "The transport strategy examines bus access and recommends a staged increase in frequent services."),
        _page(2, "Methods",
              "The council surveyed 800 riders at stops in three districts using a common questionnaire and recorded their wait times."),
        _page(3, "Findings",
              "The survey found that average weekday waits increased by 18% between 2020 and 2024 across the three districts."),
        _page(4, "Implementation Plan",
              "The first stage would add buses to the busiest routes while the council checks accessibility and budget impacts."),
        _page(5, "Recommendations",
              "We recommend a pilot on two routes before expanding the new schedule to every district in the region."),
        _page(6, "Risks",
              "The pilot could crowd adjacent routes if transfers are changed before the full network is reviewed."),
    ], title="Transport strategy")
    sections = build_sections(doc)
    assert [s.name for s in sections] == ["abstract", "methods", "results",
                                          "implementation plan", "recommendations", "risks"]


def test_cover_page_does_not_make_digital_pdf_look_scanned(tmp_path):
    import pymupdf

    path = tmp_path / "report.pdf"
    pdf = pymupdf.open()
    cover = pdf.new_page()
    cover.insert_text((50, 50), "Regional strategy")
    second_cover = pdf.new_page()
    second_cover.insert_text((50, 50), "Prepared for the council")
    body = pdf.new_page()
    body.insert_text((50, 50),
                     "This digital report includes a complete text layer with enough detail "
                     "to permit extraction and research analysis across its substantive pages.")
    pdf.save(path)
    pdf.close()

    doc = extract_pdf(str(path))
    assert doc.has_text_layer
    assert "complete text layer" in doc.full_text


def test_extracted_tables_remain_available_after_pdf_is_closed(tmp_path):
    import pymupdf

    path = tmp_path / "table-report.pdf"
    pdf = pymupdf.open()
    page = pdf.new_page()
    for x in (50, 170, 290):
        page.draw_line((x, 100), (x, 190))
    for y in (100, 130, 160, 190):
        page.draw_line((50, y), (290, y))
    for y, left, right in ((120, "District", "Wait"),
                           (150, "North", "24 min"),
                           (180, "South", "17 min")):
        page.insert_text((60, y), left)
        page.insert_text((180, y), right)
    pdf.save(path)
    pdf.close()

    doc = extract_pdf(str(path))
    assert doc.tables
    assert "North" in doc.tables[0]["text"]
    assert doc.tables[0]["page"] == 1
