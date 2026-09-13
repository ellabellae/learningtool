import json
from pathlib import Path

import pymupdf
import pytest

from learningtool.extract import NoTextLayer, _norm_key, extract, render_crop, split_sentences
from learningtool.schema import Span
from tests.fixtures.build_fixture import CAPTION, EXPECTED_BODY_SENTENCES, HAS_LIGATURE_FONT, TITLE, build


@pytest.fixture(scope="module")
def synthetic_pdf(tmp_path_factory) -> Path:
    return build(tmp_path_factory.mktemp("fixture") / "synthetic.pdf")


@pytest.fixture(scope="module")
def extraction(synthetic_pdf):
    return extract(synthetic_pdf)


def test_body_sentences_in_reading_order(extraction):
    body = [s.text for s in extraction.spans if s.page >= 2]
    assert body == EXPECTED_BODY_SENTENCES


def test_header_footer_and_references_excluded(extraction):
    texts = " ".join(s.text for s in extraction.spans)
    assert "Journal of Synthetic Neuroscience" not in texts.replace(TITLE, "")
    assert "page 2" not in texts
    assert "Klimesch" not in texts and "fabricated citation" not in texts
    assert extraction.stopped_at_references


def test_hyphenation_folded(extraction):
    texts = [s.text for s in extraction.spans]
    assert any("participants" in t for t in texts)
    assert not any("partici-" in t for t in texts)


@pytest.mark.skipif(not HAS_LIGATURE_FONT, reason="no TrueType font with ligature glyphs on this machine")
def test_ligatures_folded(extraction):
    texts = [s.text for s in extraction.spans]
    assert not any("ﬁ" in t for t in texts)
    assert any("efficient fine-tuning" in t for t in texts)


def test_caption_kept_and_label_not_split(extraction):
    assert CAPTION in [s.text for s in extraction.spans]


def test_boilerplate_and_title_not_spans(extraction):
    texts = [s.text for s in extraction.spans]
    assert not any("©" in t or "All rights reserved" in t or t.startswith("978-") for t in texts)
    assert TITLE not in texts  # the title lives in PaperMeta, not as a citable sentence


def test_every_span_has_line_boxes_on_its_page(extraction, synthetic_pdf):
    doc = pymupdf.open(synthetic_pdf)
    for s in extraction.spans:
        assert s.bboxes, s.text
        page = doc[s.page - 1]
        for b in s.bboxes:
            r = pymupdf.Rect(b)
            assert r.is_valid and not r.is_empty and page.rect.contains(r), (s.text, b)
            # Every word inside the box (shrunk so neighbouring lines don't bleed in)
            # must belong to the sentence, comparing with hyphens and ligatures removed.
            inner = pymupdf.Rect(r.x0 + 1, r.y0 + 2, r.x1 - 1, r.y1 - 2)
            got = page.get_textbox(inner).split()
            assert got, (s.text, b)
            key = _norm_key(s.text)
            assert all(_norm_key(w) in key for w in got), (s.text, got)


def test_wrapped_sentence_gets_multiple_lines(extraction):
    long = next(s for s in extraction.spans if s.text.startswith("Feedback appeared only when"))
    assert len(long.bboxes) >= 2
    ys = [b[1] for b in long.bboxes]
    assert ys == sorted(ys)


def test_hyphenated_sentence_boxes_span_both_lines(extraction):
    s = next(x for x in extraction.spans if x.text.startswith("Alpha rhythm training"))
    assert len(s.bboxes) >= 2


def test_columns_do_not_interleave(extraction):
    texts = [s.text for s in extraction.spans if s.page == 2]
    left_last = texts.index("A control group of 12 received sham feedback that was not contingent on their EEG.")
    right_first = texts.index("Working memory improved significantly (p = 0.05) in the trained group.")
    assert left_last < right_first


def test_paper_meta_is_verbatim(extraction):
    m = extraction.meta
    assert m.title == TITLE
    assert m.authors == ["A. Example", "B. Sample", "C. Fixture"]
    assert m.venue == "Journal of Synthetic Neuroscience"
    assert m.year == 2026 and m.pages == 3


def test_paper_id_is_sha256(extraction, synthetic_pdf):
    import hashlib

    assert extraction.paper_id == hashlib.sha256(synthetic_pdf.read_bytes()).hexdigest()


def test_span_ids_unique_and_ordered(extraction):
    ids = [s.id for s in extraction.spans]
    assert len(ids) == len(set(ids))
    assert ids == sorted(ids)


def test_spans_serialize_to_schema(extraction):
    payload = [s.model_dump() for s in extraction.spans]
    assert all(Span.model_validate(p) for p in payload)
    json.dumps(payload)


def test_render_crop_is_png(extraction, synthetic_pdf):
    s = extraction.spans[-1]
    png = render_crop(synthetic_pdf, s)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 1000


def test_no_text_layer_is_refused(tmp_path):
    doc = pymupdf.open()
    for _ in range(3):
        page = doc.new_page()
        page.draw_rect(pymupdf.Rect(50, 50, 300, 300), color=(0, 0, 0), fill=(0.9, 0.9, 0.9))
    path = tmp_path / "scan.pdf"
    doc.save(path)
    with pytest.raises(NoTextLayer, match="OCR is unsupported"):
        extract(path)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("One. Two.", ["One.", "Two."]),
        ("See Fig. 1 for details. Then more.", ["See Fig. 1 for details.", "Then more."]),
        ("Smith et al. showed this. Next sentence.", ["Smith et al. showed this.", "Next sentence."]),
        ("A value of 0.05 was used. Done.", ["A value of 0.05 was used.", "Done."]),
        ("Is it? Yes! Fine.", ["Is it?", "Yes!", "Fine."]),
        ("Ends with a quote.\" Next one.", ["Ends with a quote.\"", "Next one."]),
        ("Fig. 1. Upper-alpha power per session.", ["Fig. 1. Upper-alpha power per session."]),
        ("Latency was 500 ms. A control group followed.", ["Latency was 500 ms.", "A control group followed."]),
        ("We thank Dr. Smith. Next.", ["We thank Dr. Smith.", "Next."]),
    ],
)
def test_split_sentences(text, expected):
    assert split_sentences(text) == expected
