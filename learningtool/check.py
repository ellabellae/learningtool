"""Rules 1-8: deterministic checks over lesson.json. Writes statuses to Checks, never to the lesson.

    1  finding claims point at real spans (quote id among them); widgets' evidence and mechanism spans exist
    2  every number in a finding's text appears in its cited sentences
    3  background claims carry a citation -> unchecked (empty -> failed)
    4  illustrative claims -> unchecked
    5  widget template, params, ranges, readout, behavior names valid (registry)         -> widget removed
    6  expected behaviors hold strictly when model() runs at each range end (one node call) -> widget removed
    7  reveal_scene_id / widget_id / prior_links / concept scene ids resolve               -> nulled + flagged
    8  audit flags for the same lesson hash: claim contradicts -> failed; scene -> flagged; widget -> removed

Statuses: cited (structurally checked; pointing and numbers, not meaning), failed, unchecked.
"""

from __future__ import annotations

import re
from pathlib import Path

from .memory import MemoryRecord
from .schema import AuditFlags, Checks, ClaimCheck, Lesson, SceneCheck, Span, WidgetCheck
from widgets.registry import TEMPLATES_DIR, NodeMissing, check_behaviors, validate_widget

NUMBER_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?|-?\.\d+")


def numeric_tokens(text: str) -> set[str]:
    out = set()
    for tok in NUMBER_RE.findall(text):
        t = tok.replace(",", "")
        if t.startswith("."):
            t = "0" + t
        if t.startswith("-."):
            t = "-0" + t[1:]
        if "." in t:
            t = t.rstrip("0").rstrip(".")
        out.add(t)
    return out


