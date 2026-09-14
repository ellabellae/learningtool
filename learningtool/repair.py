"""Targeted repair: previous lesson + failure list ──▶ replacements keyed by id ──▶ merged lesson.

Untouched claims, widgets, and scenes stay byte-identical; only listed ids change
(plus whole scenes the model chose to return for scene-level failures).
"""

from __future__ import annotations

from pathlib import Path

import anthropic
from pydantic import ValidationError

from .llm import LLM, Block, LLMError, Reply, parse_json
from .schema import Failure, Lesson, RepairReply, Span
from .generate import PROMPTS, stable_block


def repair(llm: LLM, *, lesson: Lesson, failures: list[Failure], spans: list[Span], registry: dict, progress=None) -> tuple[Lesson, Reply]:
    if not failures:
        return lesson, Reply(text="", stop_reason="end_turn", model="none")
    system = (PROMPTS / "repair.md").read_text(encoding="utf-8")
    blocks = [
        stable_block(spans, registry, []),
        Block(text="# Current lesson\n\n" + lesson.model_dump_json(indent=None)),
        Block(text="# Failures to fix\n\n" + "\n".join(f"- {f.kind} {f.id}: {f.reason}" for f in failures) + "\n\nReply with JSON only: replacements keyed by id."),
    ]
    schema = anthropic.transform_schema(RepairReply.model_json_schema())
    reply = llm.structured(system, blocks, schema, progress=progress)
    data = parse_json(reply, "the repair")
    try:
        replacements = RepairReply.model_validate(data)
    except ValidationError as exc:
        raise LLMError("the model's repair failed validation: " + "; ".join(e["msg"] for e in exc.errors()[:4])) from exc
    return merge(lesson, replacements), reply


def merge(lesson: Lesson, replacements: RepairReply) -> Lesson:
    """Apply replacements by id. Unknown ids are ignored (the merge never invents items)."""
    data = lesson.model_dump()
    scene_by_id = {s["id"]: s for s in data["scenes"]}
    for s in replacements.scenes:
        if s.id in scene_by_id:
            scene_by_id[s.id].clear()
            scene_by_id[s.id].update(s.model_dump())
    claim_repl = {c.id: c for c in replacements.claims}
    for s in data["scenes"]:
        s["claims"] = [claim_repl[c["id"]].model_dump() if c["id"] in claim_repl else c for c in s["claims"]]
    widget_repl = {w.id: w for w in replacements.widgets}
    data["widgets"] = [widget_repl[w["id"]].model_dump() if w["id"] in widget_repl else w for w in data["widgets"]]
    return Lesson.model_validate(data)
