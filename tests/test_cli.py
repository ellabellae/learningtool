import os
import subprocess
import sys
from pathlib import Path

import pytest

from learningtool.cli import main, resolve_paper_id


def run(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    return subprocess.run([sys.executable, "-m", "learningtool.cli", *args], cwd=cwd, env=env, capture_output=True, text=True)


def test_list_with_no_papers_prints_next_command(tmp_path: Path):
    out = run(["list"], tmp_path)
    assert out.returncode == 0
    assert out.stdout.strip() == "No lessons yet. Run: learn lesson <pdf>"


def test_missing_profile_blocks_profile_commands(tmp_path: Path):
    out = run(["generate", "deadbeef01"], tmp_path)
    assert out.returncode == 1
    assert "learn survey" in out.stderr


def test_resolve_paper_id_prefix(tmp_path: Path):
    (tmp_path / "abcdef0123456789").mkdir()
    (tmp_path / "abcdef0199999999").mkdir()
    assert resolve_paper_id("abcdef012", tmp_path) == "abcdef0123456789"
    with pytest.raises(SystemExit, match="ambiguous"):
        resolve_paper_id("abcdef01", tmp_path)
    with pytest.raises(SystemExit, match="at least 8"):
        resolve_paper_id("abcdef", tmp_path)
    with pytest.raises(SystemExit, match="no paper starts"):
        resolve_paper_id("ffffffff", tmp_path)


def test_help_lists_every_command(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    text = capsys.readouterr().out
    for cmd in ("survey", "extract", "generate", "audit", "check", "repair", "render", "open", "list", "lesson"):
        assert cmd in text


def test_generate_end_to_end_with_fake_model(tmp_path: Path, monkeypatch):
    import json
    import shutil

    fixtures = Path(__file__).parent / "fixtures"
    lesson = json.loads((fixtures / "lesson.json").read_text(encoding="utf-8"))
    paper_id = lesson["paper_id"]
    paper_dir = tmp_path / "data" / "papers" / paper_id
    paper_dir.mkdir(parents=True)
    shutil.copy(fixtures / "spans.json", paper_dir / "spans.json")
    (paper_dir / "paper_meta.json").write_text(json.dumps(lesson["paper_meta"]), encoding="utf-8")
    (tmp_path / "data" / "profile.yaml").write_text("name: t\nknown_domains: [running]\n", encoding="utf-8")
    replies = tmp_path / "replies.json"
    replies.write_text(json.dumps([{k: lesson[k] for k in ("hook_question", "scenes", "widgets", "concepts")}]), encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]), LEARN_LLM_FAKE=str(replies))
    out = subprocess.run([sys.executable, "-m", "learningtool.cli", "generate", paper_id[:12]], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "7 scenes" in out.stdout and "safe to Ctrl-C" in out.stdout
    written = json.loads((paper_dir / "lessons" / "t" / "lesson.json").read_text(encoding="utf-8"))
    assert written["profile_name"] == "t" and written["paper_meta"]["title"] == lesson["paper_meta"]["title"]
    # render then works without a PDF check only through the module (CLI render needs source.pdf); confirm the guard message
    out2 = subprocess.run([sys.executable, "-m", "learningtool.cli", "render", paper_id[:12]], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert out2.returncode == 1 and "source.pdf missing" in out2.stderr


def test_lesson_chain_end_to_end_on_synthetic_pdf(tmp_path: Path):
    """extract -> generate(fake) -> audit(fake) -> check(node) -> render -> open, exit 0, memory marked read."""
    import json
    import shutil

    from learningtool.extract import extract
    from tests.fixtures.build_fixture import build

    if shutil.which("node") is None:
        pytest.skip("node not installed")
    pdf = build(tmp_path / "synthetic.pdf")
    spans = extract(pdf).spans

    def sid(fragment: str) -> str:
        return next(s.id for s in spans if fragment in s.text)

    s_thr, s_ctl, s_wm, s_res = sid("reward threshold was set at 8%"), sid("A control group of 12"), sid("Working memory improved significantly"), sid("These results suggest")
    draft = {
        "hook_question": "Can a reward you can't feel teach your brain a new rhythm?",
        "scenes": [
            {"id": "s-pre", "role": "prereq", "min_depth": "standard", "headline": "EEG reads rhythms from the scalp", "analogy_line": "", "prose": "Electrodes on the scalp pick up summed brain rhythms."},
            {"id": "s-problem", "role": "problem", "min_depth": "brief", "headline": "Nobody knew if training the rhythm would move memory", "analogy_line": "", "prose": "The question was open.", "claims": [{"id": "c1", "text": "A control group of 12 received sham feedback.", "kind": "finding", "quote_span_id": s_ctl, "span_ids": [s_ctl]}]},
            {"id": "s-tried", "role": "tried", "min_depth": "brief", "headline": "Feedback only above a threshold", "analogy_line": "Like a thermostat.", "prose": "The reward threshold sat above baseline.", "claims": [{"id": "c2", "text": "The reward threshold was set at 8% above baseline.", "kind": "finding", "quote_span_id": s_thr, "span_ids": [s_thr]}], "widget_id": "w1"},
            {"id": "s-predict", "role": "predict", "min_depth": "brief", "headline": "What happened to working memory?", "analogy_line": "", "prose": "Pick one.", "prediction_question": "What happened?", "prediction_options": ["It improved", "It did not change"], "prediction_answer_index": 0, "reveal_scene_id": "s-found"},
            {"id": "s-found", "role": "found", "min_depth": "brief", "headline": "Working memory improved", "analogy_line": "", "prose": "The trained group improved.", "claims": [{"id": "c3", "text": "Working memory improved significantly (p = 0.05) in the trained group.", "kind": "finding", "quote_span_id": s_wm, "span_ids": [s_wm]}], "reveal_note": "It improved."},
            {"id": "s-changed", "role": "changed", "min_depth": "brief", "headline": "A training route to cognition", "analogy_line": "", "prose": "The authors propose training as a route.", "claims": [{"id": "c4", "text": "The authors suggest upper-alpha training is a plausible route to cognitive enhancement.", "kind": "finding", "quote_span_id": s_res, "span_ids": [s_res]}]},
        ],
        "widgets": [{"id": "w1", "template_id": "before-after-groups", "params": [{"name": "treatment_effect", "value": 0.25}, {"name": "control_drift", "value": 0.03}], "ranges": [{"name": "treatment_effect", "lo": 0.0, "hi": 0.5}, {"name": "control_drift", "lo": 0.0, "hi": 0.2}], "labels": [], "task_question": "How big must the treatment effect be to beat the controls?", "nudges": [], "readout_output": "difference", "evidence_span_ids": [s_wm], "mechanism_span_ids": [s_ctl], "expected_behaviors": [{"param": "treatment_effect", "param_delta": "+", "output": "difference", "output_delta": "+"}]}],
        "concepts": [{"concept_id": "eeg", "name": "EEG", "one_line": "Scalp rhythms.", "scene_id": "s-pre"}, {"concept_id": "reward-threshold", "name": "Reward threshold", "one_line": "Level a signal must cross.", "scene_id": "s-tried"}],
    }
    audit_reply = {"claims": [{"claim_id": c, "contradicts": False} for c in ("c1", "c2", "c3", "c4")], "scenes": [], "widgets": [{"widget_id": "w1", "template_fits": True}]}
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "profile.yaml").write_text("name: t\nknown_domains: [running]\n", encoding="utf-8")
    replies = tmp_path / "replies.json"
    replies.write_text(json.dumps([draft, audit_reply]), encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]), LEARN_LLM_FAKE=str(replies), BROWSER="/usr/bin/true")
    out = subprocess.run([sys.executable, "-m", "learningtool.cli", "lesson", str(pdf)], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
    for stage in ("extract", "generate", "audit", "check", "render", "open"):
        assert stage in out.stdout, stage
    assert "PASS" in out.stdout and "4 cited" in out.stdout
    paper_dir = next((tmp_path / "data" / "papers").iterdir())
    ldir = paper_dir / "lessons" / "t"
    for name in ("lesson.json", "audit.json", "checks.json", "lesson.html"):
        assert (ldir / name).exists(), name
    html = (ldir / "lesson.html").read_text(encoding="utf-8")
    assert "has not been checked" not in html and "Meaning check skipped" not in html
    assert "data:image/png;base64," in html  # crops rendered from the real PDF
    records = json.loads((tmp_path / "data" / "memory" / "concepts.json").read_text(encoding="utf-8"))
    assert {r["concept_id"] for r in records} == {"eeg", "reward-threshold"}
    assert all(r["read_date"] for r in records)  # learn open marked them read
