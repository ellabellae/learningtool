import json
import re
from pathlib import Path

import pytest

from learningtool.render import ChecksMismatch, RenderInputs, render
from learningtool.schema import Checks, Lesson, Profile, Span

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixtures():
    lesson = Lesson.model_validate(json.loads((FIXTURES / "lesson.json").read_text(encoding="utf-8")))
    checks = Checks.model_validate(json.loads((FIXTURES / "checks.json").read_text(encoding="utf-8")))
    checks.lesson_sha256 = lesson.sha256()
    spans = {s["id"]: Span.model_validate(s) for s in json.loads((FIXTURES / "spans.json").read_text(encoding="utf-8"))}
    profile = Profile(name="example", default_depth="standard")
    return lesson, checks, spans, profile


@pytest.fixture
def parts():
    return load_fixtures()


@pytest.fixture
def html(parts):
    lesson, checks, spans, profile = parts
    return render(RenderInputs(lesson=lesson, checks=checks, profile=profile, spans=spans))


def test_title_scene_has_hook_and_verbatim_meta(html, parts):
    lesson = parts[0]
    assert lesson.hook_question in html
    assert lesson.paper_meta.title in html
    assert "Escolano, C." in html and "NeuroImage" in html and "2011" in html
    assert "learningtool · paper lesson" in html


def test_every_scene_rendered_with_role_and_depth(html, parts):
    lesson = parts[0]
    for s in lesson.scenes:
        assert f'data-id="{s.id}"' in html
        assert re.search(rf'data-id="{s.id}"[^>]*data-role="{s.role}"[^>]*data-depth="{s.min_depth}"', html)
    assert 'data-role="title"' in html and 'data-role="close"' in html


def test_headline_then_analogy_hierarchy(html):
    i_head = html.index("Feedback appeared only when upper-alpha power crossed a threshold")
    i_ana = html.index("Like a thermostat that only clicks on above a set point.")
    assert i_head < i_ana


def test_source_line_under_finding_opens_drawer(html):
    assert 'class="linkish open-drawer" data-claim="c-tried-1"' in html
    assert "Participants received visual feedback whenever upper alpha amplitude exceeded an individually" in html
    assert "p. 4" in html


def test_drawer_has_quote_and_crop_fallback(html):
    assert "shown exactly as extracted" in html
    assert "Page crop unavailable" in html  # no PDF passed


def test_prediction_and_reveal_markup(html):
    assert "Lock it in →" in html and "Skip prediction" in html
    assert 'data-reveal-quote' in html
    assert "Working memory performance improved significantly in the neurofeedback group" in html


def test_closing_scene(html):
    assert "What you take with you" in html
    assert "You now have" in html and "Reward threshold" in html
    assert "Escolano, C., Aguilar, M., Minguez, J. EEG-based upper alpha" in html


def test_depth_counts_and_default(html):
    assert 'data-default-depth="standard"' in html
    assert re.search(r"brief · \d+ scenes · \d+ min", html)


def test_widget_container_with_task_question_and_badge(html):
    assert 'data-template="threshold-learning-curve"' in html
    assert "What happens to learning if the reward threshold is set too high?" in html
    assert "Illustrative model, not this study" in html
    assert "The paper: “Working memory performance improved" in html


def test_status_label_is_cited_never_verified(html):
    assert ">Cited<" in html
    assert "verified" not in html.lower().replace("unverified", "")


def test_autoescape_prose(parts):
    lesson, checks, spans, profile = parts
    lesson.scenes[2].prose = 'Try <script>alert(1)</script> & see.'
    checks.lesson_sha256 = lesson.sha256()
    html = render(RenderInputs(lesson=lesson, checks=checks, profile=profile, spans=spans))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; see." in html


def test_checks_hash_mismatch_refused(parts):
    lesson, checks, spans, profile = parts
    checks.lesson_sha256 = "0" * 64
    with pytest.raises(ChecksMismatch, match="learn check"):
        render(RenderInputs(lesson=lesson, checks=checks, profile=profile, spans=spans))


