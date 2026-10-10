"""Deterministic, disposable PDF corpus for the live end-to-end probe.

These documents contain invented examples only. The caller chooses an output
directory, normally a TemporaryDirectory removed after the probe.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import textwrap

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz


CORPUS = {
    "empirical": (
        "Urban Cooling Pilot Study",
        [
            [
                ("Abstract", True),
                ("We study whether shaded bus stops reduce heat exposure. A four-week pilot in two invented districts compared 120 shaded stops with 120 ordinary stops. The shaded stops had lower measured surface temperatures.", False),
                ("Introduction", True),
                ("Hot waiting areas can discourage transit use during summer. The study asks whether low-cost shade structures change the temperature that riders experience while waiting.", False),
                ("Methods", True),
                ("Technicians measured pavement temperature at noon on matched sunny days using calibrated infrared thermometers. The pilot used 240 stops and paired each shaded stop with an unshaded stop in the same district.", False),
            ],
            [
                ("Results", True),
                ("Across 240 stops, shaded locations averaged 31.2 C and comparison locations averaged 35.8 C at noon. The observed difference was 4.6 C, although this pilot was not randomized.", False),
                ("Limitations", True),
                ("The four-week observation period did not include winter weather, and district selection may limit generalization to other cities.", False),
                ("Conclusion", True),
                ("The pilot suggests that shade can reduce surface heat at bus stops. A longer randomized study would clarify whether the change also affects rider comfort and transit use.", False),
            ],
        ],
    ),
    "policy": (
        "Public Data Access Policy Essay",
        [
            [
                ("Executive Summary", True),
                ("This essay argues that public research data needs an appeal process before agencies deny access. An independent board could review disputed decisions while protecting personal records.", False),
                ("Problem Context", True),
                ("Researchers currently receive inconsistent explanations when two agencies interpret the same disclosure rule differently. People named in the records need clear privacy safeguards.", False),
            ],
            [
                ("Policy Options", True),
                ("One option is a central repository, while another is a federated catalogue that leaves records with each agency. The federated catalogue would reduce bulk transfers of sensitive records.", False),
                ("Recommendations", True),
                ("We recommend a six-month appeal pilot with published decisions, quarterly privacy reviews, and a right for affected people to challenge disclosure.", False),
                ("Risks", True),
                ("The board may delay urgent access requests. Small agencies could lack staff to redact personal records safely unless the pilot includes technical support.", False),
            ],
        ],
    ),
    "strategy": (
        "Regional Transit Strategy Report",
        [
            [
                ("Table of Contents", True),
                ("Executive Summary 2", False),
                ("Findings 2", False),
                ("Implementation Plan 3", False),
                ("Recommendations 3", False),
                ("Risks 3", False),
            ],
            [
                ("Executive Summary", True),
                ("The invented regional council recommends a staged increase in bus frequency after finding long waits on three routes. Its first priority is a reversible pilot on two routes.", False),
                ("Findings", True),
                ("A survey of 800 invented riders found that average weekday waits increased by 18% between 2020 and 2024. Riders with mobility needs reported the longest transfer delays.", False),
            ],
            [
                ("Implementation Plan", True),
                ("The first six months would add buses to two high-demand routes. The council would publish monthly wait-time and accessibility reports before deciding on expansion.", False),
                ("Recommendations", True),
                ("We recommend a two-route pilot with quarterly public reviews and an accessible feedback channel for riders.", False),
                ("Risks", True),
                ("The pilot could crowd adjacent routes if transfer times change before the whole network is reviewed. Budget forecasts depend on fuel prices.", False),
            ],
        ],
    ),
    "figures": (
        "Water Filter Bench Evaluation",
        [
            [
                ("Abstract", True),
                ("We evaluate three invented water filter cartridges under a fixed bench protocol. Cartridge B removed more particles than the comparison cartridges in repeated trials.", False),
                ("Methods", True),
                ("The bench test ran 30 trials per cartridge with the same source water. Each trial counted particles per litre before and after filtration.", False),
            ],
            [
                ("Figure 1: Particle counts by cartridge across repeated trials.", False),
                ("Results", True),
                ("Cartridge B reduced measured particle count by 42%, compared with 27% for A and 25% for C. The result is a laboratory measurement, not a claim about long-term household safety.", False),
                ("Table 1: Summary of the 30 trials for each cartridge.", False),
                ("Limitations", True),
                ("The test used one water source and did not measure microbial contamination or durability after three months.", False),
            ],
        ],
    ),
    "narrative": (
        "Community Library Evidence Review",
        [
            [
                ("Local libraries are considering later opening hours. This narrative review summarizes three invented case studies from small towns and compares their reported attendance patterns.", False),
                ("One case study reported more evening visits after Friday hours were extended. Another found that staffing costs rose without a clear increase in visits.", False),
            ],
            [
                ("The cases used different definitions of attendance, so their numbers cannot be pooled into a single effect estimate. The review does not claim that later hours cause higher use.", False),
                ("A local pilot should count visits consistently, record staffing cost, and ask patrons whether the new hours improve access for them.", False),
            ],
        ],
    ),
}


def _write_page(page, rows: list[tuple[str, bool]], *, title: str, page_no: int,
                running_header: bool = False) -> None:
    y = 72
    if running_header:
        page.insert_text((52, 34), title, fontsize=8, fontname="helv")
    elif page_no == 1:
        page.insert_text((52, 38), title, fontsize=17, fontname="hebo")
    for content, heading in rows:
        if heading:
            y += 16
            page.insert_text((52, y), content, fontsize=12, fontname="hebo")
            y += 20
        else:
            for line in textwrap.wrap(content, width=88):
                page.insert_text((52, y), line, fontsize=10, fontname="helv")
                y += 15
            y += 13
    page.insert_text((550, 800), str(page_no), fontsize=8, fontname="helv")


def generate_corpus(directory: Path) -> dict[str, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for name, (title, pages) in CORPUS.items():
        path = directory / f"{name}.pdf"
        pdf = fitz.open()
        try:
            for page_no, rows in enumerate(pages, 1):
                page = pdf.new_page()
                _write_page(page, rows, title=title, page_no=page_no,
                            running_header=name in {"strategy", "narrative"})
            pdf.save(path)
        finally:
            pdf.close()
        paths[name] = path
    return paths


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path,
                        help="Where to create the five synthetic PDFs")
    args = parser.parse_args()
    created = generate_corpus(args.directory)
    for name, path in created.items():
        print(f"{name}: {path}")
