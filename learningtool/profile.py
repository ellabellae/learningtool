"""Load and validate data/profile.yaml with messages that name the field."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from .schema import Profile

DEFAULT_PROFILE_PATH = Path("data/profile.yaml")


class ProfileError(Exception):
    """A user-facing problem with the profile file. The message is the fix."""


def load_profile(path: Path | str = DEFAULT_PROFILE_PATH) -> Profile:
    path = Path(path)
    if not path.exists():
        raise ProfileError(f"no profile at {path}. Run: learn survey")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ProfileError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProfileError(f"{path} must be a mapping of fields, got {type(raw).__name__}")
    try:
        return Profile.model_validate(raw)
    except ValidationError as exc:
        lines = []
        for err in exc.errors():
            field = ".".join(str(p) for p in err["loc"]) or "(root)"
            got = err.get("input")
            lines.append(f"  {field}: {err['msg']} (got {got!r})")
        raise ProfileError(f"{path} has {len(lines)} problem(s):\n" + "\n".join(lines)) from exc


def save_profile(profile: Profile, path: Path | str = DEFAULT_PROFILE_PATH) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(profile.model_dump(mode="json"), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path
