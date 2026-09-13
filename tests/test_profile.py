from pathlib import Path

import pytest

from learningtool.profile import ProfileError, load_profile, save_profile
from learningtool.schema import Profile


def test_missing_profile_names_the_command(tmp_path: Path):
    with pytest.raises(ProfileError) as exc:
        load_profile(tmp_path / "profile.yaml")
    assert "learn survey" in str(exc.value)


def test_round_trip(tmp_path: Path):
    p = Profile(name="ella", known_domains=["BME", "running"], default_depth="deep", known_concepts=["EEG"])
    path = save_profile(p, tmp_path / "profile.yaml")
    assert load_profile(path) == p


def test_bad_field_names_the_field_and_value(tmp_path: Path):
    path = tmp_path / "profile.yaml"
    path.write_text("name: ella\ndefault_depth: verydeep\nknown_domains: notalist\n", encoding="utf-8")
    with pytest.raises(ProfileError) as exc:
        load_profile(path)
    msg = str(exc.value)
    assert "default_depth" in msg and "'verydeep'" in msg
    assert "known_domains" in msg and "'notalist'" in msg
    assert "Traceback" not in msg


def test_unknown_field_is_reported(tmp_path: Path):
    path = tmp_path / "profile.yaml"
    path.write_text("name: ella\nlearning_style: visual\n", encoding="utf-8")
    with pytest.raises(ProfileError) as exc:
        load_profile(path)
    assert "learning_style" in str(exc.value)


def test_not_a_mapping(tmp_path: Path):
    path = tmp_path / "profile.yaml"
    path.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(ProfileError) as exc:
        load_profile(path)
    assert "mapping" in str(exc.value)
