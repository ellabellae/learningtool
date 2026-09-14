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
    "audit": "feat/audit-check",
    "check": "feat/audit-check",
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


def cmd_extract(args: argparse.Namespace) -> int:
    """PDF -> data/papers/<sha256>/{source.pdf, spans.json, paper_meta.json}."""
    import json
    import shutil
    import time

    from .extract import NoTextLayer, extract, sha256_of
    from .schema import dump

    pdf = Path(args.pdf)
    if not pdf.exists():
        print(f"no such file: {pdf}", file=sys.stderr)
        return 1
    paper_id = sha256_of(pdf)
    paper_dir = PAPERS_DIR / paper_id
    spans_path = paper_dir / "spans.json"
    if spans_path.exists() and not args.force:
        print(f"extract  already done for {paper_id[:12]} (use --force to redo)")
        return 0
    t0 = time.monotonic()
    try:
        result = extract(pdf)
    except NoTextLayer as exc:
        print(f"extract  refused: {exc}", file=sys.stderr)
        return 1
    paper_dir.mkdir(parents=True, exist_ok=True)
    if not (paper_dir / "source.pdf").exists():
        shutil.copyfile(pdf, paper_dir / "source.pdf")
    spans_path.write_text(json.dumps([s.model_dump() for s in result.spans], ensure_ascii=False) + "\n", encoding="utf-8")
    (paper_dir / "paper_meta.json").write_text(dump(result.meta), encoding="utf-8")
    chars = sum(len(s.text) for s in result.spans)
    print(f"extract  {result.meta.title}")
    print(
        f"         {result.meta.pages} pages · {len(result.spans)} sentences · {chars // 1000}k chars"
        f"{' · stopped at References' if result.stopped_at_references else ''} · {time.monotonic() - t0:.1f}s"
    )
    print(f"         paper id {paper_id[:12]}")
    return 0


def cmd_spotcheck(args: argparse.Namespace) -> int:
    from .spotcheck import spotcheck

    paper_id = resolve_paper_id(args.paper_id)
    out = spotcheck(PAPERS_DIR / paper_id / "source.pdf", PAPERS_DIR / paper_id / "spotcheck")
    print("\n".join(str(p) for p in out))
    return 0


def _lesson_dir(paper_id: str, profile_name: str) -> Path:
    return PAPERS_DIR / paper_id / "lessons" / profile_name


def cmd_render(args: argparse.Namespace) -> int:
    import json

    from .render import ChecksMismatch, RenderInputs, render
    from .schema import Checks, Lesson, Span

    profile = load_profile(args.profile)
    paper_id = resolve_paper_id(args.paper_id)
    paper_dir = PAPERS_DIR / paper_id
    ldir = _lesson_dir(paper_id, profile.name)
    lesson_path, checks_path, audit_path = ldir / "lesson.json", ldir / "checks.json", ldir / "audit.json"
    if not lesson_path.exists():
        print(f"no lesson for profile {profile.name!r}; run: learn generate {paper_id[:12]}", file=sys.stderr)
        return 1
    if not (paper_dir / "source.pdf").exists():
        print(f"source.pdf missing for {paper_id[:12]}; re-run: learn extract <pdf>", file=sys.stderr)
        return 1
    lesson = Lesson.model_validate(json.loads(lesson_path.read_text(encoding="utf-8")))
    checks = Checks.model_validate(json.loads(checks_path.read_text(encoding="utf-8"))) if checks_path.exists() else None
    spans = {s["id"]: Span.model_validate(s) for s in json.loads((paper_dir / "spans.json").read_text(encoding="utf-8"))}
    prior = {p.parent.parent.parent.name: p for p in PAPERS_DIR.glob(f"*/lessons/{profile.name}/lesson.html")}
    try:
        html = render(
            RenderInputs(
                lesson=lesson, checks=checks, profile=profile, spans=spans, pdf=paper_dir / "source.pdf",
                audit_skipped=not audit_path.exists(), prior_lesson_paths=prior,
            )
        )
    except ChecksMismatch as exc:
        print(f"render   refused: {exc}", file=sys.stderr)
        return 1
    out = ldir / "lesson.html"
    out.write_text(html, encoding="utf-8")
    print(f"render   {out} ({len(html) // 1024} KB)")
    return 0


def cmd_open(args: argparse.Namespace) -> int:
    import webbrowser

    profile = load_profile(args.profile)
    paper_id = resolve_paper_id(args.paper_id)
    out = _lesson_dir(paper_id, profile.name) / "lesson.html"
    if not out.exists():
        print(f"no rendered lesson; run: learn render {paper_id[:12]}", file=sys.stderr)
        return 1
    try:
        from .memory import mark_read

        mark_read(paper_id)
    except ImportError:
        pass  # feat/memory
    webbrowser.open(out.resolve().as_uri())
    print(f"open     {out}")
    return 0


def _load_paper(paper_id: str):
    import json

    from .schema import PaperMeta, Span

    paper_dir = PAPERS_DIR / paper_id
    spans = [Span.model_validate(s) for s in json.loads((paper_dir / "spans.json").read_text(encoding="utf-8"))]
    meta = PaperMeta.model_validate(json.loads((paper_dir / "paper_meta.json").read_text(encoding="utf-8")))
    return paper_dir, spans, meta


