import json
from pathlib import Path

import pytest

from learningtool import memory
from learningtool.generate import MAX_SPAN_CHARS, TooLong, generate, usage_line
from learningtool.llm import Block, FakeLLM, LLMError, Refused, Reply, Truncated, get_llm, parse_json
from learningtool.repair import merge, repair
from learningtool.schema import Failure, Lesson, MemoryRecord, PaperMeta, Profile, RepairReply, Span
from widgets.registry import load_registry

FIXTURES = Path(__file__).parent / "fixtures"
DRAFT_KEYS = ("hook_question", "scenes", "widgets", "concepts")


def load_fixture_lesson() -> dict:
    return json.loads((FIXTURES / "lesson.json").read_text(encoding="utf-8"))


def draft_reply() -> dict:
    d = load_fixture_lesson()
    return {k: d[k] for k in DRAFT_KEYS}


def spans() -> list[Span]:
    return [Span.model_validate(s) for s in json.loads((FIXTURES / "spans.json").read_text(encoding="utf-8"))]


def meta() -> PaperMeta:
    return PaperMeta.model_validate(load_fixture_lesson()["paper_meta"])


PAPER_ID = "3f9a1c7e2b8d4a6f5e0c9b1d2a3f4e5c6d7b8a9f0e1d2c3b4a5f6e7d8c9b0a1f"


@pytest.fixture
def registry():
    return load_registry()


def test_generate_post_fills_and_validates(registry):
    llm = FakeLLM([draft_reply()])
    res = generate(llm, spans=spans(), profile=Profile(name="ella", known_domains=["BME"]), paper_id=PAPER_ID, paper_meta=meta(), registry=registry, records=[])
    lesson = res.lesson
    assert lesson.paper_id == PAPER_ID and lesson.profile_name == "ella"
    assert lesson.paper_meta.title.startswith("EEG-based")
    assert lesson.prior_links == []
    assert len(lesson.scenes) == 7


def test_prompt_layout_stable_prefix_first_and_cached(registry):
    llm = FakeLLM([draft_reply()])
    generate(llm, spans=spans(), profile=Profile(name="ella", known_domains=["running"], known_concepts=["EEG"]), paper_id=PAPER_ID, paper_meta=meta(), registry=registry, records=[])
    call = llm.calls[0]
    assert "Honesty rules" in call["system"]
    b0, b1 = call["blocks"]
    assert b0.cache and not b1.cache
    assert "[sp-p4-03] (p. 4) Participants received visual feedback" in b0.text
    assert "threshold-learning-curve" in b0.text and "before-after-groups" in b0.text
    assert "running" in b1.text and "EEG" in b1.text
    assert call["schema"]["type"] == "object" and "hook_question" in call["schema"]["properties"]


def test_memory_slugs_in_prompt_and_prior_links_only_for_read_papers(registry):
    other = "f" * 64
    records = [
        MemoryRecord(concept_id="reward-threshold", name="Reward threshold", one_line="x", paper_id=other, paper_title="Connectome-based neurofeedback", scene_id="s1", created_at="2026-03-01T00:00:00+00:00", read_date="2026-03-03"),
        MemoryRecord(concept_id="eeg", name="EEG", one_line="x", paper_id=other, paper_title="Connectome-based neurofeedback", scene_id="s2", created_at="2026-03-01T00:00:00+00:00", read_date=None),
    ]
    llm = FakeLLM([draft_reply()])
    res = generate(llm, spans=spans(), profile=Profile(name="ella"), paper_id=PAPER_ID, paper_meta=meta(), registry=registry, records=records)
    assert "- reward-threshold: Reward threshold" in llm.calls[0]["blocks"][0].text
    links = res.lesson.prior_links
    assert [l.concept_id for l in links] == ["reward-threshold"]  # 'eeg' was never opened
    assert links[0].scene_id == "s-tried" and links[0].read_date == "2026-03-03"


