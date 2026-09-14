"""Flag-only semantic audit: does each claim follow from its cited sentences, does any scene's
prose contradict them, and does each widget's template shape fit the mechanism text?

The audit can raise flags; it can never clear a structural failure. Its output is
stamped with the lesson hash so a stale audit is ignored by the checker.
"""

from __future__ import annotations

from pathlib import Path

import anthropic
from pydantic import ValidationError

from .generate import PROMPTS
from .llm import LLM, Block, LLMError, Reply, parse_json
from .schema import AuditDraft, AuditFlags, Lesson, Span


def audit_blocks(lesson: Lesson, spans: dict[str, Span], registry: dict) -> list[Block]:
    lines = ["# Items to audit", ""]
    for s in lesson.scenes:
        cited_ids = sorted({i for c in s.claims for i in c.span_ids if i in spans})
        lines.append(f"## Scene {s.id} ({s.role})")
        lines.append(f"headline: {s.headline}")
        lines.append(f"prose: {s.prose}")
        if cited_ids:
            lines.append("sentences this scene cites:")
            lines += [f"  [{i}] {spans[i].text}" for i in cited_ids]
        else:
            lines.append("sentences this scene cites: none")
        for c in s.claims:
            if c.kind != "finding":
                continue
            lines.append(f"- claim {c.id}: {c.text}")
            lines += [f"    cites [{i}] {spans[i].text}" for i in c.span_ids if i in spans]
        lines.append("")
    for w in lesson.widgets:
        t = registry.get(w.template_id)
        lines.append(f"## Widget {w.id}")
        lines.append(f"template: {w.template_id} — {t.description if t else 'unknown template'}")
        lines.append(f"task question: {w.task_question}")
        lines.append("mechanism sentences:")
        lines += [f"  [{i}] {spans[i].text}" for i in w.mechanism_span_ids if i in spans]
        lines.append("")
    return [Block(text="\n".join(lines))]


def audit(llm: LLM, *, lesson: Lesson, spans: dict[str, Span], registry: dict, progress=None) -> tuple[AuditFlags, Reply]:
    system = (PROMPTS / "audit.md").read_text(encoding="utf-8")
    schema = anthropic.transform_schema(AuditDraft.model_json_schema())
    reply = llm.structured(system, audit_blocks(lesson, spans, registry), schema, progress=progress)
    data = parse_json(reply, "the audit")
    try:
        draft = AuditDraft.model_validate(data)
    except ValidationError as exc:
        raise LLMError("the model's audit failed validation: " + "; ".join(e["msg"] for e in exc.errors()[:4])) from exc
    known_claims = {c.id for s in lesson.scenes for c in s.claims}
    known_scenes = {s.id for s in lesson.scenes}
    known_widgets = {w.id for w in lesson.widgets}
    flags = AuditFlags(
        lesson_sha256=lesson.sha256(),
        claims=[a for a in draft.claims if a.claim_id in known_claims],
        scenes=[a for a in draft.scenes if a.scene_id in known_scenes],
        widgets=[a for a in draft.widgets if a.widget_id in known_widgets],
    )
    return flags, reply


def summarize(flags: AuditFlags) -> str:
    c = sum(1 for a in flags.claims if a.contradicts)
    s = sum(1 for a in flags.scenes if a.contradicts)
    w = sum(1 for a in flags.widgets if not a.template_fits)
    if not (c or s or w):
        return "no contradictions found"
    return f"{c} claim{'s' if c != 1 else ''}, {s} scene{'s' if s != 1 else ''}, {w} widget{'s' if w != 1 else ''} flagged"
