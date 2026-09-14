# learningtool

Drop in a research-paper PDF, get back a story-shaped interactive lesson: prerequisite
rungs first, then the paper as a plot (the world before, what they tried, your
prediction, what they found, what changed), with sliders on the paper's real knobs and
the paper's exact words one click away.

The design record is [docs/designs/papers-as-lessons.md](docs/designs/papers-as-lessons.md).
Deferred work with triggers is in [TODOS.md](TODOS.md).

## Requirements

- Python 3.12+
- Node 20+ (widget math is evaluated with `node` during `learn check`)
- `ANTHROPIC_API_KEY` in your environment (used by `learn generate`, `audit`, `repair`)

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
learn survey            # writes data/profile.yaml
```

## Model settings

- `ANTHROPIC_API_KEY` is read by the SDK. Default model is `claude-opus-5`; override with `LEARN_MODEL`.
- `LEARN_EFFORT` (default `high`) tunes reasoning depth and cost.
- `LEARN_LLM_FAKE=path/to/replies.json` replays canned replies instead of calling the API (tests, demos).

## Use

```bash
learn lesson path/to/paper.pdf     # extract -> generate -> audit -> check -> render -> open
learn list                         # papers, profiles, unread lessons
```

Each step is also its own command (`learn extract`, `generate`, `audit`, `check`,
`repair`, `render`, `open`). Paper ids are the PDF's sha256; any unique 8+ character
prefix works.

## Tests

```bash
pytest                 # unit + e2e
pytest -m eval         # real model calls against the fixture paper; needs the API key
```

Source PDFs, page crops, and rendered `lesson.html` files are gitignored; `spans.json`,
`lesson.json`, and `checks.json` are committed.
