"""PDF -> spans.json + paper_meta.json.

    PDF ──pymupdf4llm──▶ per-page chunks: reading-ordered, dehyphenated text
                          + page_boxes [{class, bbox, pos:[start,end]}]
         │
         ├─ drop boxes whose class is page-header / page-footer
         ├─ stop at the first box that is a References / Bibliography heading
         ├─ split each text box into sentences (abbreviation-aware regex)
         └─ for each sentence: align its characters to PyMuPDF words inside
            the box (whitespace, hyphens and ligatures normalised away) and
            union the word rects per line -> bboxes

A sentence that crosses a column or page break is split into two spans: boxes
are per column, chunks are per page.

Public API: extract(pdf) -> Extraction, render_crop(pdf, span) -> PNG bytes,
NoTextLayer.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf
import pymupdf4llm

from .schema import PaperMeta, Span

MIN_CHARS_PER_PAGE = 200
HEADER_FOOTER_CLASSES = {"page-header", "page-footer"}
SKIP_CLASSES = HEADER_FOOTER_CLASSES | {"table", "figure", "picture", "image"}
REFERENCES_RE = re.compile(r"^\s*(#+\s*)?(\**)?\s*(references|bibliography|works cited)\b", re.I)
CROP_PAD_PT = 24
CROP_DPI = 110

# Case-insensitive scientific abbreviations ("fig.", "Fig.", "et al.", "e.g.") ...
_ABBREVIATIONS_CI = ("fig", "figs", "eq", "eqs", "ref", "refs", "et al", "e.g", "i.e", "vs", "cf", "ca", "approx", "vol", "pp", "inc", "ltd")
# ... and capitalised titles, matched case-sensitively so the unit "ms." never reads as "Ms.".
_ABBREVIATIONS_CS = ("Dr", "Prof", "Mr", "Mrs", "Ms", "St", "Jr", "Sr", "No")
_ABBREV_RE_CI = re.compile(r"(?:^|\W)(?:" + "|".join(re.escape(a) for a in _ABBREVIATIONS_CI) + r")\.$", re.I)
_ABBREV_RE_CS = re.compile(r"(?:^|\W)(?:" + "|".join(re.escape(a) for a in _ABBREVIATIONS_CS) + r")\.$")
# "Fig. 1." / "Table 2." / "Eq. 3a." are labels, not sentence ends.
_LABEL_NUMBER_RE = re.compile(r"\b(?:Fig|Figs|Figure|Table|Eq|Eqs|Equation)\.?\s*\d+[A-Za-z]?\.$")


def _ends_with_abbreviation(s: str) -> bool:
    return bool(_ABBREV_RE_CI.search(s) or _ABBREV_RE_CS.search(s) or _LABEL_NUMBER_RE.search(s))
_SENTENCE_BREAK_RE = re.compile(r"([.!?][\"')\]]?)(\s+)(?=[\"'(\[]?[A-Z0-9])")
_LIGATURES = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"}
_YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")


class NoTextLayer(Exception):
    """Raised for scanned PDFs. The message is the user-facing explanation."""


@dataclass
class Extraction:
    paper_id: str
    meta: PaperMeta
    spans: list[Span] = field(default_factory=list)
    pages_seen: int = 0
    stopped_at_references: bool = False


def sha256_of(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def extract(pdf: Path | str) -> Extraction:
    pdf = Path(pdf)
    doc = pymupdf.open(pdf)
    _guard_text_layer(doc, pdf)
    chunks = pymupdf4llm.to_markdown(doc, page_chunks=True)
    meta = paper_meta(doc, chunks)
    result = Extraction(paper_id=sha256_of(pdf), meta=meta)

    counter = 0
    for chunk in chunks:
        page_index = chunk["metadata"]["page_number"] - 1
        page = doc[page_index]
        result.pages_seen += 1
        text = chunk["text"]
        for box in sorted(chunk.get("page_boxes") or [], key=lambda b: b["index"]):
            cls = box.get("class", "text")
            start, end = box["pos"]
            box_text = text[start:end]
            if REFERENCES_RE.match(box_text):
                result.stopped_at_references = True
                break
            if cls in SKIP_CLASSES:
                continue
            rect = pymupdf.Rect(*box["bbox"])
            words = page.get_text("words", clip=rect, sort=True)
            if not words:
                continue
            aligner = _WordAligner(words)
            for sentence in split_sentences(_clean(box_text)):
                bboxes = aligner.bboxes_for(sentence)
                if not bboxes:
                    continue
                counter += 1
                result.spans.append(
                    Span(id=f"sp-{page_index + 1:03d}-{counter:04d}", page=page_index + 1, text=sentence, bboxes=bboxes)
                )
        if result.stopped_at_references:
            break
    doc.close()
    return result


def render_crop(pdf: Path | str, span: Span, pad: float = CROP_PAD_PT, dpi: int = CROP_DPI) -> bytes:
    """PNG of the span's page region: union of its line boxes, padded, clipped
    to the page, with the lines highlighted."""
    doc = pymupdf.open(pdf)
    page = doc[span.page - 1]
    union = pymupdf.Rect(span.bboxes[0])
    for b in span.bboxes[1:]:
        union |= pymupdf.Rect(b)
    clip = pymupdf.Rect(union.x0 - pad, union.y0 - pad, union.x1 + pad, union.y1 + pad) & page.rect
    shape = page.new_shape()
    for b in span.bboxes:
        shape.draw_rect(pymupdf.Rect(b))
    shape.finish(color=(0.71, 0.31, 0.18), fill=(0.71, 0.31, 0.18), fill_opacity=0.18, width=0.6)
    shape.commit()
    png = page.get_pixmap(clip=clip, dpi=dpi).tobytes("png")
    doc.close()
    return png


def paper_meta(doc: pymupdf.Document, chunks: list[dict]) -> PaperMeta:
    """Verbatim metadata; falls back to the largest text on page 1 for the title.
    Never guesses authors: an empty list is more honest than a wrong name."""
    md = doc.metadata or {}
    title = (md.get("title") or "").strip()
    if not title or title.lower().endswith((".pdf", ".doc", ".docx")) or len(title) < 8:
        title = _largest_text_line(doc[0]) or (chunks[0]["text"].strip().splitlines() or ["Untitled"])[0].strip("# ").strip()
    authors = [a.strip() for a in re.split(r";|,\s*(?=[A-Z][a-z]+\s)|\band\b", md.get("author") or "") if a.strip()]
    venue = (md.get("subject") or "").strip() or None
    year = None
    for candidate in (md.get("creationDate") or "", chunks[0]["text"][:2000] if chunks else ""):
        m = _YEAR_RE.search(candidate)
        if m:
            year = int(m.group(1))
            break
    return PaperMeta(title=title, authors=authors, venue=venue, year=year, pages=doc.page_count)


# --------------------------------------------------------------------------- #
# Sentence splitting
# --------------------------------------------------------------------------- #


def split_sentences(text: str) -> list[str]:
    """Split on sentence punctuation followed by whitespace and a capital or
    digit, unless the punctuation belongs to a known abbreviation."""
    pieces: list[str] = []
    last = 0
    for m in _SENTENCE_BREAK_RE.finditer(text):
        candidate = text[last : m.end(1)].strip()
        if _ends_with_abbreviation(candidate.rstrip("\"')]")):
            continue  # "Fig." / "et al." / "e.g." are not sentence ends
        pieces.append(candidate)
        last = m.end()
    tail = text[last:].strip()
    if tail:
        pieces.append(tail)
    return [p for p in pieces if len(p) > 1]


_HYPHEN_VARIANTS = {"­": "-", "‐": "-", "‑": "-", "−": "-"}  # soft hyphen, hyphen, nb-hyphen, minus


def _clean(text: str) -> str:
    text = text.replace("\n", " ")
    text = re.sub(r"[*_`#>]+", " ", text)  # markdown residue from pymupdf4llm
    for lig, plain in _LIGATURES.items():
        text = text.replace(lig, plain)
    for variant, plain in _HYPHEN_VARIANTS.items():
        text = text.replace(variant, plain)
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------------------------------- #
# Word alignment
# --------------------------------------------------------------------------- #


def _norm_key(s: str) -> str:
    for lig, plain in _LIGATURES.items():
        s = s.replace(lig, plain)
    s = unicodedata.normalize("NFKC", s)
    return re.sub(r"[\s\-­‐‑–]+", "", s)


class _WordAligner:
    """Maps a sentence to the word rects that spell it, per line.

    Both the sentence and the box's words are reduced to a key with all
    whitespace and hyphens removed, so a word hyphenated across a line break
    ("partici-" / "pants") and its dehyphenated sentence form align.
    """

    def __init__(self, words: list[tuple]):
        self.words = words
        self.keys = [_norm_key(w[4]) for w in words]
        self.stream = "".join(self.keys)
        self.owner: list[int] = []
        for i, k in enumerate(self.keys):
            self.owner.extend([i] * len(k))
        self.cursor = 0

    def bboxes_for(self, sentence: str) -> list[list[float]]:
        key = _norm_key(sentence)
        if not key:
            return []
        at = self.stream.find(key, self.cursor)
        if at < 0:
            at = self.stream.find(key)
            if at < 0:
                return self._fuzzy(key)
        self.cursor = at + len(key)
        return self._rects(at, at + len(key))

    def _fuzzy(self, key: str) -> list[list[float]]:
        # Fall back to the longest head of the sentence that does match (>= 12 chars).
        for n in range(len(key), 11, -1):
            at = self.stream.find(key[:n], self.cursor)
            if at >= 0:
                self.cursor = at + n
                return self._rects(at, at + n)
        return []

    def _rects(self, a: int, b: int) -> list[list[float]]:
        if b <= a or a >= len(self.owner):
            return []
        first, last = self.owner[a], self.owner[min(b, len(self.owner)) - 1]
        lines: dict[tuple[int, int], pymupdf.Rect] = {}
        for w in self.words[first : last + 1]:
            k = (w[5], w[6])  # block, line
            r = pymupdf.Rect(w[:4])
            lines[k] = (lines[k] | r) if k in lines else r
        return [[round(r.x0, 2), round(r.y0, 2), round(r.x1, 2), round(r.y1, 2)] for r in lines.values()]


# --------------------------------------------------------------------------- #
# Guards and helpers
# --------------------------------------------------------------------------- #


def _guard_text_layer(doc: pymupdf.Document, pdf: Path) -> None:
    n = max(doc.page_count, 1)
    chars = sum(len(doc[i].get_text("text")) for i in range(min(n, 8)))
    if chars / min(n, 8) < MIN_CHARS_PER_PAGE:
        raise NoTextLayer(
            f"{pdf.name}: no text layer (about {chars // min(n, 8)} characters per page). "
            "OCR is unsupported in v1; find a text PDF of this paper."
        )


def _largest_text_line(page: pymupdf.Page) -> str | None:
    best, best_size = None, 0.0
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            size = max((s["size"] for s in line["spans"]), default=0)
            text = "".join(s["text"] for s in line["spans"]).strip()
            if size > best_size and len(text) >= 8:
                best, best_size = text, size
    return best