def test_too_long_paper_refused(registry):
    big = [Span(id=f"s{i}", page=1, text="x" * 1000, bboxes=[[0, 0, 1, 1]]) for i in range(MAX_SPAN_CHARS // 1000 + 2)]
    with pytest.raises(TooLong, match="chunking arrives in v1.1"):
        generate(FakeLLM([]), spans=big, profile=Profile(name="e"), paper_id=PAPER_ID, paper_meta=meta(), registry=registry, records=[])


def test_truncation_and_refusal_are_clear_messages():
    with pytest.raises(Truncated, match="cut off at 64000 output tokens"):
        parse_json(Reply(text="{", stop_reason="max_tokens", output_tokens=64000), "the lesson")
    with pytest.raises(Refused, match="declined to write the lesson \\(category: cyber\\)"):
        parse_json(Reply(text="", stop_reason="refusal", stop_details={"category": "cyber"}), "the lesson")
    with pytest.raises(LLMError, match="not valid JSON"):
        parse_json(Reply(text="{nope", stop_reason="end_turn"), "the lesson")


def test_invalid_draft_reports_fields(registry):
    bad = draft_reply()
    bad["scenes"][4]["prediction_options"] = ["only one"]
    with pytest.raises(LLMError, match="failed validation") as exc:
        generate(FakeLLM([bad]), spans=spans(), profile=Profile(name="e"), paper_id=PAPER_ID, paper_meta=meta(), registry=registry, records=[])
    assert ">= 2 options" in str(exc.value)


def test_repair_merge_leaves_untouched_items_byte_identical(registry):
    lesson = Lesson.model_validate(load_fixture_lesson())
    before = lesson.model_dump_json()
    new_claim = lesson.scenes[3].claims[0].model_copy(update={"text": "Feedback was tied to an individual upper-alpha threshold."})
    fake = FakeLLM([RepairReply(claims=[new_claim]).model_dump()])
    merged, _ = repair(fake, lesson=lesson, failures=[Failure(kind="claim", id=new_claim.id, reason="number missing")], spans=spans(), registry=registry)
    assert merged.scenes[3].claims[0].text == "Feedback was tied to an individual upper-alpha threshold."
    # everything else identical
    a, b = json.loads(before), json.loads(merged.model_dump_json())
    a["scenes"][3]["claims"][0]["text"] = b["scenes"][3]["claims"][0]["text"]
    assert a == b
    assert "# Failures to fix" in fake.calls[0]["blocks"][2].text and "number missing" in fake.calls[0]["blocks"][2].text


def test_repair_replaces_whole_scene_and_ignores_unknown_ids():
    lesson = Lesson.model_validate(load_fixture_lesson())
    scene = lesson.scenes[6].model_copy(update={"prose": "Rewritten takeaways."})
    stray = lesson.scenes[6].model_copy(update={"id": "s-nope"})
    merged = merge(lesson, RepairReply(scenes=[scene, stray]))
    assert merged.scenes[6].prose == "Rewritten takeaways."
    assert [s.id for s in merged.scenes] == [s.id for s in lesson.scenes]


def test_repair_with_no_failures_is_a_noop(registry):
    lesson = Lesson.model_validate(load_fixture_lesson())
    fake = FakeLLM([])
    merged, reply = repair(fake, lesson=lesson, failures=[], spans=spans(), registry=registry)
    assert merged == lesson and reply.model == "none" and fake.calls == []


def test_get_llm_fake_from_env(tmp_path, monkeypatch):
    p = tmp_path / "replies.json"
    p.write_text(json.dumps([draft_reply()]), encoding="utf-8")
    monkeypatch.setenv("LEARN_LLM_FAKE", str(p))
    llm = get_llm()
    reply = llm.structured("s", [Block(text="x")], {})
    assert reply.stop_reason == "end_turn" and json.loads(reply.text)["hook_question"]


def test_memory_upsert_preserves_dates():
    lesson = Lesson.model_validate(load_fixture_lesson())
    first = memory.upsert_paper([], PAPER_ID, "T", lesson.concepts)
    first[0].read_date = "2026-01-01"
    created = first[0].created_at
    second = memory.upsert_paper(first, PAPER_ID, "T", lesson.concepts[:2])  # one concept dropped
    assert [r.concept_id for r in second] == ["eeg", "neurofeedback"]
    assert second[0].read_date == "2026-01-01" and second[0].created_at == created


def test_mark_read(tmp_path):
    lesson = Lesson.model_validate(load_fixture_lesson())
    path = tmp_path / "concepts.json"
    memory.save_records(memory.upsert_paper([], PAPER_ID, "T", lesson.concepts), path)
    assert memory.mark_read(PAPER_ID, path, when="2026-09-13") == 3
    assert memory.mark_read(PAPER_ID, path, when="2026-09-14") == 0
    assert all(r.read_date == "2026-09-13" for r in memory.load_records(path))


def test_usage_line_format():
    r = Reply(text="", stop_reason="end_turn", model="claude-opus-5", input_tokens=20000, output_tokens=6000, cache_read_tokens=18000, elapsed=134)
    assert usage_line(r) == "claude-opus-5 · 20000 in / 6000 out · cache 18000 read / 0 written · 2:14"
