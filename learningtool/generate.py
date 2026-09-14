"""spans + profile + memory + registry ──▶ ONE structured call ──▶ lesson.json

    prompt layout (stable prefix first, so audit and repair reuse the cache):
      system   prompts/lesson.md                                    [cache]
      block 1  the paper's sentences + widget templates + memory slugs [cache]
      block 2  reader profile, requested depth, and the schema reminder

    LessonDraft (model output) ──post-fill──▶ Lesson {paper_id, profile_name, paper_meta, prior_links}
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import anthropic
from pydantic import ValidationError

from . import memory
from .llm import LLM, Block, LLMError, Reply, parse_json
from .schema import Lesson, LessonDraft, PaperMeta, Profile, Span, draft_json_schema

PROMPTS = Path(__file__).parent / "prompts"
MAX_SPAN_CHARS = 150_000


class TooLong(LLMError):
    pass


@dataclass
class GenerateResult:
    lesson: Lesson
    reply: Reply


def stable_block(spans: list[Span], registry: dict, slugs: list[dict]) -> Block:
    lines = ["# The paper, as numbered sentences", ""]
    for s in spans:
        lines.append(f"[{s.id}] (p. {s.page}) {s.text}")
    lines += ["", "# Widget templates available (choose only when the mechanism fits)", ""]
    for t in registry.values():
        params = ", ".join(f"{p.name} [{p.lo}, {p.hi}] default {p.default}" for p in t.params.values())
        lines.append(f"- {t.id}: {t.description} Params: {params}. Outputs: {', '.join(t.outputs)}.")
    lines += ["", "# Concept slugs from lessons already in memory (reuse an exact slug when the idea matches)", ""]
    lines += [f"- {s['concept_id']}: {s['name']} (from \"{s['paper_title']}\")" for s in slugs] or ["(none yet)"]
    return Block(text="\n".join(lines), cache=True)


def profile_block(profile: Profile) -> Block:
    lines = [
        "# The reader",
        "",
        f"Known domains for analogies: {', '.join(profile.known_domains) or 'none given; use everyday life'}.",
        f"Already understands (do not write prerequisite rungs for these): {', '.join(profile.known_concepts) or 'nothing listed'}.",
        f"Topics to keep short or skip: {', '.join(profile.skip_topics) or 'none'}.",
        "",
        "Write the full lesson at depth 'deep'; mark each scene's min_depth so brief and standard are complete subsets.",
        "Reply with JSON only, matching the schema.",
    ]
    return Block(text="\n".join(lines))


def guard_length(spans: list[Span]) -> None:
    chars = sum(len(s.text) for s in spans)
    if chars > MAX_SPAN_CHARS:
        raise TooLong(f"paper has {chars:,} characters of sentences, above the {MAX_SPAN_CHARS:,} limit for one lesson; chunking arrives in v1.1.")


def generate(
    llm: LLM,
    *,
    spans: list[Span],
    profile: Profile,
    paper_id: str,
    paper_meta: PaperMeta,
    registry: dict,
    records: list[memory.MemoryRecord],
    progress=None,
) -> GenerateResult:
    guard_length(spans)
    system = (PROMPTS / "lesson.md").read_text(encoding="utf-8")
    slugs = memory.slugs_for_prompt(records, paper_id)
    blocks = [stable_block(spans, registry, slugs), profile_block(profile)]
    schema = anthropic.transform_schema(draft_json_schema())
    reply = llm.structured(system, blocks, schema, progress=progress)
    data = parse_json(reply, "the lesson")
    try:
        draft = LessonDraft.model_validate(data)
    except ValidationError as exc:
        raise LLMError("the model's lesson failed validation:\n" + _short_errors(exc) + "\nRe-run: learn generate <id>") from exc
    lesson = post_fill(draft, paper_id=paper_id, profile_name=profile.name, paper_meta=paper_meta, records=records)
    return GenerateResult(lesson=lesson, reply=reply)


def post_fill(draft: LessonDraft, *, paper_id: str, profile_name: str, paper_meta: PaperMeta, records: list[memory.MemoryRecord]) -> Lesson:
    links = memory.prior_links(draft.concepts, draft.scenes, records, paper_id)
    return Lesson(**draft.model_dump(), paper_id=paper_id, profile_name=profile_name, paper_meta=paper_meta, prior_links=links)


def _short_errors(exc: ValidationError) -> str:
    out = []
    for e in exc.errors()[:8]:
        loc = ".".join(str(p) for p in e["loc"]) or "(root)"
        out.append(f"  {loc}: {e['msg']}")
    return "\n".join(out)


def usage_line(reply: Reply) -> str:
    cache = f" · cache {reply.cache_read_tokens} read / {reply.cache_write_tokens} written" if (reply.cache_read_tokens or reply.cache_write_tokens) else ""
    m, s = divmod(int(reply.elapsed), 60)
    return f"{reply.model} · {reply.input_tokens} in / {reply.output_tokens} out{cache} · {m}:{s:02d}"


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
