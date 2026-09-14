"""Data shapes for every file the pipeline reads or writes.

Two rules shape everything here (see docs/designs/papers-as-lessons.md,
"Data shapes"):

1. ``LessonDraft`` is exactly what the model emits through structured output.
   It stays flat: no unions between object types, no recursion, dicts are lists
   of named records. ``Lesson`` adds the fields the pipeline post-fills.
2. Statuses never live in ``lesson.json``. ``Checks`` and ``AuditFlags`` carry
   them, stamped with the sha256 of the lesson they were computed for.

    LessonDraft (model output)          Lesson (on disk)
    +---------------------+             +--------------------------------+
    | hook_question       |             | LessonDraft fields             |
    | scenes[Scene]       |  post-fill  | schema_version, paper_id,      |
    | widgets[Widget]     | ----------> | profile_name, paper_meta,      |
    | concepts[Concept]   |             | prior_links[PriorLink]         |
    +---------------------+             +--------------------------------+
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = 1

Depth = Literal["brief", "standard", "deep"]
Role = Literal["prereq", "before", "problem", "tried", "predict", "found", "changed"]
ClaimKind = Literal["finding", "background", "illustrative"]
ClaimStatus = Literal["cited", "failed", "unchecked"]
Delta = Literal["+", "-"]

DEPTH_ORDER: dict[str, int] = {"brief": 0, "standard": 1, "deep": 2}
STORY_ROLES: tuple[str, ...] = ("before", "problem", "tried", "predict", "found", "changed")
BRIEF_REQUIRED_ROLES: tuple[str, ...] = ("problem", "tried", "found", "changed")


class Strict(BaseModel):
    """All models reject unknown keys so a drifting prompt fails loudly."""

    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- #
# Profile and extraction
# --------------------------------------------------------------------------- #


class Profile(Strict):
    schema_version: int = SCHEMA_VERSION
    name: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    known_domains: list[str] = Field(default_factory=list)
    default_depth: Depth = "standard"
    skip_topics: list[str] = Field(default_factory=list)
    known_concepts: list[str] = Field(default_factory=list)


class Span(Strict):
    """One sentence of the paper. One page per span; a sentence that crosses a
    page or column break is split into two spans upstream."""

    id: str
    page: int = Field(ge=1)
    text: str = Field(min_length=1)
    bboxes: list[list[float]] = Field(min_length=1)

    @model_validator(mode="after")
    def _boxes_are_rects(self) -> "Span":
        for box in self.bboxes:
            if len(box) != 4:
                raise ValueError(f"span {self.id}: every bbox needs 4 numbers, got {box}")
        return self


class PaperMeta(Strict):
    """Verbatim from the PDF's metadata and first-page spans. Never model-written."""

    title: str = Field(min_length=1)
    authors: list[str] = Field(default_factory=list)
    venue: str | None = None
    year: int | None = None
    pages: int = Field(ge=1)


# --------------------------------------------------------------------------- #
# Lesson content (model-emitted)
# --------------------------------------------------------------------------- #


class NamedValue(Strict):
    name: str
    value: float


class Range(Strict):
    name: str
    lo: float
    hi: float

    @model_validator(mode="after")
    def _ordered(self) -> "Range":
        if self.lo >= self.hi:
            raise ValueError(f"range {self.name}: lo must be < hi")
        return self


class Label(Strict):
    name: str
    text: str


class Behavior(Strict):
    """"Raising <param> makes <output> go <output_delta>" — rule 6 evaluates it."""

    param: str
    param_delta: Delta
    output: str
    output_delta: Delta


class Widget(Strict):
    id: str
    template_id: str
    params: list[NamedValue] = Field(default_factory=list)
    ranges: list[Range] = Field(default_factory=list)
    labels: list[Label] = Field(default_factory=list)
    task_question: str = Field(min_length=1)
    nudges: list[Label] = Field(default_factory=list)
    readout_output: str = Field(min_length=1)
    evidence_span_ids: list[str] = Field(min_length=1)
    mechanism_span_ids: list[str] = Field(min_length=1)
    expected_behaviors: list[Behavior] = Field(default_factory=list)


