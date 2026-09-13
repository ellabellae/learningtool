"""Schema and validator tests. The fixture lesson is the contract every later
branch renders, checks, and audits against."""

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from learningtool.schema import (
    Checks,
    Lesson,
    LessonDraft,
    Profile,
    Span,
    draft_json_schema,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def lesson_dict() -> dict:
    return json.loads((FIXTURES / "lesson.json").read_text(encoding="utf-8"))


def test_fixture_lesson_validates(lesson_dict):
    lesson = Lesson.model_validate(lesson_dict)
    assert lesson.hook_question.endswith("?")
    assert lesson.paper_meta.title.startswith("EEG-based")
    assert [s.role for s in lesson.scenes] == ["prereq", "prereq", "problem", "tried", "predict", "found", "changed"]


def test_fixture_draft_is_the_model_shape(lesson_dict):
    draft_keys = {"hook_question", "scenes", "widgets", "concepts"}
    draft = LessonDraft.model_validate({k: lesson_dict[k] for k in draft_keys})
    assert len(draft.scenes) == 7


def test_lesson_sha_is_stable(lesson_dict):
    a = Lesson.model_validate(lesson_dict).sha256()
    b = Lesson.model_validate(copy.deepcopy(lesson_dict)).sha256()
    assert a == b and len(a) == 64


def test_depth_filter(lesson_dict):
    lesson = Lesson.model_validate(lesson_dict)
    assert [s.id for s in lesson.scenes_at("brief")] == ["s-problem", "s-tried", "s-predict", "s-found", "s-changed"]
    assert len(lesson.scenes_at("deep")) == 7


def test_fixture_checks_validate_and_pass():
    checks = Checks.model_validate(json.loads((FIXTURES / "checks.json").read_text(encoding="utf-8")))
    assert checks.passed()


def test_checks_fail_on_widget_only_failure():
    checks = Checks.model_validate(json.loads((FIXTURES / "checks.json").read_text(encoding="utf-8")))
    checks.widgets[0].ok = False
    assert not checks.passed()


def test_checks_fail_with_zero_findings():
    checks = Checks(paper_id="x" * 64, profile_name="p", lesson_sha256="0" * 64, claims=[])
    assert not checks.passed()


def _break(lesson_dict, mutate):
    d = copy.deepcopy(lesson_dict)
    mutate(d)
    with pytest.raises(ValidationError) as exc:
        Lesson.model_validate(d)
    return str(exc.value)


def test_duplicate_scene_id_rejected(lesson_dict):
    def m(d):
        d["scenes"][1]["id"] = d["scenes"][0]["id"]

    assert "duplicate scene id" in _break(lesson_dict, m)


def test_prereq_after_story_rejected(lesson_dict):
    def m(d):
        d["scenes"].append(dict(d["scenes"][1], id="s-late"))  # scene 1 has no claims

    assert "prereq scenes must precede" in _break(lesson_dict, m)


def test_predict_needs_two_options(lesson_dict):
    def m(d):
        d["scenes"][4]["prediction_options"] = ["only one"]
        d["scenes"][4]["prediction_answer_index"] = 0

    assert ">= 2 options" in _break(lesson_dict, m)


def test_reveal_must_be_found_scene_with_same_depth(lesson_dict):
    def m(d):
        d["scenes"][5]["min_depth"] = "deep"

    assert "share min_depth" in _break(lesson_dict, m)


def test_free_form_prediction_needs_reveal_note(lesson_dict):
    def m(d):
        d["scenes"][4]["prediction_answer_index"] = None
        d["scenes"][5]["reveal_note"] = None

    assert "needs reveal_note" in _break(lesson_dict, m)


def test_brief_must_be_a_complete_arc(lesson_dict):
    def m(d):
        d["scenes"][6]["min_depth"] = "standard"  # drop 'changed' from brief

    assert "brief depth must include" in _break(lesson_dict, m)


def test_widget_id_must_resolve(lesson_dict):
    def m(d):
        d["scenes"][3]["widget_id"] = "w-nope"

    assert "does not resolve" in _break(lesson_dict, m)


def test_unknown_field_rejected(lesson_dict):
    def m(d):
        d["scenes"][0]["status"] = "verified"

    assert "Extra inputs are not permitted" in _break(lesson_dict, m)


def test_widget_requires_evidence_and_mechanism(lesson_dict):
    def m(d):
        d["widgets"][0]["mechanism_span_ids"] = []

    assert "at least 1 item" in _break(lesson_dict, m)


def test_span_bbox_shape():
    with pytest.raises(ValidationError):
        Span(id="a", page=1, text="x", bboxes=[[1, 2, 3]])
    assert Span(id="a", page=1, text="x", bboxes=[[1, 2, 3, 4]]).page == 1


def test_profile_defaults():
    p = Profile(name="me")
    assert p.default_depth == "standard" and p.known_concepts == []


def test_draft_schema_survives_sdk_transform():
    """The Anthropic SDK rewrites a JSON schema into the subset structured output
    accepts. If our schema needed anything it drops silently, this is where we'd see it."""
    import anthropic

    original = draft_json_schema()
    transformed = anthropic.transform_schema(original)
    assert transformed["type"] == "object"
    assert set(transformed["properties"]) == {"hook_question", "scenes", "widgets", "concepts"}
    # Every object keeps additionalProperties: false after transform.
    text = json.dumps(transformed)
    assert '"additionalProperties": true' not in text
    # Enums and optional fields survive.
    scene = transformed["$defs"]["Scene"] if "$defs" in transformed else None
    assert scene is None or "role" in scene["properties"]


def test_draft_schema_has_no_recursion_or_refs_to_self():
    schema = draft_json_schema()
    text = json.dumps(schema)
    # Structured output rejects recursive schemas; a self-reference would show up as a $ref
    # to the root definition. Every $ref here must point at a distinct $defs entry.
    defs = schema.get("$defs", {})
    for name in defs:
        assert f'"$ref": "#/$defs/{name}"' not in json.dumps(defs[name]), f"{name} references itself"
    assert "additionalProperties" in text  # extra='forbid' emits additionalProperties: false