def test_failed_claim_removed_and_named_in_strip(parts):
    lesson, checks, spans, profile = parts
    checks.claims[2].status = "failed"  # c-tried-1
    checks.claims[2].reason = "number 500 not found in the cited sentence"
    checks.scenes.append({"scene_id": "s-tried", "flags": ["claim_failed"]})
    checks = Checks.model_validate(checks.model_dump())
    html = render(RenderInputs(lesson=lesson, checks=checks, profile=profile, spans=spans))
    assert "Checker removed 1 claim: number 500 not found in the cited sentence. Read with care." in html
    assert 'data-claim="c-tried-1"' not in html.split("<template")[0]  # not a visible source line
    assert "Not shown" in html and "Feedback was given when upper alpha amplitude exceeded" in html
    assert re.search(r'data-id="s-tried"[^>]*class="[^"]*flagged|class="scene tried flagged"', html)


def test_widget_removed_renders_one_line(parts):
    lesson, checks, spans, profile = parts
    checks.widgets[0].ok = False
    checks.widgets[0].reason = "template shape not supported by the mechanism text"
    html = render(RenderInputs(lesson=lesson, checks=checks, profile=profile, spans=spans))
    assert "Interactive model removed: template shape not supported by the mechanism text." in html
    assert 'data-template="threshold-learning-curve"' not in html


def test_unchecked_lesson_shows_banner(parts):
    lesson, _, spans, profile = parts
    html = render(RenderInputs(lesson=lesson, checks=None, profile=profile, spans=spans))
    assert "has not been checked yet" in html


def test_audit_skipped_banner(parts):
    lesson, checks, spans, profile = parts
    html = render(RenderInputs(lesson=lesson, checks=checks, profile=profile, spans=spans, audit_skipped=True))
    assert "Meaning check skipped" in html


def test_tie_back_plain_text_without_prior_html(parts):
    lesson, checks, spans, profile = parts
    lesson.prior_links.append(
        {"scene_id": "s-tried", "concept_id": "reward-threshold", "paper_id": "f" * 64, "paper_title": "Connectome-based neurofeedback", "read_date": "2026-03-03", "note": "applied there to attention networks"}
    )
    lesson = Lesson.model_validate(lesson.model_dump())
    checks.lesson_sha256 = lesson.sha256()
    html = render(RenderInputs(lesson=lesson, checks=checks, profile=profile, spans=spans))
    assert "<em>Connectome-based neurofeedback</em>, read 2026-03-03, applied there to attention networks." in html
    assert 'href="file://' not in html


def test_html_is_self_contained_except_fonts(html):
    srcs = re.findall(r'(?:src|href)="(https?://[^"]+)"', html)
    assert all("fonts.g" in s for s in srcs), srcs
    assert "<script src=" not in html


def test_prediction_scene_never_shows_source_lines(parts):
    lesson, checks, spans, profile = parts
    lesson.scenes[4].claims = [lesson.scenes[5].claims[0].model_copy(update={"id": "c-spoiler"})]
    lesson = Lesson.model_validate(lesson.model_dump())
    checks.lesson_sha256 = lesson.sha256()
    html = render(RenderInputs(lesson=lesson, checks=None, profile=profile, spans=spans))
    visible = html.split("<template")  # source lines live outside the drawer templates
    assert not any('data-claim="c-spoiler"' in part.split("</template>")[-1] for part in visible)


def test_player_has_hashchange_resize_and_clean_verdict():
    js = (Path(__file__).resolve().parents[1] / "learningtool" / "player" / "player.js").read_text(encoding="utf-8")
    assert 'addEventListener("hashchange"' in js and 'addEventListener("resize", updateChrome)' in js
    assert "replace(/[.!?\\s]+$/" in js
