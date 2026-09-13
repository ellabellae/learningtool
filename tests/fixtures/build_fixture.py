"""Build a synthetic two-column research paper as a PDF.

Deterministic and fully authored, so tests can assert the exact sentences the
extractor must produce. Exercises: a one-column title page, two-column body,
running header and footer, a word hyphenated across a line break, ligatures,
a figure caption, numbers in several formats, and a References section that
must be cut off.

    python -m tests.fixtures.build_fixture out.pdf
"""

from __future__ import annotations

import sys
from pathlib import Path

import pymupdf

TITLE = "Threshold-gated feedback raises upper-alpha power and working memory in twelve participants"
AUTHORS = "A. Example; B. Sample; C. Fixture"
HEADER = "Journal of Synthetic Neuroscience · 2026"

# The body sentences, in reading order, exactly as the extractor must return them.
# Column text is written so that at least one word wraps with a hyphen.
LEFT_P2 = (
    "Alpha rhythm training was applied to twelve participants over six sessions. "
    "The reward threshold was set at 8% above each participant's baseline. "
    "Feedback appeared only when upper-alpha amplitude exceeded the threshold for 500 ms. "
    "A control group of 12 received sham feedback that was not contingent on their EEG."
)
RIGHT_P2 = (
    "Working memory improved significantly (p = 0.05) in the trained group. "
    "Mean reaction time fell from 1,200 ms to 950 ms. "
    "Fig. 1 shows the effect across sessions. "
    "The efficient fine-tuning of the threshold mattered more than session count."
)
LEFT_P3 = (
    "These results suggest that upper-alpha training is a plausible route to cognitive enhancement. "
    "Limitations include the small sample and the absence of a follow-up."
)
REFERENCES = (
    "References\n"
    "1. Klimesch W. EEG alpha and theta oscillations. Brain Res Rev. 1999;29:169-195.\n"
    "2. Example A. A fabricated citation. J Synth. 2026;1:1-2."
)

CAPTION = "Fig. 1. Upper-alpha power per session."
BOILERPLATE = "978-1-0000-0000-0/26/$26.00 ©2026 Synthetic Press. All rights reserved."

# A TrueType font with fi/ffi ligature glyphs; base-14 Helvetica has none.
LIGATURE_FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
)


def ligature_font() -> Path | None:
    for candidate in LIGATURE_FONT_CANDIDATES:
        p = Path(candidate)
        if p.exists():
            return p
    return None


HAS_LIGATURE_FONT = ligature_font() is not None

EXPECTED_BODY_SENTENCES = [
    "Alpha rhythm training was applied to twelve participants over six sessions.",
    "The reward threshold was set at 8% above each participant's baseline.",
    "Feedback appeared only when upper-alpha amplitude exceeded the threshold for 500 ms.",
    "A control group of 12 received sham feedback that was not contingent on their EEG.",
    "Working memory improved significantly (p = 0.05) in the trained group.",
    "Mean reaction time fell from 1,200 ms to 950 ms.",
    "Fig. 1 shows the effect across sessions.",
    "The efficient fine-tuning of the threshold mattered more than session count.",
    CAPTION,
    "These results suggest that upper-alpha training is a plausible route to cognitive enhancement.",
    "Limitations include the small sample and the absence of a follow-up.",
]

FONT = "helv"
W, H = 595, 842
LEFT = pymupdf.Rect(50, 90, 285, 760)
RIGHT = pymupdf.Rect(310, 90, 545, 760)


def build(out: Path | str) -> Path:
    out = Path(out)
    doc = pymupdf.open()
    doc.set_metadata({"title": TITLE, "author": AUTHORS, "subject": "Journal of Synthetic Neuroscience", "creationDate": "D:20260101000000"})

    # Page 1: title page, one column.
    p1 = doc.new_page(width=W, height=H)
    _header_footer(p1, 1)
    p1.insert_textbox(pymupdf.Rect(50, 100, 545, 200), TITLE, fontsize=18, fontname=FONT)
    p1.insert_textbox(pymupdf.Rect(50, 210, 545, 240), AUTHORS.replace(";", ","), fontsize=11, fontname=FONT)
    p1.insert_textbox(
        pymupdf.Rect(50, 260, 545, 400),
        "Abstract. We trained participants to raise upper-alpha power with threshold-gated feedback. Working memory improved.",
        fontsize=10, fontname=FONT,
    )

    # Page 2: two columns. The ligature "efficient" is written with U+FB01 so extraction must fold it.
    p2 = doc.new_page(width=W, height=H)
    _header_footer(p2, 2)
    p2.insert_textbox(LEFT, LEFT_P2, fontsize=10.5, fontname=FONT)
    lig = ligature_font()
    if lig is not None:
        p2.insert_font(fontname="lig", fontfile=str(lig))
        p2.insert_textbox(RIGHT, RIGHT_P2.replace("efficient fine", "efﬁcient ﬁne"), fontsize=10.5, fontname="lig")
    else:  # no ligature-capable font on this machine; the ligature test skips itself
        p2.insert_textbox(RIGHT, RIGHT_P2, fontsize=10.5, fontname=FONT)
    # A figure block (skipped as a picture) with a caption (kept: captions are paper text).
    p2.draw_rect(pymupdf.Rect(310, 500, 545, 620), color=(0.5, 0.5, 0.5), width=0.8)
    p2.insert_textbox(pymupdf.Rect(310, 625, 545, 660), CAPTION, fontsize=8.5, fontname=FONT)

    # Page 3: short body then References.
    p3 = doc.new_page(width=W, height=H)
    _header_footer(p3, 3)
    p3.insert_textbox(LEFT, LEFT_P3, fontsize=10.5, fontname=FONT)
    # Publisher boilerplate inside the body column, as IEEE PDFs do; must be filtered out.
    p3.insert_textbox(pymupdf.Rect(50, 300, 285, 330), BOILERPLATE, fontsize=8, fontname=FONT)
    p3.insert_textbox(pymupdf.Rect(310, 90, 545, 400), REFERENCES, fontsize=9.5, fontname=FONT)

    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out, deflate=True)
    doc.close()
    return out


def _header_footer(page: pymupdf.Page, n: int) -> None:
    page.insert_text((50, 40), HEADER, fontsize=8, fontname=FONT)
    page.insert_text((50, 815), f"page {n}", fontsize=8, fontname=FONT)


if __name__ == "__main__":
    print(build(sys.argv[1] if len(sys.argv) > 1 else "synthetic.pdf"))
