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

NOT_YET: dict[str, str] = {}  # every command is built; kept so a future subcommand can name its branch


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


def cmd_share(args: argparse.Namespace) -> int:
    """Render public versions (no page images, no local links) plus an index page into --out."""
    import html as htmllib
    import json

    from .render import ChecksMismatch, RenderInputs, detect_source_url, render, slugify
    from .schema import Checks, Lesson

    profile = load_profile(args.profile)
    if args.source_url and len(args.paper_ids) != 1:
        print("--source-url applies to exactly one paper id", file=sys.stderr)
        return 1
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for raw_id in args.paper_ids:
        paper_id = resolve_paper_id(raw_id)
        _, spans, meta = _load_paper(paper_id)
        ldir = _lesson_dir(paper_id, profile.name)
        lesson_path, checks_path = ldir / "lesson.json", ldir / "checks.json"
        if not lesson_path.exists():
            print(f"no lesson for {paper_id[:12]} under profile {profile.name!r}; run: learn lesson <pdf>", file=sys.stderr)
            return 1
        lesson = Lesson.model_validate(json.loads(lesson_path.read_text(encoding="utf-8")))
        checks = Checks.model_validate(json.loads(checks_path.read_text(encoding="utf-8"))) if checks_path.exists() else None
        if checks is None or not checks.passed():
            print(f"share    refusing {paper_id[:12]}: only lessons that pass the checker are shared (run: learn check {paper_id[:12]})", file=sys.stderr)
            return 1
        # A link given once is remembered beside the paper, so later shares (of many papers) keep it.
        url_file = PAPERS_DIR / paper_id / "source_url.txt"
        if args.source_url:
            url_file.write_text(args.source_url.strip() + "\n", encoding="utf-8")
        saved = url_file.read_text(encoding="utf-8").strip() if url_file.exists() else None
        source_url = saved or detect_source_url(meta, spans)
        try:
            page = render(
                RenderInputs(
                    lesson=lesson, checks=checks, profile=profile, spans={s.id: s for s in spans}, pdf=None,
                    audit_skipped=not (ldir / "audit.json").exists(), share=True, source_url=source_url,
                )
            )
        except ChecksMismatch as exc:
            print(f"share    refused: {exc}", file=sys.stderr)
            return 1
        name = slugify(meta.title) + ".html"
        (out_dir / name).write_text(page, encoding="utf-8")
        entries.append({"file": name, "title": meta.title, "hook": lesson.hook_question, "authors": ", ".join(meta.authors), "year": meta.year})
        print(f"share    {out_dir / name} ({len(page) // 1024} KB){' · ' + source_url if source_url else ' · no source link (pass --source-url)'}")
    items = "\n".join(
        f'<li><a href="{htmllib.escape(e["file"])}"><span class="hook">{htmllib.escape(e["hook"])}</span>'
        f'<span class="meta">{htmllib.escape(e["title"])}{" · " + htmllib.escape(e["authors"]) if e["authors"] else ""}{" · " + str(e["year"]) if e["year"] else ""}</span></a></li>'
        for e in entries
    )
    index = _INDEX_HTML.replace("{{ITEMS}}", items)
    (out_dir / "index.html").write_text(index, encoding="utf-8")
    print(f"share    {out_dir / 'index.html'} ({len(entries)} lesson{'s' if len(entries) != 1 else ''})")
    return 0


