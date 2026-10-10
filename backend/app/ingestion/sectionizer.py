"""Section detection: map a stream of PDF lines onto canonical paper sections.

Handles "1. Introduction", "INTRODUCTION", "Introduction" styles. Falls back to
page-based chunks when no recognizable structure exists (scanned/messy PDFs).
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from app.ingestion.pdf_extractor import ExtractedDoc

# canonical name -> aliases (regex fragments, matched case-insensitively)
SECTION_MAP: list[tuple[str, list[str]]] = [
    ("preface", [r"preface", r"foreword", r"about\s+this\s+(?:series|publication)"]),
    ("abstract", [r"abstract", r"executive\s+summary", r"summary"]),
    ("keywords", [r"keywords?", r"key\s*words", r"index\s*terms"]),
    ("introduction", [r"introduction", r"background", r"motivation"]),
    ("related work", [r"related\s*work", r"prior\s*(work|art)", r"literature\s*review", r"background"]),
    ("methods", [r"method(s|ology)?", r"approach", r"(model|framework|system)\s+(design|architecture|overview)",
                 r"proposed\s+(method|approach|framework)", r"materials", r"problem\s*(formulation|setup|setting)",
                 r"preliminar(ies|y)", r"our\s+(approach|method|model|framework)", r"methodology"]),
    ("experiments", [r"experiment(s|al)?", r"evaluation", r"experimental\s+setup", r"setup",
                     r"training", r"results\s*and\s*(discussion|analysis)"]),
    ("results", [r"results?", r"findings", r"analysis"]),
    ("discussion", [r"discussions?(?:\s+and\s+conclusions?)?", r"ablation(\s*stud(y|ies))?", r"analysis\s*and\s*discussion"]),
    ("limitations", [r"limitations?", r"threats\s*to\s*validity", r"shortcomings", r"caveats"]),
    ("recommendations", [r"recommendations?", r"policy\s+recommendations"]),
    ("risks", [r"risks?", r"trade[- ]offs?"]),
    ("implementation plan", [r"implementation\s+plan", r"action\s+plan"]),
    ("conclusion", [r"conclusions?", r"concluding\s*remarks", r"summary\s*and\s*(outlook|conclusion)", r"future\s*work"]),
    ("acknowledgements", [r"acknowledg(e)?ments?", r"funding", r"acknowledgments"]),
    ("references", [r"references?", r"bibliography", r"works\s*cited", r"literature"]),
    ("appendix", [r"appendix", r"appendices", r"supplementary"]),
]

HEADING_NUM = re.compile(r"^(\d+(\.\d+)*)\.?\s+(.+)$")
HEADING_ROMAN = re.compile(r"^[IVXLCDM]{1,6}[.)]\s+(.+)$")
INLINE_SECTION = re.compile(r"^(abstract|executive\s+summary|key\s*words?|index\s*terms?)\s*[:\-–—]\s*(.*)$", re.I)
ALL_CAPS = re.compile(r"^[A-Z0-9\s:,'()&./-]+$")
CAPTION = re.compile(r"^(?:figure|fig\.?|table|exhibit)\s+\d+\b", re.I)
TOC_ENTRY = re.compile(r"^.{3,75}?(?:\.{2,}|\s{3,})\s*\d{1,3}$")
TOC_SIMPLE_ENTRY = re.compile(r"^[A-Z][A-Za-z ,&/\-]{3,75}\s+\d{1,3}$")
PAGE_NUMBER = re.compile(r"^(?:page\s+)?\d{1,4}(?:\s+of\s+\d{1,4})?$", re.I)


@dataclass
class DetectedSection:
    name: str
    position: int
    page_start: int
    text: str = ""
    lines: list[tuple] = field(default_factory=list)
    page_texts: list[tuple[int, str]] = field(default_factory=list)

    def append(self, text: str, page: int | None = None) -> None:
        if text:
            self.text = (self.text + "\n" + text).strip()
            if page is not None:
                if self.page_texts and self.page_texts[-1][0] == page:
                    self.page_texts[-1] = (page, self.page_texts[-1][1] + "\n" + text)
                else:
                    self.page_texts.append((page, text))


def _heading_match(line: tuple, body_size: float, next_text: str = "", title: str = "",
                   allow_generic: bool = True):
    """Return canonical section name if a line looks like a section heading."""
    text, size, bold = line[0], line[1], line[2]
    text = text.strip().rstrip(":-–—").strip()
    if (len(text) > 160 or not text or CAPTION.match(text) or
            text.lower() in {"contents", "table of contents"} or
            re.match(r"^(?:professor|dr\.|by\s|series editor\b)", text, re.I) or
            (title and re.sub(r"\W+", "", text).casefold() == re.sub(r"\W+", "", title).casefold())):
        return None
    # heading must be short-ish and either numbered, bold, big, or ALL CAPS
    numbered = HEADING_NUM.match(text)
    roman = HEADING_ROMAN.match(text)
    stripped = numbered.group(3) if numbered else roman.group(1) if roman else text
    # Font substitutions sometimes separate a heading's initial character.
    for canonical, aliases in SECTION_MAP:
        if re.fullmatch(canonical[:1] + r"\s+" + re.escape(canonical[1:]), stripped, re.I):
            stripped = canonical
            break
    if roman:
        stripped = re.sub(r"\b([B-HJ-Z])\s+(?=[a-z]{2,}\b)", r"\1", stripped)
    title_case = (stripped[:1].isupper() and
                  all(word[:1].isupper() or word.lower() in {"a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "the", "to", "with"}
                      for word in stripped.split()))
    conventional = next((canon for canon, aliases in SECTION_MAP
                         for alias in aliases if re.fullmatch(alias, stripped, re.I)), None)
    if conventional:
        return conventional
    if (re.search(r"[.!?;,]|\d", stripped) or len(stripped.split()) < 2
            or (not numbered and not roman and not title_case and not ALL_CAPS.fullmatch(stripped))):
        return None
    looks_heading = (
        ((numbered or roman) and size >= body_size - 1.5 and title_case and len(stripped) < 150)
        or (bold and size >= body_size - 1.5 and len(stripped) < 100)
        or (size >= body_size + 1.5)
    ) or bool(ALL_CAPS.match(stripped) and len(stripped) < 60)
    # Text extraction often reports identical font metadata for every line.
    # Exact conventional headings and short title-case lines before prose are
    # still useful structure, even without a bold/font-size signal.
    plain_heading = bool(
        len(stripped) < 80 and 2 <= len(stripped.split()) <= 9 and
        not re.search(r"[.!?]$|\.{2,}", stripped) and
        (title_case or ALL_CAPS.fullmatch(stripped)) and
        len(next_text) >= 55
    )
    looks_heading = looks_heading or plain_heading
    if not looks_heading:
        return None

    if (allow_generic and len(stripped.split()) <= 18 and any(len(word) > 2 for word in stripped.split())
            and re.fullmatch(r"[A-Za-z][A-Za-z &/\-]{2,149}", stripped)
            and (len(next_text) >= 40 or roman)
            and (numbered or roman or bold or size >= body_size + 1.5 or plain_heading)):
        return re.sub(r"\s+", " ", stripped).strip().lower()
    return None


def _structure_lines(lines: list[tuple], body_size: float) -> list[tuple]:
    """Rejoin consecutive display lines belonging to one report heading."""
    out, index = [], 0
    while index < len(lines):
        text, size, bold = lines[index]
        if ((size >= body_size + 1.5 or re.fullmatch(r"[IVXLCDM]{1,6}[.)]", text))
                and not re.search(r"[.!?;]$", text) and not INLINE_SECTION.match(text)):
            while index + 1 < len(lines):
                next_text, next_size, next_bold = lines[index + 1]
                if (abs(next_size - size) > .8 or len(text + " " + next_text) > 160
                        or re.search(r"[.!?;]$", next_text)
                        or INLINE_SECTION.match(next_text)
                        or re.match(r"^(?:by\s|dr\.)", next_text, re.I)):
                    break
                text += " " + next_text
                index += 1
        out.append((text, size, bold))
        index += 1
    return out


def _contents_page(page: object, index: int) -> bool:
    if index > 3:
        return False
    lines = [line[0].strip() for line in page.lines if line[0].strip()]
    return (any(re.fullmatch(r"(?:table of )?contents", line, re.I) for line in lines[:5])
            and sum(bool(TOC_ENTRY.match(line) or TOC_SIMPLE_ENTRY.match(line))
                    for line in lines) >= 2)


def _running_matter(doc: ExtractedDoc) -> set[str]:
    """Repeated top/bottom lines across pages are running matter, not prose."""
    top, bottom = Counter(), Counter()
    for page in doc.pages:
        lines = [line[0].strip() for line in page.lines if line[0].strip()]
        top.update(set(lines[:1]))
        bottom.update(set(lines[-1:]))
    return {line for counter in (top, bottom) for line, count in counter.items()
            if count >= 2 and len(line) <= 100}


def sectionize(doc: ExtractedDoc, body_size: float | None = None) -> list[DetectedSection]:
    if body_size is None:
        sizes = sorted(s for p in doc.pages for (_, s, _) in p.lines)
        body_size = sizes[len(sizes) // 2] if sizes else 10.0

    sections: list[DetectedSection] = []
    current: DetectedSection | None = None
    last_canon: str | None = None
    running_matter = _running_matter(doc)
    # In a conventional research paper, unrecognized headings are often
    # technical subsections. Keeping their prose inside Methods/Experiments
    # prevents sparse dossiers and false "sections" from table headings.
    canonical_names = {
        name for page_index, page in enumerate(doc.pages)
        if not _contents_page(page, page_index)
        for line in _structure_lines(page.lines, body_size)
        if (name := _heading_match(line, body_size, title=doc.title,
                                   allow_generic=False))
    }
    academic_structure = (len(canonical_names) >= 4
                          and bool(canonical_names & {"methods", "experiments"}))

    for page_index, page in enumerate(doc.pages):
        if _contents_page(page, page_index):
            continue
        useful = [line for index, line in enumerate(page.lines)
                  if line[0].strip() not in running_matter
                  and not (index >= len(page.lines) - 2 and PAGE_NUMBER.fullmatch(line[0].strip()))
                  and not TOC_ENTRY.match(line[0].strip())]
        useful = _structure_lines(useful, body_size)
        for index, line in enumerate(useful):
            text = line[0].strip()
            next_text = useful[index + 1][0].strip() if index + 1 < len(useful) else ""
            if (page_index > 0 and doc.title and current and current.name in {"front matter", "preface"}
                    and re.sub(r"\W+", "", text).casefold() == re.sub(r"\W+", "", doc.title).casefold()):
                current = DetectedSection(name="introduction", position=len(sections), page_start=page.number)
                sections.append(current)
                last_canon = "introduction"
                continue
            inline = INLINE_SECTION.match(text)
            if inline:
                canon = "keywords" if re.match(r"key|index", inline.group(1), re.I) else "abstract"
                current = DetectedSection(name=canon, position=len(sections), page_start=page.number)
                sections.append(current)
                last_canon = canon
                current.append(inline.group(2).strip(), page.number)
                continue
            canon = _heading_match(line, body_size, next_text, doc.title,
                                   allow_generic=not academic_structure)
            if canon and canon != last_canon and not _false_positive(text, canon, sections):
                current = DetectedSection(name=canon, position=len(sections), page_start=page.number)
                sections.append(current)
                last_canon = canon
                continue
            if current is None:
                current = DetectedSection(name="front matter", position=0, page_start=page.number)
                sections.append(current)
            current.append(text, page.number)

    # merge duplicates keeping first occurrence (e.g. "results" appearing twice)
    merged: dict[str, DetectedSection] = {}
    for s in sections:
        if s.name in merged:
            merged[s.name].text = (merged[s.name].text + "\n" + s.text).strip()
            merged[s.name].page_texts.extend(s.page_texts)
        else:
            merged[s.name] = s

    out = [s for s in merged.values() if s.text.strip()]
    out.sort(key=lambda s: s.position)
    for i, s in enumerate(out):
        s.position = i
    return out


def _false_positive(text: str, canon: str, existing: list[DetectedSection]) -> bool:
    """Reject headings that appear inside a references block (citation titles)."""
    if existing and existing[-1].name == "references":
        return True
    # "Introduction to ..." mid-sentence starts
    if canon == "introduction" and text.lower().startswith(("in this", "we ")):
        return True
    return False


def fallback_page_sections(doc: ExtractedDoc) -> list[DetectedSection]:
    """No headings found → treat each page (or page pair) as a section."""
    out = []
    running_matter = _running_matter(doc)
    for i, p in enumerate(doc.pages):
        if _contents_page(p, i):
            continue
        raw_lines = [line[0] for line in p.lines] if p.lines else p.text.splitlines()
        kept = [line.strip() for j, line in enumerate(raw_lines)
                if line.strip() not in running_matter
                and not (j >= len(raw_lines) - 2 and PAGE_NUMBER.fullmatch(line.strip()))
                and not TOC_ENTRY.match(line.strip())]
        text = "\n".join(line for line in kept if line)
        if text:
            out.append(DetectedSection(name=f"page {p.number}", position=len(out),
                                       page_start=p.number, text=text, page_texts=[(p.number, text)]))
    return out or [DetectedSection(name="document", position=0, page_start=1, text=doc.full_text)]


def build_sections(doc: ExtractedDoc) -> list[DetectedSection]:
    secs = sectionize(doc)
    body_names = {s.name for s in secs} - {"front matter", "abstract", "keywords",
                                          "references", "appendix"}
    if not body_names and not any(s.name == "abstract" for s in secs):
        return fallback_page_sections(doc)
    return secs
