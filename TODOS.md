# TODOS

## Pipeline

### Chunked generation for papers over 150k characters

**What:** Split spans by section heading, generate story scenes per chunk, merge scene lists, run audit and check on the merged lesson.

**Why:** `learn generate` refuses long papers and truncated output is a hard failure. Long economics and review papers are exactly the ones Ella wants digested.

**Context:** Trigger: the "lesson too long for one call (N tokens); chunking arrives in v1.1" message, or `stop_reason == max_tokens`. Start here: `learningtool/generate.py` already assembles the cacheable spans prefix; `learningtool/repair.py`'s merge-by-id knows how to combine keyed scenes. Generate prereq rungs once from the whole paper's concept list, then story scenes per chunk.

**Effort:** M
**Priority:** P2
**Depends on:** feat/generate, feat/audit-check

### Sub-sentence quotes

**What:** Let the model emit `quote_text`; the checker requires it to be an exact substring of the cited span and computes highlight offsets in Python.

**Why:** Whole-sentence quotes are sometimes a paragraph-long sentence where one clause matters.

**Context:** Trigger: reading whole-sentence quotes where the relevant part is a clause, more than a couple of times per lesson. Deferred because model-emitted character offsets are unreliable (cannot count characters); substring matching sidesteps that. Start here: `Claim.quote_text` optional field, rule 1 in `learningtool/check.py`, offset computation in `learningtool/render.py`.

**Effort:** S
**Priority:** P3
**Depends on:** feat/audit-check

### Paper figures as the fallback for widget-less scenes

**What:** `Figure` records (page, bbox, caption span id) from pymupdf4llm image blocks plus a "Fig." caption regex; a scene with no supported widget shows the paper's own figure with its caption verbatim.

**Why:** Rules 5, 6, and the audit strip widgets whose template shape the paper doesn't support; those scenes currently get prose only.

**Context:** Trigger: most papers end up with stripped widgets, meaning the template library isn't covering the mechanisms Ella reads about. Vector plots need `page.cluster_drawings()`, not `get_images()`. Start here: `learningtool/extract.py` (figures.json), `Scene.figure_id`, `learningtool/render.py` figure panel.

**Effort:** M
**Priority:** P3
**Depends on:** feat/extract, feat/player

## Product

### Approach B: local web app (FastAPI + Vite player)

**What:** Wrap the same pipeline commands in a FastAPI service; a Vite player streams scenes as they're generated, renders the real PDF page with PDF.js highlights, and lists past papers with tie-backs.

**Why:** Friends without Python can use it; streaming makes the multi-minute generation feel alive; the survey becomes a form.

**Context:** Decision 5fa11115 (2026-09-12): Approach A chosen at completeness 7/10. Trigger: the lesson JSON schema goes five papers without changing, or a non-technical friend wants to use it. Start here: `learningtool/` stays as-is; add `server/` (FastAPI) and `web/` (Vite) that call the same functions `learn` calls.

**Effort:** L
**Priority:** P3
**Depends on:** feat/memory (v1 complete)

## Completed
