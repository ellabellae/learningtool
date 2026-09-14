"""data/memory/concepts.json: what earlier lessons taught, and when they were read.

Read side (used by generate): slugs to reuse, tie-backs to papers already opened.
Write side (feat/audit-check adds the upsert on a passing check; feat/memory adds
mark_read on open) — kept here so every reader and writer shares one path.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .schema import ConceptRecord, MemoryRecord, PriorLink, Scene

DEFAULT_PATH = Path("data/memory/concepts.json")


def load_records(path: Path | str = DEFAULT_PATH) -> list[MemoryRecord]:
    path = Path(path)
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8") or "[]")
    return [MemoryRecord.model_validate(r) for r in raw]


def save_records(records: list[MemoryRecord], path: Path | str = DEFAULT_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([r.model_dump() for r in records], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def slugs_for_prompt(records: list[MemoryRecord], exclude_paper_id: str) -> list[dict]:
    """Slugs from other papers, deduplicated, for the generate prompt."""
    seen: dict[str, dict] = {}
    for r in records:
        if r.paper_id == exclude_paper_id or r.concept_id in seen:
            continue
        seen[r.concept_id] = {"concept_id": r.concept_id, "name": r.name, "paper_title": r.paper_title}
    return list(seen.values())


def prior_links(concepts: list[ConceptRecord], scenes: list[Scene], records: list[MemoryRecord], paper_id: str) -> list[PriorLink]:
    """Tie-backs by exact slug to papers that were actually opened (read_date set)."""
    scene_ids = {s.id for s in scenes}
    by_slug: dict[str, MemoryRecord] = {}
    for r in sorted(records, key=lambda r: r.read_date or ""):
        if r.paper_id != paper_id and r.read_date and r.concept_id not in by_slug:
            by_slug[r.concept_id] = r
    links: list[PriorLink] = []
    for c in concepts:
        r = by_slug.get(c.concept_id)
        if r and c.scene_id in scene_ids:
            links.append(PriorLink(scene_id=c.scene_id, concept_id=c.concept_id, paper_id=r.paper_id, paper_title=r.paper_title, read_date=r.read_date or "", note=""))
    return links


def upsert_paper(records: list[MemoryRecord], paper_id: str, paper_title: str, concepts: list[ConceptRecord]) -> list[MemoryRecord]:
    """Delete-then-insert for one paper, carrying over created_at and read_date."""
    old = {r.concept_id: r for r in records if r.paper_id == paper_id}
    kept = [r for r in records if r.paper_id != paper_id]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for c in concepts:
        prev = old.get(c.concept_id)
        kept.append(
            MemoryRecord(
                concept_id=c.concept_id, name=c.name, one_line=c.one_line, paper_id=paper_id, paper_title=paper_title,
                scene_id=c.scene_id, created_at=prev.created_at if prev else now, read_date=prev.read_date if prev else None,
            )
        )
    return kept


def mark_read(paper_id: str, path: Path | str = DEFAULT_PATH, when: str | None = None) -> int:
    """Set read_date on a paper's records if unset. Returns how many were marked."""
    records = load_records(path)
    when = when or datetime.now(timezone.utc).date().isoformat()
    n = 0
    for r in records:
        if r.paper_id == paper_id and not r.read_date:
            r.read_date = when
            n += 1
    if n:
        save_records(records, path)
    return n
