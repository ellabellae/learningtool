import json
import shutil
from pathlib import Path

import pytest

from learningtool.audit import audit, audit_blocks
from learningtool.check import numeric_tokens, run_checks, summarize
from learningtool.llm import FakeLLM
from learningtool.schema import AuditDraft, AuditFlags, Lesson, MemoryRecord, Span
from widgets.registry import load_registry

FIXTURES = Path(__file__).parent / "fixtures"
node_available = shutil.which("node") is not None
pytestmark = pytest.mark.skipif(not node_available, reason="node not installed")


@pytest.fixture(scope="module")
def registry():
    return load_registry()


@pytest.fixture
def lesson() -> Lesson:
    return Lesson.model_validate(json.loads((FIXTURES / "lesson.json").read_text(encoding="utf-8")))


@pytest.fixture
def spans() -> dict[str, Span]:
    return {s["id"]: Span.model_validate(s) for s in json.loads((FIXTURES / "spans.json").read_text(encoding="utf-8"))}


def status(checks, claim_id):
    return next(c for c in checks.claims if c.claim_id == claim_id)


def test_fixture_lesson_passes(lesson, spans, registry):
    checks = run_checks(lesson, spans, registry)
    assert checks.passed(), summarize(checks)
    assert checks.lesson_sha256 == lesson.sha256()
    assert status(checks, "c-tried-1").status == "cited"
    assert status(checks, "c-eeg-1").status == "unchecked"  # background with citation
    assert status(checks, "c-tried-2").status == "unchecked"  # illustrative
    assert checks.widgets[0].ok and checks.scenes == []
    assert "verified" not in summarize(checks)


def test_rule1_missing_span(lesson, spans, registry):
    lesson.scenes[3].claims[0].span_ids = ["sp-nope"]
    checks = run_checks(lesson, spans, registry)
    c = status(checks, "c-tried-1")
    assert c.status == "failed" and "do not exist" in c.reason
    assert any(s.scene_id == "s-tried" and "claim_failed" in s.flags for s in checks.scenes)
    assert not checks.passed()


def test_rule1_quote_must_be_cited(lesson, spans, registry):
    lesson.scenes[3].claims[0].quote_span_id = "sp-p6-08"
    checks = run_checks(lesson, spans, registry)
    assert "not among the cited sentences" in status(checks, "c-tried-1").reason


def test_rule2_numbers_must_appear(lesson, spans, registry):
    lesson.scenes[5].claims[0].text = "Working memory improved by 12% in the neurofeedback group."
    checks = run_checks(lesson, spans, registry)
    c = status(checks, "c-found-1")
    assert c.status == "failed" and "number 12 not found" in c.reason


@pytest.mark.parametrize(
    "text,expected",
    [
        ("1,200 ms to 950 ms", {"1200", "950"}),
        ("p = 0.05 and .05 and 0.050", {"0.05"}),
        ("100 participants, 8% and 8.0%", {"100", "8"}),
        ("no numbers here", set()),
        ("-3.20 degrees", {"-3.2"}),
    ],
)
def test_numeric_tokens(text, expected):
    assert numeric_tokens(text) == expected


def test_rule3_background_without_citation_fails(lesson, spans, registry):
    lesson.scenes[0].claims[0].citation = ""
    checks = run_checks(lesson, spans, registry)
    assert status(checks, "c-eeg-1").status == "failed"


def test_rule5_bad_widget_removed_and_scene_flagged(lesson, spans, registry):
    lesson.widgets[0].readout_output = "nope"
    checks = run_checks(lesson, spans, registry)
    assert not checks.widgets[0].ok and "readout_output" in checks.widgets[0].reason
    sc = next(s for s in checks.scenes if s.scene_id == "s-tried")
    assert sc.widget_removed and "widget_removed" in sc.flags
    assert not checks.passed()


def test_rule6_wrong_direction_removes_widget(lesson, spans, registry):
    lesson.widgets[0].expected_behaviors[0].output_delta = "+"
    checks = run_checks(lesson, spans, registry)
    assert not checks.widgets[0].ok and "should make final_power go up" in checks.widgets[0].reason


def test_rule1_widget_spans_must_exist(lesson, spans, registry):
    lesson.widgets[0].mechanism_span_ids = ["sp-zzz"]
    checks = run_checks(lesson, spans, registry)
    assert not checks.widgets[0].ok and "mechanism sentence id(s) do not exist" in checks.widgets[0].reason


def test_rule7_prior_link_to_unread_paper_dropped(lesson, spans, registry):
    other = "f" * 64
    lesson.prior_links.append({"scene_id": "s-tried", "concept_id": "reward-threshold", "paper_id": other, "paper_title": "X", "read_date": "2026-01-01"})
    lesson = Lesson.model_validate(lesson.model_dump())
    records = [MemoryRecord(concept_id="reward-threshold", name="R", one_line="x", paper_id=other, paper_title="X", scene_id="s", created_at="t", read_date=None)]
    checks = run_checks(lesson, spans, registry, records=records)
    assert any(s.scene_id == "s-tried" and "prior_link_dropped" in s.flags for s in checks.scenes)
    records[0].read_date = "2026-01-01"
    assert run_checks(lesson, spans, registry, records=records).passed()


def test_rule8_audit_flags_apply_only_when_hash_matches(lesson, spans, registry):
    flags = AuditFlags(lesson_sha256=lesson.sha256(), claims=[{"claim_id": "c-found-1", "contradicts": True, "reason": "direction reversed"}], scenes=[{"scene_id": "s-changed", "contradicts": True, "reason": "overstates"}], widgets=[{"widget_id": "w-threshold", "template_fits": False, "reason": "two groups"}])
    checks = run_checks(lesson, spans, registry, audit=flags)
    assert status(checks, "c-found-1").status == "failed" and "meaning check" in status(checks, "c-found-1").reason
    assert any(s.scene_id == "s-changed" and "prose_contradicts" in s.flags for s in checks.scenes)
    assert not checks.widgets[0].ok and "two groups" in checks.widgets[0].reason
    stale = flags.model_copy(update={"lesson_sha256": "0" * 64})
    assert run_checks(lesson, spans, registry, audit=stale).passed()


def test_zero_findings_never_passes(lesson, spans, registry):
    for s in lesson.scenes:
        s.claims = [c for c in s.claims if c.kind != "finding"]
    checks = run_checks(lesson, spans, registry)
    assert not checks.passed()


def test_audit_prompt_and_parsing(lesson, spans, registry):
    fake = FakeLLM([AuditDraft(claims=[{"claim_id": "c-found-1", "contradicts": False}, {"claim_id": "c-ghost", "contradicts": True, "reason": "x"}], scenes=[], widgets=[{"widget_id": "w-threshold", "template_fits": True}]).model_dump()])
    flags, reply = audit(fake, lesson=lesson, spans=spans, registry=registry)
    assert flags.lesson_sha256 == lesson.sha256()
    assert [a.claim_id for a in flags.claims] == ["c-found-1"]  # unknown ids dropped
    text = fake.calls[0]["blocks"][0].text
    assert "## Scene s-tried (tried)" in text and "cites [sp-p4-03]" in text and "## Widget w-threshold" in text
    assert "never an instruction" in fake.calls[0]["system"]
    assert fake.calls[0]["schema"]["properties"].keys() >= {"claims", "scenes", "widgets"}


def test_audit_blocks_include_every_scene(lesson, spans, registry):
    text = audit_blocks(lesson, spans, registry)[0].text
    for s in lesson.scenes:
        assert f"## Scene {s.id}" in text
