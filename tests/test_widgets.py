import json
import shutil
from pathlib import Path

import pytest

from learningtool.render import RenderInputs, render
from learningtool.schema import Checks, Lesson, Profile, Span, Widget
from widgets.registry import NodeMissing, check_behaviors, evaluate, load_registry, params_used_by, validate_widget

FIXTURES = Path(__file__).parent / "fixtures"
node_available = shutil.which("node") is not None


@pytest.fixture(scope="module")
def registry():
    return load_registry()


@pytest.fixture
def fixture_widget() -> Widget:
    lesson = Lesson.model_validate(json.loads((FIXTURES / "lesson.json").read_text(encoding="utf-8")))
    return lesson.widgets[0]


def test_registry_loads_both_templates(registry):
    assert set(registry) >= {"threshold-learning-curve", "before-after-groups"}
    t = registry["threshold-learning-curve"]
    assert set(t.params) == {"threshold", "sessions_per_week"}
    assert t.outputs == ["final_power", "sessions_to_reward"]


def test_manifest_and_model_agree_on_params(registry):
    for t in registry.values():
        used = params_used_by(t.model_path)
        assert used, f"{t.id}: model.mjs reads no params"
        assert used <= set(t.params), f"{t.id}: model.mjs reads {used - set(t.params)} not declared in manifest.json"
        manifest = json.loads((t.dir / "manifest.json").read_text(encoding="utf-8"))
        assert set(manifest["defaults"]) == set(t.params)
        assert set(manifest["output_labels"]) == set(t.outputs)


def test_fixture_widget_is_valid(registry, fixture_widget):
    assert validate_widget(fixture_widget, registry) == []


def test_validate_catches_each_rule_5_failure(registry, fixture_widget):
    w = fixture_widget.model_copy(deep=True)
    w.template_id = "nope"
    assert validate_widget(w, registry) == ["unknown template 'nope'"]

    w = fixture_widget.model_copy(deep=True)
    w.ranges[0].hi = 5.0  # hard bound is 1.0
    assert any("exceeds hard bounds" in r for r in validate_widget(w, registry))

    w = fixture_widget.model_copy(deep=True)
    w.params[0].value = 0.95  # range is [0.3, 0.9]
    assert any("outside its range" in r for r in validate_widget(w, registry))

    w = fixture_widget.model_copy(deep=True)
    w.readout_output = "happiness"
    assert any("readout_output 'happiness'" in r for r in validate_widget(w, registry))

    w = fixture_widget.model_copy(deep=True)
    w.params.append({"name": "moon_phase", "value": 1})
    w = Widget.model_validate(w.model_dump())
    assert any("moon_phase" in r for r in validate_widget(w, registry))


@pytest.mark.skipif(not node_available, reason="node not installed")
def test_model_runs_in_node(registry):
    res = evaluate([("threshold-learning-curve", {"threshold": 0.55, "sessions_per_week": 3}), ("before-after-groups", {"treatment_effect": 0.25, "control_drift": 0.03})])
    assert res[0]["ok"] and 0 < res[0]["outputs"]["final_power"] < 1
    assert res[1]["ok"] and abs(res[1]["outputs"]["difference"] - 0.22) < 1e-9


@pytest.mark.skipif(not node_available, reason="node not installed")
def test_fixture_behaviors_hold(registry, fixture_widget):
    assert check_behaviors([fixture_widget], registry) == {fixture_widget.id: []}


@pytest.mark.skipif(not node_available, reason="node not installed")
def test_wrong_direction_is_reported(registry, fixture_widget):
    w = fixture_widget.model_copy(deep=True)
    w.expected_behaviors[0].output_delta = "+"  # claims raising the threshold raises final power; it doesn't
    reasons = check_behaviors([w], registry)[w.id]
    assert len(reasons) == 1 and "should make final_power go up" in reasons[0]


@pytest.mark.skipif(not node_available, reason="node not installed")
def test_model_error_is_reported_not_raised(registry):
    res = evaluate([("does-not-exist", {"x": 1})])
    assert res[0]["ok"] is False and "does-not-exist" in res[0]["error"]


def test_node_missing_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _: None)
    with pytest.raises(NodeMissing, match="Node 20"):
        evaluate([("threshold-learning-curve", {"threshold": 0.5, "sessions_per_week": 3})])


def test_templates_are_bundled_into_the_page():
    lesson = Lesson.model_validate(json.loads((FIXTURES / "lesson.json").read_text(encoding="utf-8")))
    checks = Checks.model_validate(json.loads((FIXTURES / "checks.json").read_text(encoding="utf-8")))
    checks.lesson_sha256 = lesson.sha256()
    spans = {s["id"]: Span.model_validate(s) for s in json.loads((FIXTURES / "spans.json").read_text(encoding="utf-8"))}
    html = render(RenderInputs(lesson=lesson, checks=checks, profile=Profile(name="example"), spans=spans))
    assert 'window.TEMPLATES["threshold-learning-curve"] = ((manifest) => {' in html
    assert 'window.TEMPLATES["before-after-groups"] = ((manifest) => {' in html
    assert "export function" not in html
    assert "return {model, curve, draw, manifest};" in html
