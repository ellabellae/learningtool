"""lesson.json + checks.json + profile (+ spans, optional PDF) -> one self-contained lesson.html.

    Lesson ─┐
    Checks ─┼─ apply removals + flags ─▶ view model ─▶ Jinja (autoescape) ─▶ HTML
    Profile─┤                              ▲               ▲
    spans ──┘  quote text by span id ──────┘               │
    source.pdf  base64 crops (skipped when absent) ────────┘
    widgets/templates/*/{manifest.json, model.mjs}  inlined as TEMPLATES[id]

Statuses come only from checks.json (stamped with the lesson's sha256; a
mismatch is refused). The page is rendered server-side: every scene is a
<section>; player.js only navigates, filters by depth, records predictions,
opens the evidence drawer, and mounts widgets.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .schema import DEPTH_ORDER, Checks, Depth, Lesson, Profile, Span

PLAYER_DIR = Path(__file__).parent / "player"
TEMPLATES_DIR = Path(__file__).parent.parent / "widgets" / "templates"

ROLE_LABELS = {
    "prereq": "Before",
    "before": "The world before",
    "problem": "The problem",
    "tried": "What they tried",
    "predict": "Your prediction",
    "found": "What they found",
    "changed": "What changed",
    "close": "Close",
}
KIND_CHIPS = {"finding": "Cited", "background": "Background · unchecked", "illustrative": "Analogy"}
WORDS_PER_MIN = 200
WIDGET_SECONDS = 60
PREDICT_SECONDS = 45
HEADER_TITLE_CHARS = 60
SOURCE_LINE_CHARS = 90


class ChecksMismatch(Exception):
    """checks.json was computed for a different lesson.json. Re-run: learn check <id>."""


@dataclass
class RenderInputs:
    lesson: Lesson
    checks: Checks | None
    profile: Profile
    spans: dict[str, Span]
    pdf: Path | None = None
    audit_skipped: bool = False
    prior_lesson_paths: dict[str, Path] | None = None  # paper_id -> lesson.html for tie-back links


def render(inputs: RenderInputs) -> str:
    lesson = inputs.lesson
    if inputs.checks is not None and inputs.checks.lesson_sha256 != lesson.sha256():
        raise ChecksMismatch("checks.json does not match lesson.json; re-run: learn check <id>")
    view = build_view(inputs)
    env = Environment(loader=FileSystemLoader(str(PLAYER_DIR)), autoescape=select_autoescape(["html", "j2"]))
    template = env.get_template("lesson.html.j2")
    return template.render(**view)


# --------------------------------------------------------------------------- #
# View model
# --------------------------------------------------------------------------- #


def build_view(inputs: RenderInputs) -> dict:
    lesson, checks, profile, spans = inputs.lesson, inputs.checks, inputs.profile, inputs.spans
    claim_status = {c.claim_id: (c.status, c.reason) for c in (checks.claims if checks else [])}
    widget_ok = {w.widget_id: (w.ok, w.reason) for w in (checks.widgets if checks else [])}
    scene_checks = {s.scene_id: s for s in (checks.scenes if checks else [])}
    widgets_by_id = {w.id: w for w in lesson.widgets}
    predict_for_reveal = {s.reveal_scene_id: s for s in lesson.scenes if s.role == "predict" and s.reveal_scene_id}
    links_by_scene: dict[str, list] = {}
    for link in lesson.prior_links:
        links_by_scene.setdefault(link.scene_id, []).append(link)
    concept_by_scene = {c.scene_id: c for c in lesson.concepts}
    crops = _crops(inputs, lesson, spans)

    scenes = []
    for s in lesson.scenes:
        sc = scene_checks.get(s.id)
        visible_claims, not_shown = [], []
        for c in s.claims:
            status, reason = claim_status.get(c.id, ("unchecked" if c.kind != "finding" else "unverified", None))
            if status == "failed":
                not_shown.append({"text": c.text, "reason": reason or "rejected by the checker"})
                continue
            quote_id = c.quote_span_id or (c.span_ids[0] if c.span_ids else None)
            quote = spans.get(quote_id) if quote_id else None
            visible_claims.append(
                {
                    "id": c.id,
                    "text": c.text,
                    "kind": c.kind,
                    "chip": KIND_CHIPS[c.kind] if status != "unverified" else "Unchecked",
                    "status": status,
                    "citation": c.citation,
                    "quote": quote.text if quote else None,
                    "page": quote.page if quote else None,
                    "source_line": _source_line(quote.text) if quote else None,
                    "crop": crops.get(quote_id) if quote_id else None,
                    "span_id": quote_id,
                }
            )
        flags = []
        if sc:
            if not_shown:
                n = len(not_shown)
                flags.append(f"Checker removed {n} claim{'s' if n > 1 else ''}: " + "; ".join(x["reason"] for x in not_shown) + ". Read with care.")
            for f in sc.flags:
                if f == "prose_contradicts":
                    flags.append("The meaning check disagrees with the paper here. Read with care.")
                elif f == "widget_removed":
                    pass  # shown in the widget's place
                elif f == "reveal_nulled":
                    flags.append("The prediction's reveal could not be linked; the paper's finding is shown without a verdict.")
                else:
                    flags.append(f"Checker flag: {f}. Read with care.")
        elif not_shown:
            flags.append(f"Checker removed {len(not_shown)} claim(s). Read with care.")

        widget = None
        widget_removed_reason = None
        if s.widget_id:
            ok, reason = widget_ok.get(s.widget_id, (True, None))
            removed = (sc.widget_removed if sc else False) or not ok
            if removed:
                widget_removed_reason = reason or "removed by the checker"
            else:
                w = widgets_by_id[s.widget_id]
                evidence = [spans[i].text for i in w.evidence_span_ids if i in spans]
                widget = {
                    "id": w.id,
                    "template_id": w.template_id,
                    "task_question": w.task_question,
                    "readout_output": w.readout_output,
                    "evidence": evidence[0] if evidence else None,
                    "evidence_span_id": w.evidence_span_ids[0] if w.evidence_span_ids else None,
                    "evidence_page": spans[w.evidence_span_ids[0]].page if w.evidence_span_ids and w.evidence_span_ids[0] in spans else None,
                    "config": json.dumps(
                        {
                            "params": {p.name: p.value for p in w.params},
                            "ranges": {r.name: [r.lo, r.hi] for r in w.ranges},
                            "labels": {l.name: l.text for l in w.labels},
                            "nudges": {n.name: n.text for n in w.nudges},
                            "readout": w.readout_output,
                        }
                    ),
                }

        predict = None
        if s.role == "predict":
            predict = {"question": s.prediction_question, "options": s.prediction_options, "answer_index": s.prediction_answer_index, "reveal_id": s.reveal_scene_id}
        reveal = None
        if s.role == "found" and s.id in predict_for_reveal:
            p = predict_for_reveal[s.id]
            finding_claim = next((c for c in visible_claims if c["kind"] == "finding" and c["quote"]), None)
            reveal = {
                "claim_id": finding_claim["id"] if finding_claim else None,
                "predict_id": p.id,
                "options": p.prediction_options,
                "answer_index": p.prediction_answer_index,
                "quote": finding_claim["quote"] if finding_claim else None,
                "page": finding_claim["page"] if finding_claim else None,
                "note": s.reveal_note,
                "nulled": bool(sc and sc.reveal_nulled),
            }

        label = ROLE_LABELS[s.role]
        if s.role == "prereq":
            c = concept_by_scene.get(s.id)
            label = c.name if c else _first_words(s.headline, 3)

        scenes.append(
            {
                "id": s.id,
                "role": s.role,
                "kicker": ROLE_LABELS[s.role] if s.role != "prereq" else "Before the paper",
                "arc_label": label,
                "min_depth": s.min_depth,
                "headline": s.headline,
                "analogy_line": s.analogy_line,
                "prose": s.prose,
                "claims": visible_claims,
                "not_shown": not_shown,
                "flags": flags,
                "flagged": bool(flags) or widget_removed_reason is not None,
                "widget": widget,
                "widget_removed_reason": widget_removed_reason,
                "predict": predict,
                "reveal": reveal,
                "tie_backs": [
                    {
                        "concept": l.concept_id,
                        "paper_title": l.paper_title,
                        "read_date": l.read_date,
                        "note": l.note,
                        "href": _tie_back_href(inputs, l.paper_id),
                    }
                    for l in links_by_scene.get(s.id, [])
                ],
                "seconds": _scene_seconds(s.prose + " " + s.analogy_line, bool(s.widget_id), s.role == "predict"),
            }
        )

    depth_counts = {d: _depth_summary(scenes, d) for d in ("brief", "standard", "deep")}
    finding_for_close = next((c for sc_ in scenes if sc_["role"] == "found" for c in sc_["claims"] if c["kind"] == "finding" and c["quote"]), None)
    meta = lesson.paper_meta
    return {
        "lesson": lesson,
        "meta": meta,
        "citation": _citation(meta),
        "header_title": _truncate(meta.title, HEADER_TITLE_CHARS),
        "hook": lesson.hook_question,
        "scenes": scenes,
        "concepts": lesson.concepts,
        "depth_counts": depth_counts,
        "default_depth": profile.default_depth,
        "profile_name": profile.name,
        "paper_id": lesson.paper_id,
        "audit_skipped": inputs.audit_skipped,
        "checks_present": checks is not None,
        "close_finding": finding_for_close,
        "templates_js": _templates_bundle(),
        "css": (PLAYER_DIR / "player.css").read_text(encoding="utf-8"),
        "js": (PLAYER_DIR / "player.js").read_text(encoding="utf-8"),
    }


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _crops(inputs: RenderInputs, lesson: Lesson, spans: dict[str, Span]) -> dict[str, str]:
    if inputs.pdf is None or not Path(inputs.pdf).exists():
        return {}
    from .extract import render_crop

    wanted: set[str] = set()
    for s in lesson.scenes:
        for c in s.claims:
            sid = c.quote_span_id or (c.span_ids[0] if c.span_ids else None)
            if sid:
                wanted.add(sid)
    out: dict[str, str] = {}
    for sid in sorted(wanted):
        if sid in spans:
            out[sid] = "data:image/png;base64," + base64.b64encode(render_crop(inputs.pdf, spans[sid])).decode("ascii")
    return out


def _templates_bundle() -> str:
    """Wrap every widget template so two never collide: TEMPLATES[id] = (manifest => {model, curve, draw})(manifest)."""
    parts = ["window.TEMPLATES = window.TEMPLATES || {};"]
    if not TEMPLATES_DIR.exists():
        return "\n".join(parts)
    for d in sorted(p for p in TEMPLATES_DIR.iterdir() if p.is_dir()):
        manifest, model = d / "manifest.json", d / "model.mjs"
        if not (manifest.exists() and model.exists()):
            continue
        src = re.sub(r"^\s*export\s+(?=(?:function|const|let|var)\b)", "", model.read_text(encoding="utf-8"), flags=re.M)
        src = re.sub(r"^\s*export\s*\{[^}]*\};?\s*$", "", src, flags=re.M)
        parts.append(
            f"window.TEMPLATES[{json.dumps(d.name)}] = ((manifest) => {{\n{src}\nreturn {{model, curve, draw, manifest}};\n}})({manifest.read_text(encoding='utf-8')});"
        )
    return "\n".join(parts)


def _scene_seconds(text: str, has_widget: bool, is_predict: bool) -> int:
    words = len(text.split())
    return int(words / WORDS_PER_MIN * 60) + (WIDGET_SECONDS if has_widget else 0) + (PREDICT_SECONDS if is_predict else 0)


def _depth_summary(scenes: list[dict], depth: Depth) -> dict:
    limit = DEPTH_ORDER[depth]
    visible = [s for s in scenes if DEPTH_ORDER[s["min_depth"]] <= limit]
    seconds = sum(s["seconds"] for s in visible)
    return {"scenes": len(visible) + 2, "minutes": max(1, round(seconds / 60))}  # +2: title and close


def _source_line(text: str) -> str:
    return _truncate(text, SOURCE_LINE_CHARS)


def _truncate(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _first_words(text: str, n: int) -> str:
    return " ".join(text.split()[:n])


def _citation(meta) -> str:
    parts = [", ".join(meta.authors) if meta.authors else None, meta.title, meta.venue, str(meta.year) if meta.year else None]
    return ". ".join(p.rstrip(".") for p in parts if p) + "."


def _tie_back_href(inputs: RenderInputs, paper_id: str) -> str | None:
    paths = inputs.prior_lesson_paths or {}
    p = paths.get(paper_id)
    return p.resolve().as_uri() if p and Path(p).exists() else None
