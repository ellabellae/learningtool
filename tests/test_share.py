"""Share mode: a public lesson carries no page images, no local paths, and says why."""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from learningtool.render import RenderInputs, detect_source_url, render, slugify
from learningtool.schema import Checks, Lesson, PaperMeta, Profile, Span

FIXTURES = Path(__file__).parent / "fixtures"


def parts():
    lesson = Lesson.model_validate(json.loads((FIXTURES / "lesson.json").read_text(encoding="utf-8")))
    checks = Checks.model_validate(json.loads((FIXTURES / "checks.json").read_text(encoding="utf-8")))
    checks.lesson_sha256 = lesson.sha256()
    spans = {s["id"]: Span.model_validate(s) for s in json.loads((FIXTURES / "spans.json").read_text(encoding="utf-8"))}
    return lesson, checks, spans


def test_share_mode_has_no_images_or_local_paths_and_explains_itself(tmp_path):
    lesson, checks, spans = parts()
    lesson.prior_links.append({"scene_id": "s-tried", "concept_id": "reward-threshold", "paper_id": "f" * 64, "paper_title": "Other paper", "read_date": "2026-03-03"})
    lesson = Lesson.model_validate(lesson.model_dump())
    checks.lesson_sha256 = lesson.sha256()
    prior = tmp_path / "lesson.html"
    prior.write_text("x", encoding="utf-8")
    html = render(RenderInputs(lesson=lesson, checks=checks, profile=Profile(name="example"), spans=spans, pdf=tmp_path / "missing.pdf", prior_lesson_paths={"f" * 64: prior}, share=True, source_url="https://doi.org/10.1000/xyz"))
    assert "data:image/png;base64" not in html
    assert "file://" not in html and "/Users/" not in html and str(tmp_path) not in html
    assert "Page images are omitted in this shared version because the source paper is copyrighted" in html
    assert "the full tool shows the highlighted page beside every quote" in html
    assert "Not affiliated with or endorsed by the authors or publisher" in html
    assert html.count('href="https://doi.org/10.1000/xyz"') >= 2
    assert "<em>Other paper</em>" in html  # tie-back stays as plain text
    # quotes and page numbers survive
    assert "Working memory performance improved significantly in the neurofeedback group" in html and "p. 6" in html


def test_private_render_is_unchanged_by_the_share_flag_default():
    lesson, checks, spans = parts()
    html = render(RenderInputs(lesson=lesson, checks=checks, profile=Profile(name="example"), spans=spans))
    assert "shared version" not in html and "Not affiliated" not in html


def test_detect_source_url_from_metadata_and_never_guesses():
    meta = PaperMeta(title="T", venue="Nature Reviews Drug Discovery, doi:10.1038/s41573-026-01496-2", pages=3)
    assert detect_source_url(meta, []) == "https://doi.org/10.1038/s41573-026-01496-2"
    spans = [Span(id="a", page=1, text="Available at https://doi.org/10.1109/IEMBS.2011.6090651.", bboxes=[[0, 0, 1, 1]])]
    assert detect_source_url(PaperMeta(title="T", pages=1), spans) == "https://doi.org/10.1109/IEMBS.2011.6090651"
    assert detect_source_url(PaperMeta(title="Heart Rate Accuracy Study 2026", venue="Apple", pages=9), []) is None


def test_slugify():
    slug = slugify("Artificial intelligence in drug discovery — what it is, where we stand and the path forward")
    assert slug.startswith("artificial-intelligence-in-drug-discovery-what-it-is") and len(slug) <= 60
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", slug)
    assert slugify("!!!") == "lesson"


def _setup_paper(tmp_path: Path, passing: bool = True) -> str:
    lesson = json.loads((FIXTURES / "lesson.json").read_text(encoding="utf-8"))
    lesson["profile_name"] = "t"
    paper_id = lesson["paper_id"]
    paper_dir = tmp_path / "data" / "papers" / paper_id
    ldir = paper_dir / "lessons" / "t"
    ldir.mkdir(parents=True)
    shutil.copy(FIXTURES / "spans.json", paper_dir / "spans.json")
    (paper_dir / "paper_meta.json").write_text(json.dumps(lesson["paper_meta"]), encoding="utf-8")
    (ldir / "lesson.json").write_text(json.dumps(lesson), encoding="utf-8")
    checks = json.loads((FIXTURES / "checks.json").read_text(encoding="utf-8"))
    checks["profile_name"] = "t"
    checks["lesson_sha256"] = Lesson.model_validate(lesson).sha256()
    if not passing:
        checks["claims"][1]["status"] = "failed"
    (ldir / "checks.json").write_text(json.dumps(checks), encoding="utf-8")
    (tmp_path / "data" / "profile.yaml").write_text("name: t\n", encoding="utf-8")
    return paper_id


def _run(args, cwd):
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    return subprocess.run([sys.executable, "-m", "learningtool.cli", *args], cwd=cwd, env=env, capture_output=True, text=True)


def test_cli_share_writes_lesson_and_index(tmp_path):
    paper_id = _setup_paper(tmp_path)
    out = _run(["share", paper_id[:12], "--out", "site", "--source-url", "https://example.org/paper"], tmp_path)
    assert out.returncode == 0, out.stderr
    files = sorted(p.name for p in (tmp_path / "site").iterdir())
    assert len(files) == 2 and files[1] == "index.html" and files[0].startswith("eeg-based-upper-alpha-neurofeedback-training")
    index = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert "Can you train your brain to make more of one wave" in index and 'href="eeg-based-upper-alpha' in index
    page = (tmp_path / "site" / files[0]).read_text(encoding="utf-8")
    assert "data:image" not in page and "https://example.org/paper" in page
    assert not re.search(r"/Users/|/private/|file://", page + index)


def test_cli_share_refuses_a_lesson_that_did_not_pass(tmp_path):
    paper_id = _setup_paper(tmp_path, passing=False)
    out = _run(["share", paper_id[:12]], tmp_path)
    assert out.returncode == 1 and "only lessons that pass the checker are shared" in out.stderr


def test_source_url_is_remembered_for_later_shares(tmp_path):
    paper_id = _setup_paper(tmp_path)
    assert _run(["share", paper_id[:12], "--source-url", "https://example.org/original.pdf"], tmp_path).returncode == 0
    saved = tmp_path / "data" / "papers" / paper_id / "source_url.txt"
    assert saved.read_text(encoding="utf-8").strip() == "https://example.org/original.pdf"
    out = _run(["share", paper_id[:12], "--out", "site2"], tmp_path)  # no flag this time
    assert out.returncode == 0 and "https://example.org/original.pdf" in out.stdout
    page = next(p for p in (tmp_path / "site2").iterdir() if p.name != "index.html").read_text(encoding="utf-8")
    assert page.count('href="https://example.org/original.pdf"') >= 2
