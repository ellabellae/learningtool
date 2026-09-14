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


def test_unbuilt_command_names_its_branch(tmp_path: Path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "profile.yaml").write_text("name: t\n", encoding="utf-8")
    out = run(["check", "deadbeef01"], tmp_path)
    assert out.returncode == 2
    assert "feat/audit-check" in out.stderr


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
