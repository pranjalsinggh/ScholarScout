"""PDF text extraction with PyMuPDF - page-level text, title via font heuristics,
figure/table caption detection. Gracefully handles messy PDFs."""
from __future__ import annotations

import re
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

try:
    import pymupdf as fitz  # PyMuPDF ≥ 1.24
except ImportError:  # pragma: no cover - older installs
    import fitz

CAPTION_RE = re.compile(r"^(Figure|Fig\.?|Table)\s+(\d+)[.:)(-]?\s*(.{3,400})", re.I)
EQUATION_CHARS = re.compile(r"[=∑∫∂∇≈≤≥±√∈×⊕⇒↦√λμσθπ]")


class PDFLimitError(ValueError):
    """A document exceeds the limits of this anonymous analysis service."""


_parser_lock = threading.RLock()
_parsers: dict[str, set] = {}
_parser_condition = threading.Condition(_parser_lock)
_parser_admitted: dict[str, int] = {}


@contextmanager
def _parser_admission(owner):
    if not owner:
        yield
        return
    from app.database.database import SessionLocal
    from app.security.session import owner_write
    with SessionLocal() as db:
        with owner_write(db, owner):
            yield


@contextmanager
def _parser_capacity(owner, workers, seconds):
    deadline = time.monotonic() + seconds
    with _parser_condition:
        while (sum(_parser_admitted.values()) >= workers or
               (owner and _parser_admitted.get(owner, 0))):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PDFLimitError("PDF reading capacity is busy. Please retry in a moment.")
            _parser_condition.wait(timeout=min(remaining, .5))
        _parser_admitted[owner] = _parser_admitted.get(owner, 0) + 1
    try:
        yield
    finally:
        with _parser_condition:
            remaining = _parser_admitted[owner] - 1
            if remaining:
                _parser_admitted[owner] = remaining
            else:
                _parser_admitted.pop(owner, None)
            _parser_condition.notify_all()


def cancel_owner_parsers(owner: str) -> None:
    """Kill and reap native parsers before reporting that a session was erased."""
    with _parser_lock:
        processes = list(_parsers.get(owner, ()))
        for process in processes:
            if process.poll() is None:
                process.kill()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        _parser_condition.notify_all()


def extract_pdf(path: str, *, source_name: str | None = None, owner: str = "") -> ExtractedDoc:
    from app.config import settings
    with _parser_capacity(owner, settings.analysis_workers, settings.pdf_parse_timeout_seconds):
        return _extract_pdf_isolated(path, source_name=source_name, owner=owner)


def _extract_pdf_isolated(path: str, *, source_name: str | None = None, owner: str = "") -> ExtractedDoc:
    """Parse untrusted native PDF content in a killable, resource-limited child.

    The result is bounded JSON, never a pickle supplied by the parser. The
    child receives no provider keys or browser token and imports no ML model.
    """
    from app.config import settings, UPLOAD_DIR
    limits = {"pages": settings.max_pdf_pages, "text": settings.max_pdf_text_chars,
              "seconds": settings.pdf_parse_timeout_seconds,
              "memory_mb": settings.pdf_worker_memory_mb}
    worker = Path(__file__).with_name("pdf_worker.py")
    env = {key: value for key, value in os.environ.items()
           if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "LANG", "LC_ALL"}}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    with _parser_admission(owner):
        directory = tempfile.TemporaryDirectory(prefix=f"parse-{owner or 'anonymous'}-result-", dir=UPLOAD_DIR)
    with directory as temporary:
        output = Path(temporary) / "result.json"
        command = [sys.executable, "-B", "-I", str(worker), str(Path(path).resolve()),
                   str(output), json.dumps(limits), source_name or Path(path).name]
        process = None
        try:
            with _parser_admission(owner):
                with _parser_lock:
                    process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                               env=env, close_fds=True,
                                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    _parsers.setdefault(owner, set()).add(process)
            try:
                process.wait(timeout=limits["seconds"])
            except subprocess.TimeoutExpired as exc:
                process.kill()
                process.wait()
                raise PDFLimitError("This PDF took too long to read. Try a shorter or simpler PDF.") from exc
            if not output.exists() or output.stat().st_size > limits["text"] * 8 + 2_000_000:
                raise PDFLimitError("This PDF exceeded the analysis resource budget. Try a shorter or simpler PDF.")
            payload = json.loads(output.read_text(encoding="utf-8"))
            if payload.get("error"):
                # The worker emits fixed messages, not native parser paths or errors.
                raise PDFLimitError(payload["error"])
            if process.returncode != 0:
                raise PDFLimitError("This PDF could not be read safely. Try another PDF.")
            pages = [ExtractedPage(number=p["number"], text=p["text"],
                                   lines=[tuple(line) for line in p["lines"]])
                     for p in payload["pages"]]
            if (len(pages) > limits["pages"] or
                    sum(len(p.text) for p in pages) > limits["text"]):
                raise PDFLimitError("This PDF exceeded the analysis text budget.")
            return ExtractedDoc(pages=pages, **{key: value for key, value in payload.items()
                                               if key != "pages"})
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait()
            with _parser_lock:
                _parsers.get(owner, set()).discard(process)
                if not _parsers.get(owner):
                    _parsers.pop(owner, None)


