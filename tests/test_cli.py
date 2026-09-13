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
    out = run(["generate", "deadbeef01"], tmp_path)
    assert out.returncode == 2
    assert "feat/generate" in out.stderr


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