_INDEX_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>learningtool · paper lessons</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif&family=Inter:wght@400;500&display=swap" rel="stylesheet">
<style>
:root{--paper:#F7F3EC;--ink:#1F1D1A;--slate:#5B5F66;--terracotta:#B5502F;--hairline:#D9D2C5}
body{margin:0;background:var(--paper);color:var(--ink);font-family:Inter,-apple-system,"Segoe UI",Helvetica,Arial,sans-serif;font-size:18px;line-height:1.55}
main{max-width:68ch;margin:0 auto;padding:72px 32px 96px}
.kick{font-size:16px;color:var(--slate);margin:0 0 8px}
h1{font-family:"Instrument Serif",Georgia,serif;font-weight:400;font-size:40px;line-height:1.2;margin:0 0 16px}
p{margin:0 0 24px}.muted{color:var(--slate);font-size:16px}
ul{list-style:none;padding:0;margin:40px 0}
li{border-top:1px solid var(--hairline)}li:last-child{border-bottom:1px solid var(--hairline)}
li a{display:block;padding:24px 0;color:inherit;text-decoration:none}
li a:hover .hook,li a:focus-visible .hook{color:var(--terracotta)}
.hook{display:block;font-family:"Instrument Serif",Georgia,serif;font-size:28px;line-height:1.2;margin-bottom:6px}
.meta{display:block;font-size:16px;color:var(--slate)}
a{color:var(--ink);text-underline-offset:3px;text-decoration-color:var(--hairline)}
:focus-visible{outline:2px solid var(--terracotta);outline-offset:2px}
</style></head><body><main>
<p class="kick">learningtool</p>
<h1>Research papers, told as lessons you can play through</h1>
<p>Each lesson starts with the concepts a paper assumes, then tells the paper as a story: the problem, what they tried, your prediction, what they found, and what changed. Every finding points at the paper's own sentence, and sliders let you poke at the mechanism.</p>
<ul>
{{ITEMS}}
</ul>
<p class="muted">Unofficial lessons, not affiliated with or endorsed by the papers' authors or publishers. Page images are omitted here because the source papers are copyrighted; the full tool shows the highlighted page beside every quote. Code: <a href="https://github.com/ellabellae/learningtool">github.com/ellabellae/learningtool</a></p>
</main></body></html>
"""


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


def cmd_audit(args: argparse.Namespace) -> int:
    import json

    from .audit import audit, summarize
    from .generate import usage_line, write_json
    from .llm import LLMError, get_llm, ticker
    from .schema import Lesson
    from widgets.registry import load_registry

    profile = load_profile(args.profile)
    paper_id = resolve_paper_id(args.paper_id)
    _, spans, _ = _load_paper(paper_id)
    ldir = _lesson_dir(paper_id, profile.name)
    lesson_path = ldir / "lesson.json"
    if not lesson_path.exists():
        print(f"no lesson for profile {profile.name!r}; run: learn generate {paper_id[:12]}", file=sys.stderr)
        return 1
    lesson = Lesson.model_validate(json.loads(lesson_path.read_text(encoding="utf-8")))
    try:
        flags, reply = audit(get_llm(), lesson=lesson, spans={s.id: s for s in spans}, registry=load_registry(), progress=ticker("audit"))
    except LLMError as exc:
        print(f"\naudit failed: {exc}", file=sys.stderr)
        return 1
    print("\r" + " " * 60 + "\r", end="", file=sys.stderr)
    write_json(ldir / "audit.json", flags)
    print(f"audit    {summarize(flags)} · {usage_line(reply)}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    import json

    from . import memory
    from .check import run_checks, summarize
    from .generate import write_json
    from .schema import AuditFlags, Lesson
    from widgets.registry import NodeMissing, load_registry

    profile = load_profile(args.profile)
    paper_id = resolve_paper_id(args.paper_id)
    _, spans, meta = _load_paper(paper_id)
    ldir = _lesson_dir(paper_id, profile.name)
    lesson_path, audit_path = ldir / "lesson.json", ldir / "audit.json"
    if not lesson_path.exists():
        print(f"no lesson for profile {profile.name!r}; run: learn generate {paper_id[:12]}", file=sys.stderr)
        return 1
    lesson = Lesson.model_validate(json.loads(lesson_path.read_text(encoding="utf-8")))
    flags = AuditFlags.model_validate(json.loads(audit_path.read_text(encoding="utf-8"))) if audit_path.exists() else None
    if flags is not None and flags.lesson_sha256 != lesson.sha256():
        print("check    audit.json is for an older lesson; ignoring it (re-run: learn audit)")
        flags = None
    records = memory.load_records()
    try:
        checks = run_checks(lesson, {s.id: s for s in spans}, load_registry(), audit=flags, records=records)
    except NodeMissing as exc:
        print(f"check failed: {exc}", file=sys.stderr)
        return 1
    write_json(ldir / "checks.json", checks)
    passed = checks.passed()
    print(f"check    {summarize(checks)} · {'PASS' if passed else 'FAIL'}")
    if passed:
        memory.save_records(memory.upsert_paper(records, paper_id, meta.title, lesson.concepts))
        print(f"         memory: {len(lesson.concepts)} concept record(s) for this paper")
    return 0 if passed else 1


def cmd_lesson(args: argparse.Namespace) -> int:
    """extract -> generate -> audit -> check -> (repair -> audit -> check, once) -> render -> open.

    Render and open always run; the exit code is the last check's. An audit that
    fails to run is skipped with a warning, not fatal."""
    ns = argparse.Namespace
    rc = cmd_extract(ns(pdf=args.pdf, force=False))
    if rc != 0:
        return rc
    from .extract import sha256_of

    paper_id = sha256_of(Path(args.pdf))
    rc = cmd_generate(ns(paper_id=paper_id, profile=args.profile, failures=None))
    if rc != 0:
        return rc
    if cmd_audit(ns(paper_id=paper_id, profile=args.profile)) != 0:
        print("audit    skipped; the lesson will say so")
    check_rc = cmd_check(ns(paper_id=paper_id, profile=args.profile))
    if check_rc != 0:
        if cmd_repair(ns(paper_id=paper_id, profile=args.profile)) == 0:
            if cmd_audit(ns(paper_id=paper_id, profile=args.profile)) != 0:
                print("audit    skipped after repair")
            check_rc = cmd_check(ns(paper_id=paper_id, profile=args.profile))
        if check_rc != 0:
            print("check    still failing after one repair; rendering with flags")
    rc = cmd_render(ns(paper_id=paper_id, profile=args.profile))
    if rc != 0:
        return rc
    cmd_open(ns(paper_id=paper_id, profile=args.profile))
    return check_rc


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
    p = with_profile(sub.add_parser("share", help="render public versions (no page images) plus an index page"))
    p.add_argument("paper_ids", nargs="+"); p.add_argument("--out", default="site"); p.add_argument("--source-url", help="link to the original paper (single id only)")
    p.set_defaults(func=cmd_share)
    built = {"render": cmd_render, "open": cmd_open, "generate": cmd_generate, "repair": cmd_repair, "audit": cmd_audit, "check": cmd_check}
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