class Claim(Strict):
    id: str
    text: str = Field(min_length=1)
    kind: ClaimKind
    quote_span_id: str | None = None
    span_ids: list[str] = Field(default_factory=list)
    citation: str | None = None


class Scene(Strict):
    id: str
    role: Role
    min_depth: Depth
    headline: str = Field(min_length=1)
    analogy_line: str = ""
    prose: str = Field(min_length=1)
    claims: list[Claim] = Field(default_factory=list)
    widget_id: str | None = None
    prediction_question: str | None = None
    prediction_options: list[str] = Field(default_factory=list)
    prediction_answer_index: int | None = None
    reveal_scene_id: str | None = None
    reveal_note: str | None = None


class ConceptRecord(Strict):
    concept_id: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    name: str
    one_line: str
    scene_id: str


class LessonDraft(Strict):
    """What the generate call emits. Structural validators live here so a bad
    draft fails at parse time, before anything is written."""

    hook_question: str = Field(min_length=1)
    scenes: list[Scene] = Field(min_length=1)
    widgets: list[Widget] = Field(default_factory=list)
    concepts: list[ConceptRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def _structure(self) -> "LessonDraft":
        scene_ids = [s.id for s in self.scenes]
        _unique("scene", scene_ids)
        _unique("widget", [w.id for w in self.widgets])
        _unique("claim", [c.id for s in self.scenes for c in s.claims])

        by_id = {s.id: s for s in self.scenes}
        widget_ids = {w.id for w in self.widgets}

        # Play order: every prereq rung precedes every story scene.
        seen_story = False
        for s in self.scenes:
            if s.role == "prereq" and seen_story:
                raise ValueError(f"scene {s.id}: prereq scenes must precede story scenes")
            if s.role != "prereq":
                seen_story = True

        for s in self.scenes:
            if s.widget_id is not None and s.widget_id not in widget_ids:
                raise ValueError(f"scene {s.id}: widget_id {s.widget_id!r} does not resolve")
            if s.role == "predict":
                if not s.prediction_question:
                    raise ValueError(f"scene {s.id}: predict scene needs prediction_question")
                if len(s.prediction_options) < 2:
                    raise ValueError(f"scene {s.id}: predict scene needs >= 2 options")
                if s.prediction_answer_index is not None and not (
                    0 <= s.prediction_answer_index < len(s.prediction_options)
                ):
                    raise ValueError(f"scene {s.id}: prediction_answer_index out of range")
                target = by_id.get(s.reveal_scene_id or "")
                if target is None:
                    raise ValueError(f"scene {s.id}: reveal_scene_id does not resolve")
                if target.role != "found":
                    raise ValueError(f"scene {s.id}: reveal target {target.id} must have role 'found'")
                if target.min_depth != s.min_depth:
                    raise ValueError(
                        f"scene {s.id}: predict and reveal scenes must share min_depth "
                        f"({s.min_depth} vs {target.min_depth})"
                    )
                if s.prediction_answer_index is None and not target.reveal_note:
                    raise ValueError(
                        f"scene {s.id}: free-form prediction needs reveal_note on {target.id}"
                    )
            elif s.prediction_question or s.prediction_options or s.reveal_scene_id:
                raise ValueError(f"scene {s.id}: only predict scenes carry prediction fields")

        # Brief mode is a complete arc.
        brief_roles = {s.role for s in self.scenes if s.min_depth == "brief"}
        missing = [r for r in BRIEF_REQUIRED_ROLES if r not in brief_roles]
        if missing:
            raise ValueError(f"brief depth must include roles {list(BRIEF_REQUIRED_ROLES)}; missing {missing}")

        for c in self.concepts:
            if c.scene_id not in by_id:
                raise ValueError(f"concept {c.concept_id}: scene_id {c.scene_id!r} does not resolve")
        return self


class PriorLink(Strict):
    scene_id: str
    concept_id: str
    paper_id: str
    paper_title: str
    read_date: str
    note: str = ""


class Lesson(LessonDraft):
    """LessonDraft plus what generate.py fills in. Never mutated by check."""

    schema_version: int = SCHEMA_VERSION
    paper_id: str = Field(min_length=8)
    profile_name: str
    paper_meta: PaperMeta
    prior_links: list[PriorLink] = Field(default_factory=list)

    @model_validator(mode="after")
    def _links_resolve(self) -> "Lesson":
        scene_ids = {s.id for s in self.scenes}
        for link in self.prior_links:
            if link.scene_id not in scene_ids:
                raise ValueError(f"prior_link {link.concept_id}: scene_id {link.scene_id!r} does not resolve")
        return self

    def sha256(self) -> str:
        """Stable hash of the lesson content; stamps audit.json and checks.json."""
        payload = self.model_dump_json(exclude_none=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def scene(self, scene_id: str) -> Scene:
        for s in self.scenes:
            if s.id == scene_id:
                return s
        raise KeyError(scene_id)

    def scenes_at(self, depth: Depth) -> list[Scene]:
        limit = DEPTH_ORDER[depth]
        return [s for s in self.scenes if DEPTH_ORDER[s.min_depth] <= limit]


# --------------------------------------------------------------------------- #
# Audit, checks, repair, memory
# --------------------------------------------------------------------------- #


class ClaimAudit(Strict):
    claim_id: str
    contradicts: bool
    reason: str = ""


class SceneAudit(Strict):
    scene_id: str
    contradicts: bool
    reason: str = ""


class WidgetAudit(Strict):
    widget_id: str
    template_fits: bool
    reason: str = ""


class AuditDraft(Strict):
    """What the audit call emits: flags only. It can never clear a structural failure."""

    claims: list[ClaimAudit] = Field(default_factory=list)
    scenes: list[SceneAudit] = Field(default_factory=list)
    widgets: list[WidgetAudit] = Field(default_factory=list)


class AuditFlags(AuditDraft):
    schema_version: int = SCHEMA_VERSION
    lesson_sha256: str


class ClaimCheck(Strict):
    claim_id: str
    status: ClaimStatus
    reason: str | None = None


class WidgetCheck(Strict):
    widget_id: str
    ok: bool
    reason: str | None = None


class SceneCheck(Strict):
    scene_id: str
    flags: list[str] = Field(default_factory=list)
    widget_removed: bool = False
    reveal_nulled: bool = False


class Checks(Strict):
    schema_version: int = SCHEMA_VERSION
    paper_id: str
    profile_name: str
    lesson_sha256: str
    claims: list[ClaimCheck] = Field(default_factory=list)
    widgets: list[WidgetCheck] = Field(default_factory=list)
    scenes: list[SceneCheck] = Field(default_factory=list)

    def passed(self) -> bool:
        """Exit-0 condition: zero failures of any kind and at least one finding cited."""
        any_failed = any(c.status == "failed" for c in self.claims)
        any_widget_bad = any(not w.ok for w in self.widgets)
        any_scene_flag = any(s.flags for s in self.scenes)
        any_cited = any(c.status == "cited" for c in self.claims)
        return not (any_failed or any_widget_bad or any_scene_flag) and any_cited


class Failure(Strict):
    kind: Literal["claim", "widget", "scene"]
    id: str
    reason: str


class RepairRequest(Strict):
    lesson: Lesson
    failures: list[Failure] = Field(min_length=1)


class RepairReply(Strict):
    """Replacements keyed by id. Untouched items stay byte-identical on merge."""

    claims: list[Claim] = Field(default_factory=list)
    widgets: list[Widget] = Field(default_factory=list)
    scenes: list[Scene] = Field(default_factory=list)


class MemoryRecord(Strict):
    """One entry in data/memory/concepts.json."""

    concept_id: str
    name: str
    one_line: str
    paper_id: str
    paper_title: str
    scene_id: str
    created_at: str
    read_date: str | None = None


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _unique(kind: str, ids: list[str]) -> None:
    seen: set[str] = set()
    for i in ids:
        if i in seen:
            raise ValueError(f"duplicate {kind} id {i!r}")
        seen.add(i)


def draft_json_schema() -> dict:
    """The JSON schema handed to structured output. Kept in a function so a test
    can assert it contains nothing the API rejects (recursion, complex enums)."""
    return LessonDraft.model_json_schema()


def dump(model: BaseModel) -> str:
    return json.dumps(model.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"
