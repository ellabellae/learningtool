"""learningtool: research-paper PDFs in, story-shaped interactive lessons out.

Pipeline (one directory per paper under data/papers/<sha256>/):

    extract  -> spans.json                    (pymupdf4llm reading order, sentence spans with boxes)
    generate -> lessons/<profile>/lesson.json (one structured LLM call -> LessonDraft, post-filled)
    audit    -> lessons/<profile>/audit.json  (flag-only semantic pass)
    check    -> lessons/<profile>/checks.json (rules 1-8, deterministic; memory upsert on pass)
    repair   -> lesson.json patched by id     (once, if check fails)
    render   -> lessons/<profile>/lesson.html (self-contained; gitignored)
    open     -> browser; sets read_date in data/memory/concepts.json

Design record: docs/designs/papers-as-lessons.md
"""

__version__ = "0.1.0"