def cmd_generate(args: argparse.Namespace) -> int:
    from . import memory
    from .generate import generate, usage_line, write_json
    from .llm import LLMError, get_llm, ticker
    from widgets.registry import load_registry

    profile = load_profile(args.profile)
    paper_id = resolve_paper_id(args.paper_id)
    paper_dir, spans, meta = _load_paper(paper_id)
    print(f"generate {meta.title}")
    print("         safe to Ctrl-C; nothing is written until the reply parses")
    try:
        res = generate(get_llm(), spans=spans, profile=profile, paper_id=paper_id, paper_meta=meta, registry=load_registry(), records=memory.load_records(), progress=ticker("generate"))
    except LLMError as exc:
        print(f"\ngenerate failed: {exc}", file=sys.stderr)
        return 1
    print("\r" + " " * 60 + "\r", end="", file=sys.stderr)
    out = _lesson_dir(paper_id, profile.name) / "lesson.json"
    write_json(out, res.lesson)
    n_find = sum(1 for s in res.lesson.scenes for c in s.claims if c.kind == "finding")
    print(f"         {len(res.lesson.scenes)} scenes · {n_find} findings · {len(res.lesson.widgets)} widget(s) · {usage_line(res.reply)}")
    print(f"         wrote {out}")
    return 0


def cmd_repair(args: argparse.Namespace) -> int:
    import json

    from .generate import usage_line, write_json
    from .llm import LLMError, get_llm, ticker
    from .repair import repair
    from .schema import Checks, Failure, Lesson
    from widgets.registry import load_registry

    profile = load_profile(args.profile)
    paper_id = resolve_paper_id(args.paper_id)
    paper_dir, spans, _ = _load_paper(paper_id)
    ldir = _lesson_dir(paper_id, profile.name)
    lesson_path, checks_path = ldir / "lesson.json", ldir / "checks.json"
    if not lesson_path.exists() or not checks_path.exists():
        print("repair needs lesson.json and checks.json; run: learn generate then learn check", file=sys.stderr)
        return 1
    lesson = Lesson.model_validate(json.loads(lesson_path.read_text(encoding="utf-8")))
    checks = Checks.model_validate(json.loads(checks_path.read_text(encoding="utf-8")))
    failures = [Failure(kind="claim", id=c.claim_id, reason=c.reason or "failed") for c in checks.claims if c.status == "failed"]
    failures += [Failure(kind="widget", id=w.widget_id, reason=w.reason or "failed") for w in checks.widgets if not w.ok]
    failures += [Failure(kind="scene", id=s.scene_id, reason=", ".join(s.flags)) for s in checks.scenes if s.flags and not (s.widget_removed and len(s.flags) == 1)]
    if not failures:
        print("repair   nothing to repair")
        return 0
    print(f"repair   {len(failures)} failure(s)")
    try:
        merged, reply = repair(get_llm(), lesson=lesson, failures=failures, spans=spans, registry=load_registry(), progress=ticker("repair"))
    except LLMError as exc:
        print(f"\nrepair failed: {exc}", file=sys.stderr)
        return 1
    print("\r" + " " * 60 + "\r", end="", file=sys.stderr)
    write_json(lesson_path, merged)
    print(f"         merged · {usage_line(reply)} · re-run: learn check {paper_id[:12]}")
    return 0


def cmd_lesson(args: argparse.Namespace) -> int:
    """extract -> generate -> [audit] -> [check] -> [repair] -> render -> open. Steps not yet built are skipped and named."""
    ns = argparse.Namespace
    rc = cmd_extract(ns(pdf=args.pdf, force=False))
    if rc != 0:
        return rc
    from .extract import sha256_of

    paper_id = sha256_of(Path(args.pdf))
    rc = cmd_generate(ns(paper_id=paper_id, profile=args.profile, failures=None))
    if rc != 0:
        return rc
    skipped = [name for name in ("audit", "check") if name in NOT_YET]
    if skipped:
        print(f"skipped  {', '.join(skipped)} (not built yet: {', '.join(NOT_YET[n] for n in skipped)}); the lesson renders as unchecked")
    rc = cmd_render(ns(paper_id=paper_id, profile=args.profile))
    if rc != 0:
        return rc
    return cmd_open(ns(paper_id=paper_id, profile=args.profile))


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

    p = sub.add_parser("extract", help="PDF -> spans.json"); p.add_argument("pdf"); p.add_argument("--force", action="store_true"); p.set_defaults(func=cmd_extract)
    p = sub.add_parser("spotcheck", help="draw every span's boxes on its page (PNG per page)"); p.add_argument("paper_id"); p.set_defaults(func=cmd_spotcheck)
    built = {"render": cmd_render, "open": cmd_open, "generate": cmd_generate, "repair": cmd_repair}
    for name in ("generate", "audit", "check", "repair", "render", "open"):
        p = with_profile(sub.add_parser(name)); p.add_argument("paper_id")
        if name == "generate":
            p.add_argument("--failures", help="checks.json from a failed check (repair input)")
        p.set_defaults(func=built.get(name, cmd_not_yet))
    p = with_profile(sub.add_parser("lesson", help="extract -> generate -> audit -> check -> render -> open")); p.add_argument("pdf"); p.set_defaults(func=cmd_lesson)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command not in ("survey", "list", "extract", "spotcheck") and hasattr(args, "profile"):
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
