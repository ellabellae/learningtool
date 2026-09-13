"""The `learn` command. Every subcommand from the design doc is registered here;
the ones not yet built exit 2 with a message naming the branch that builds them.

    learn survey   [--profile P]
    learn extract  <pdf>
    learn generate <id>  [--profile P] [--failures checks.json]
    learn audit    <id>  [--profile P]
    learn check    <id>  [--profile P]
    learn repair   <id>  [--profile P]
    learn render   <id>  [--profile P]
    learn open     <id>  [--profile P]
    learn list
    learn lesson   <pdf> [--profile P]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .profile import DEFAULT_PROFILE_PATH, ProfileError, load_profile, save_profile
from .schema import Profile

DATA_DIR = Path("data")
PAPERS_DIR = DATA_DIR / "papers"

NOT_YET = {
    "extract": "feat/extract",
    "generate": "feat/generate",
    "audit": "feat/audit-check",
    "check": "feat/audit-check",
    "repair": "feat/generate",
    "render": "feat/player",
    "open": "feat/player",
    "lesson": "feat/generate",
}


def resolve_paper_id(prefix: str, papers_dir: Path = PAPERS_DIR) -> str:
    """Accept any unique prefix of 8+ characters of a paper's sha256."""
    if len(prefix) < 8:
        raise SystemExit(f"paper id must be at least 8 characters, got {prefix!r}")
    if not papers_dir.exists():
        raise SystemExit(f"no papers yet. Run: learn lesson <pdf>")
    matches = sorted(p.name for p in papers_dir.iterdir() if p.is_dir() and p.name.startswith(prefix))
    if not matches:
        raise SystemExit(f"no paper starts with {prefix!r}")
    if len(matches) > 1:
        raise SystemExit(f"{prefix!r} is ambiguous: {', '.join(m[:12] for m in matches)}")
    return matches[0]


def cmd_survey(args: argparse.Namespace) -> int:
    """A short questionnaire that writes the profile. Learning signals, not styles."""
    path = Path(args.profile)
    print("learningtool survey. Answers are plain text; edit the file later any time.\n")
    name = _ask("A short name for this profile (letters, digits, - or _)", "me")
    domains = _ask_list(
        "Domains you already know well, for analogies (comma-separated)\n"
        "  e.g. biomedical engineering, running, cooking, personal finance"
    )
    depth = _ask("Default depth: brief, standard, or deep", "standard")
    while depth not in ("brief", "standard", "deep"):
        depth = _ask("Please type brief, standard, or deep", "standard")
    skip = _ask_list("Topics to skip or keep short (comma-separated, blank for none)")
    known = _ask_list(
        "Concepts you already understand, so lessons skip their rungs (comma-separated)\n"
        "  e.g. what an LLM is, p-values, EEG frequency bands"
    )
    profile = Profile(
        name=name, known_domains=domains, default_depth=depth, skip_topics=skip, known_concepts=known
    )
    save_profile(profile, path)
    print(f"\nwrote {path}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    papers = sorted(p for p in PAPERS_DIR.glob("*") if p.is_dir()) if PAPERS_DIR.exists() else []
    if not papers:
        print("No lessons yet. Run: learn lesson <pdf>")
        return 0
    print(f"{'paper':14} {'profile':12} {'status':10} title")
    for paper in papers:
        title = "(not extracted)"
        meta = paper / "paper_meta.json"
        if meta.exists():
            import json

            title = json.loads(meta.read_text(encoding="utf-8")).get("title", title)
        lessons = sorted(p for p in (paper / "lessons").glob("*") if p.is_dir()) if (paper / "lessons").exists() else []
        if not lessons:
            print(f"{paper.name[:12]:14} {'-':12} {'no lesson':10} {title[:60]}")
        for lesson in lessons:
            status = "rendered" if (lesson / "lesson.html").exists() else "generated" if (lesson / "lesson.json").exists() else "empty"
            print(f"{paper.name[:12]:14} {lesson.name:12} {status:10} {title[:60]}")
    return 0


def cmd_not_yet(args: argparse.Namespace) -> int:
    print(f"`learn {args.command}` is not built yet; it arrives in {NOT_YET[args.command]}.", file=sys.stderr)
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="learn", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def with_profile(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
        p.add_argument("--profile", default=str(DEFAULT_PROFILE_PATH), help="profile file (default data/profile.yaml)")
        return p

    with_profile(sub.add_parser("survey", help="write a profile")).set_defaults(func=cmd_survey)
    sub.add_parser("list", help="papers, profiles, and unread lessons").set_defaults(func=cmd_list)

    p = sub.add_parser("extract", help="PDF -> spans.json"); p.add_argument("pdf"); p.add_argument("--force", action="store_true"); p.set_defaults(func=cmd_not_yet)
    for name in ("generate", "audit", "check", "repair", "render", "open"):
        p = with_profile(sub.add_parser(name)); p.add_argument("paper_id")
        if name == "generate":
            p.add_argument("--failures", help="checks.json from a failed check (repair input)")
        p.set_defaults(func=cmd_not_yet)
    p = with_profile(sub.add_parser("lesson", help="extract -> generate -> audit -> check -> render -> open")); p.add_argument("pdf"); p.set_defaults(func=cmd_not_yet)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command not in ("survey", "list", "extract") and hasattr(args, "profile"):
        try:
            load_profile(args.profile)
        except ProfileError as exc:
            print(str(exc), file=sys.stderr)
            return 1
    return args.func(args)


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    answer = input(f"{prompt}{suffix}: ").strip()
    return answer or default


def _ask_list(prompt: str) -> list[str]:
    raw = input(f"{prompt}: ").strip()
    return [item.strip() for item in raw.split(",") if item.strip()]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