@dataclass
class ExtractedPage:
    number: int          # 1-based
    text: str
    lines: list[tuple[str, float, float]] = field(default_factory=list)  # (text, size, boldness)


@dataclass
class ExtractedDoc:
    pages: list[ExtractedPage]
    title: str = ""
    captions: list[dict] = field(default_factory=list)   # {kind,number,text,page}
    tables: list[dict] = field(default_factory=list)     # {page, text}
    equation_density: float = 0.0
    has_text_layer: bool = True
    source_name: str = ""
    pdf_metadata: dict[str, str] = field(default_factory=dict)

    @property
    def full_text(self) -> str:
        return "\n\n".join(p.text for p in self.pages)

    @property
    def page_count(self) -> int:
        return len(self.pages)


def _clean_line(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\u00ad", "")).strip()


def _layout_lines(page, *, max_chars=100000, max_fragments=10000) -> list[tuple[str, float, float]]:
    """Join fragments at the same baseline and retain column reading order.

    Some exported PDFs store every justified word as a separate text line.
    Treating those fragments as headings loses structure and reverses clauses.
    Geometry also prevents left/right newspaper columns being interleaved.
    """
    fragments = []
    character_count = 0
    for block in page.get_text("dict", flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES).get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
            if not spans:
                continue
            parts, previous = [], None
            for span in spans:
                value = span.get("text", "")
                if previous and parts and parts[-1] and value:
                    gap = span["bbox"][0] - previous["bbox"][2]
                    if parts[-1][-1].isalnum() and value[0].isalnum() and gap > .16 * span.get("size", 10):
                        parts.append(" ")
                parts.append(value)
                previous = span
            text = _clean_line("".join(parts))
            character_count += len(text)
            if character_count > max_chars or len(fragments) >= max_fragments:
                raise PDFLimitError("This PDF has too much text or layout detail on a page. Try a simpler PDF.")
            if not text:
                continue
            direction = line.get("dir", (1, 0))
            if abs(direction[1]) > .4 and not re.search(r"arXiv:\d{4}\.\d{4,5}", text, re.I):
                continue
            weights = Counter()
            for span in spans:
                weights[round(span.get("size", 10), 1)] += len(span["text"].strip())
            size = weights.most_common(1)[0][0]
            bold_chars = sum(len(s["text"].strip()) for s in spans
                             if "bold" in (s.get("font") or "").lower() or s.get("flags", 0) & 16)
            anchor = max(spans, key=lambda s: len(s["text"].strip()))
            baseline = anchor.get("origin", (0, line["bbox"][3]))[1]
            fragments.append({"text": text, "size": size,
                              "bold": float(bold_chars >= sum(weights.values()) / 2),
                              "box": line["bbox"], "baseline": baseline,
                              "order": len(fragments)})

    split = page.rect.width / 2
    # Report columns can be offset from the centre of the sheet. Find two
    # repeated paragraph starts and their gutter instead of assuming equal
    # margins. Only substantial narrow lines participate (not isolated words).
    narrow_lines = [f for f in fragments if page.rect.width * .18 <
                    f["box"][2] - f["box"][0] < page.rect.width * .48]
    starts = Counter(round(f["box"][0] / 3) * 3 for f in narrow_lines)
    anchors = [x for x, count in starts.most_common() if count >= 8]
    anchor_pairs = [(left, right) for left in anchors for right in anchors
                    if right - left > page.rect.width * .25]
    if anchor_pairs:
        left, right = max(anchor_pairs, key=lambda pair: min(starts[pair[0]], starts[pair[1]]))
        left_ends = sorted(f["box"][2] for f in narrow_lines if abs(f["box"][0] - left) <= 3)
        left_end = left_ends[len(left_ends) // 2]
        if left_end < right:
            split = (left_end + right) / 2
    substantial_left = [f for f in fragments if f["box"][2] < split - 4
                        and f["box"][2] - f["box"][0] > page.rect.width * .22]
    substantial_right = [f for f in fragments if f["box"][0] > split + 4
                         and f["box"][2] - f["box"][0] > page.rect.width * .22]
    column_layout = len(substantial_left) >= 8 and len(substantial_right) >= 8
    # Group by baseline first; a font change or an inline equation does not
    # turn ordinary body prose into a larger-font section heading.
    rows: list[list[dict]] = []
    for fragment in sorted(fragments, key=lambda f: (f["baseline"], f["box"][0])):
        if rows and abs(fragment["baseline"] - rows[-1][0]["baseline"]) <= 1.0:
            rows[-1].append(fragment)
        else:
            rows.append([fragment])
    joined = []
    for row in rows:
        current = None
        for fragment in sorted(row, key=lambda f: f["box"][0]):
            gap = fragment["box"][0] - current["box"][2] if current else 0
            crosses_gutter = (column_layout and current and current["box"][2] < split
                              and fragment["box"][0] > split)
            if current and not crosses_gutter and -.1 * current["size"] <= gap <= 1.8 * max(current["size"], fragment["size"]):
                current["text"] += " " + fragment["text"]
                current["order"] = min(current["order"], fragment["order"])
                current["box"] = (current["box"][0], min(current["box"][1], fragment["box"][1]),
                                  fragment["box"][2], max(current["box"][3], fragment["box"][3]))
            else:
                current = dict(fragment)
                joined.append(current)

    left = [f for f in joined if f["box"][2] < split - 4]
    right = [f for f in joined if f["box"][0] > split + 4]
    two_columns = column_layout or (len(left) >= 8 and len(right) >= 8 and (len(left) + len(right)) >= .7 * len(joined))
    if two_columns:
        narrow = left + right
        wide = sorted((f for f in joined if f not in narrow), key=lambda f: f["baseline"])
        ordered = []
        for separator in wide:
            band = [f for f in narrow if f["baseline"] < separator["baseline"] - 1]
            ordered.extend(sorted(band, key=lambda f: (f["box"][0] > split, f["baseline"], f["box"][0])))
            narrow = [f for f in narrow if f not in band]
            ordered.append(separator)
        ordered.extend(sorted(narrow, key=lambda f: (f["box"][0] > split, f["baseline"], f["box"][0])))
    else:
        ordered = sorted(joined, key=lambda f: f["order"])
    return [(f["text"], f["size"], f["bold"]) for f in ordered]


def _extract_pdf_in_process(path: str, *, source_name: str | None = None,
                            max_pages=300, max_text_chars=1500000) -> ExtractedDoc:
    doc = fitz.open(path)
    pages: list[ExtractedPage] = []
    captions: list[dict] = []
    tables: list[dict] = []
    eq_chars = 0
    total_chars = 0
    pdf_metadata = {key: value for key, value in (doc.metadata or {}).items()
                    if key in {"title", "author", "creationDate", "modDate"} and value}

    try:
        if doc.page_count > max_pages:
            raise PDFLimitError(f"This PDF exceeds the {max_pages} page limit. Split it into smaller documents.")
        for i, page in enumerate(doc):
            lines = _layout_lines(page, max_chars=min(100000, max_text_chars))
            for text, _, _ in lines:
                m = CAPTION_RE.match(text)
                if m and len(m.group(3)) > 10:
                    captions.append({"kind": "figure" if m.group(1).lower().startswith("fig") else "table",
                                     "number": int(m.group(2)), "text": m.group(3)[:300], "page": i + 1})
                eq_chars += len(EQUATION_CHARS.findall(text))
                total_chars += len(text)
                if total_chars > max_text_chars:
                    raise PDFLimitError("This PDF exceeds the extracted text limit. Split it into smaller documents.")
            pages.append(ExtractedPage(number=i + 1, text="\n".join(t for t, _, _ in lines), lines=lines))
        # The table finder needs a live PyMuPDF document. Calling it after
        # close silently returned no tables and lost table-only result data.
        tables = _extract_tables(doc, max_chars=min(200000, max_text_chars - total_chars))
    finally:
        doc.close()

    text_lengths = [len(p.text.strip()) for p in pages]
    # A cover or image-heavy first page must not mark a readable report as a
    # scanned document. Require substantive selectable text anywhere.
    has_text_layer = sum(text_lengths) > 80 and any(n > 60 for n in text_lengths)

    title = _guess_title(pages)
    embedded_title = _clean_line(pdf_metadata.get("title", ""))
    cover_words = set(re.findall(r"[a-z]{3,}", pages[0].text.lower())) if pages else set()
    embedded_words = set(re.findall(r"[a-z]{3,}", embedded_title.lower()))
    if (8 <= len(embedded_title) <= 300 and embedded_words
            and len(embedded_words & cover_words) / len(embedded_words) >= .85
            and not re.search(r"microsoft|untitled|\.docx?$", embedded_title, re.I)):
        title = embedded_title
    return ExtractedDoc(
        pages=pages,
        title=title,
        captions=captions[:40],
        tables=tables,
        equation_density=round(eq_chars / max(total_chars, 1) * 1000, 2),
        has_text_layer=has_text_layer,
        source_name=source_name or Path(path).name,
        pdf_metadata=pdf_metadata,
    )


def _guess_title(pages: list[ExtractedPage]) -> str:
    """Title = largest text in the first page, merged across consecutive big lines."""
    if not pages:
        return ""
    lines = pages[0].lines
    if not lines:
        # fallback: first non-empty line of raw text
        for raw in pages[0].text.splitlines():
            raw = raw.strip()
            if len(raw) > 8:
                return raw[:300]
        return ""

    body_size = _median_size(pages)
    stamp = re.compile(r"arXiv:\d{4}\.\d{4,5}|^\s*(provided proper|licensed for|to cite)",
                       re.I)
    candidates = [(t, s) for (t, s, b) in lines
                  if s >= body_size + 2.0 and 3 < len(t) < 300 and not stamp.search(t)]
    if not candidates:
        candidates = [(t, s) for (t, s, b) in lines[:8] if len(t) > 3 and not stamp.search(t)]

    top = max((s for _, s in candidates), default=0)
    merged: list[str] = []
    seen: set[str] = set()
    for t, s in candidates:
        key = re.sub(r"\W+", "", t).casefold()
        if s >= top - 1.5 and key not in seen:
            merged.append(t)
            seen.add(key)
    title = " ".join(merged[:4]).strip()
    # strip common arXiv side banners
    title = re.sub(r"\s*arXiv:\d{4}\.\d{4,5}(v\d+)?\s*\[.*?\]\s*.*$", "", title, flags=re.I).strip()
    return title[:300]


def _median_size(pages: list[ExtractedPage]) -> float:
    sizes = [s for p in pages[:5] for (_, s, _) in p.lines]
    if not sizes:
        return 10.0
    sizes.sort()
    return sizes[len(sizes) // 2]


def _extract_tables(doc, *, max_chars=200000) -> list[dict]:
    """Extract tables as pipe-joined text rows so their content becomes
    searchable evidence (results often live in tables, not prose)."""
    out = []
    character_count = 0
    try:
        for page in doc:
            found = page.find_tables()
            for t in found:
                try:
                    rows = t.extract()
                except Exception:
                    continue
                if not rows or len(rows) < 2:
                    continue
                lines = []
                for row in rows[:25]:
                    cells = [re.sub(r"\s+", " ", (c or "")).strip() for c in row]
                    if any(cells):
                        lines.append(" | ".join(cells))
                if len(lines) >= 2:
                    character_count += sum(len(line) for line in lines)
                    if character_count > max_chars:
                        raise PDFLimitError("This PDF exceeds the table text limit. Split it into smaller documents.")
                    out.append({"page": page.number + 1,
                                "text": f"Table from page {page.number + 1}: " + " ;; ".join(lines)})
                if len(out) >= 12:
                    return out
    except PDFLimitError:
        raise
    except Exception:
        return out
    return out


def looks_like_scanned(doc: ExtractedDoc) -> bool:
    return not doc.has_text_layer
