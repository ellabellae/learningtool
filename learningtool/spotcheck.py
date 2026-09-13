"""Draw every span's line boxes on its page and save PNGs, so extraction is
judged by eye before anything downstream trusts it.

    python -m learningtool.spotcheck data/papers/<id>/source.pdf out_dir/
    learn spotcheck <paper_id>
"""

from __future__ import annotations

import sys
from pathlib import Path

import pymupdf

from .extract import extract
from .schema import Span

PALETTE = [(0.71, 0.31, 0.18), (0.36, 0.37, 0.40), (0.72, 0.53, 0.04), (0.20, 0.45, 0.35)]


def spotcheck(pdf: Path | str, out_dir: Path | str, spans: list[Span] | None = None, dpi: int = 90) -> list[Path]:
    pdf, out_dir = Path(pdf), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if spans is None:
        spans = extract(pdf).spans
    doc = pymupdf.open(pdf)
    by_page: dict[int, list[Span]] = {}
    for s in spans:
        by_page.setdefault(s.page, []).append(s)
    written: list[Path] = []
    for page_no, page_spans in sorted(by_page.items()):
        page = doc[page_no - 1]
        shape = page.new_shape()
        for i, s in enumerate(page_spans):
            color = PALETTE[i % len(PALETTE)]
            for b in s.bboxes:
                shape.draw_rect(pymupdf.Rect(b))
            shape.finish(color=color, fill=color, fill_opacity=0.16, width=0.5)
            first = pymupdf.Rect(s.bboxes[0])
            shape.insert_text((first.x0 - 22, first.y1), str(i + 1), fontsize=6, color=color)
        shape.commit()
        out = out_dir / f"page-{page_no:03d}.png"
        page.get_pixmap(dpi=dpi).save(out)
        written.append(out)
    doc.close()
    return written


if __name__ == "__main__":  # pragma: no cover
    files = spotcheck(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "spotcheck")
    print("\n".join(str(f) for f in files))