def run_checks(
    lesson: Lesson,
    spans: dict[str, Span],
    registry: dict,
    audit: AuditFlags | None = None,
    records: list[MemoryRecord] | None = None,
    templates_dir: Path = TEMPLATES_DIR,
) -> Checks:
    sha = lesson.sha256()
    if audit is not None and audit.lesson_sha256 != sha:
        audit = None  # stale audit is no audit
    audit_claims = {a.claim_id: a for a in (audit.claims if audit else [])}
    audit_scenes = {a.scene_id: a for a in (audit.scenes if audit else [])}
    audit_widgets = {a.widget_id: a for a in (audit.widgets if audit else [])}
    records = records or []
    read_records = {(r.paper_id, r.concept_id) for r in records if r.read_date}

    claims: list[ClaimCheck] = []
    scene_checks: dict[str, SceneCheck] = {s.id: SceneCheck(scene_id=s.id) for s in lesson.scenes}
    widget_reasons: dict[str, list[str]] = {w.id: [] for w in lesson.widgets}

    # Rules 1-4 (+8 for claims)
    for s in lesson.scenes:
        for c in s.claims:
            status, reason = "unchecked", None
            if c.kind == "finding":
                missing = [i for i in c.span_ids if i not in spans]
                if not c.span_ids:
                    status, reason = "failed", "finding cites no sentence"
                elif missing:
                    status, reason = "failed", f"cited sentence id(s) do not exist: {', '.join(missing)}"
                elif c.quote_span_id and c.quote_span_id not in c.span_ids:
                    status, reason = "failed", f"quote sentence {c.quote_span_id} is not among the cited sentences"
                else:
                    cited_text = " ".join(spans[i].text for i in c.span_ids)
                    absent = sorted(numeric_tokens(c.text) - numeric_tokens(cited_text))
                    if absent:
                        status, reason = "failed", f"number{'s' if len(absent) > 1 else ''} {', '.join(absent)} not found in the cited sentence{'s' if len(c.span_ids) > 1 else ''}"
                    else:
                        status = "cited"
                a = audit_claims.get(c.id)
                if status == "cited" and a and a.contradicts:
                    status, reason = "failed", f"meaning check: {a.reason or 'claim does not follow from the cited sentences'}"
            elif c.kind == "background":
                if not (c.citation or "").strip():
                    status, reason = "failed", "background claim has no citation"
            claims.append(ClaimCheck(claim_id=c.id, status=status, reason=reason))
            if status == "failed":
                sc = scene_checks[s.id]
                if "claim_failed" not in sc.flags:
                    sc.flags.append("claim_failed")

    # Rules 1 (widget spans), 5, 6, 8 (widgets)
    widgets_by_id = {w.id: w for w in lesson.widgets}
    for w in lesson.widgets:
        for label, ids in (("evidence", w.evidence_span_ids), ("mechanism", w.mechanism_span_ids)):
            missing = [i for i in ids if i not in spans]
            if missing:
                widget_reasons[w.id].append(f"{label} sentence id(s) do not exist: {', '.join(missing)}")
        widget_reasons[w.id] += validate_widget(w, registry)
        a = audit_widgets.get(w.id)
        if a and not a.template_fits:
            widget_reasons[w.id].append(f"meaning check: {a.reason or 'the mechanism text does not support this template shape'}")
    try:
        behaviors = check_behaviors([w for w in lesson.widgets if not widget_reasons[w.id]], registry, templates_dir)
    except NodeMissing as exc:
        raise
    for wid, reasons in behaviors.items():
        widget_reasons[wid] += reasons
    widget_checks = [WidgetCheck(widget_id=w.id, ok=not widget_reasons[w.id], reason="; ".join(widget_reasons[w.id]) or None) for w in lesson.widgets]
    bad_widgets = {w.id for w in lesson.widgets if widget_reasons[w.id]}

    # Rule 7 (+ widget removal flags, + rule 8 scenes)
    scene_ids = {s.id for s in lesson.scenes}
    for s in lesson.scenes:
        sc = scene_checks[s.id]
        if s.widget_id:
            if s.widget_id not in widgets_by_id:
                sc.widget_removed = True
                sc.flags.append("widget_missing")
            elif s.widget_id in bad_widgets:
                sc.widget_removed = True
                sc.flags.append("widget_removed")
        if s.role == "predict" and (s.reveal_scene_id not in scene_ids):
            sc.reveal_nulled = True
            sc.flags.append("reveal_nulled")
        a = audit_scenes.get(s.id)
        if a and a.contradicts:
            sc.flags.append("prose_contradicts")
    for link in lesson.prior_links:
        if (link.paper_id, link.concept_id) not in read_records:
            sc = scene_checks.get(link.scene_id)
            if sc is not None and "prior_link_dropped" not in sc.flags:
                sc.flags.append("prior_link_dropped")
    for c in lesson.concepts:
        if c.scene_id not in scene_ids:
            # validators prevent this at parse time; recorded defensively
            scene_checks.setdefault(c.scene_id, SceneCheck(scene_id=c.scene_id)).flags.append("concept_scene_missing")

    return Checks(
        paper_id=lesson.paper_id,
        profile_name=lesson.profile_name,
        lesson_sha256=sha,
        claims=claims,
        widgets=widget_checks,
        scenes=[sc for sc in scene_checks.values() if sc.flags or sc.widget_removed or sc.reveal_nulled],
    )


def summarize(checks: Checks) -> str:
    cited = sum(1 for c in checks.claims if c.status == "cited")
    failed = sum(1 for c in checks.claims if c.status == "failed")
    unchecked = sum(1 for c in checks.claims if c.status == "unchecked")
    w_ok = sum(1 for w in checks.widgets if w.ok)
    parts = [f"{cited} cited", f"{failed} failed", f"{unchecked} unchecked", f"{w_ok}/{len(checks.widgets)} widget{'s' if len(checks.widgets) != 1 else ''} ok"]
    if checks.scenes:
        parts.append(f"{len(checks.scenes)} scene{'s' if len(checks.scenes) != 1 else ''} flagged")
    return " · ".join(parts)
