---
name: learningtool
family: atlas
mode: hybrid (app + read)
colors:
  paper: "#F7F3EC"
  ink: "#1F1D1A"
  slate: "#5B5F66"
  terracotta: "#B5502F"
  amber: "#B8860B"
  amber_bg: "#FFF4D6"
  hairline: "#D9D2C5"
type:
  display: "Instrument Serif"
  text: "Inter"
  mono: "ui-monospace"
scale: [16, 18, 22, 28, 40]
spacing_unit: 8
measure: 68ch
breakpoints: [768, 1024]
motion_ms: { scene: 150, reveal: 150, drawer: 200 }
---

# learningtool design system

The Atlas family, chosen deliberately (design decision 10A, 2026-09-13): one visual identity
across the author's personal tools. Mode is hybrid: an app surface (calm, dense, utility language)
wrapped around a reading surface (one column, 68ch measure, wayfinding as a feature).

## Color

| Token | Value | Use |
|-------|-------|-----|
| `--paper` | #F7F3EC | page ground |
| `--ink` | #1F1D1A | text |
| `--slate` | #5B5F66 | muted text, kickers, source lines (4.5:1 on paper) |
| `--terracotta` | #B5502F | the one accent: active arc dot, lock-in button, focus ring, selection, reward-threshold line |
| `--amber` / `--amber-bg` | #B8860B / #FFF4D6 | checker flags only |
| `--hairline` | #D9D2C5 | rules, chip borders, widget enclosure |

No gradients, no icons in circles, no shadows except the open evidence drawer
(offset 0 8px, blur 24px, 12% ink). Secondary text on the amber strip is tinted from amber, not grey.

## Type

Instrument Serif for the hook, scene headline, analogy line, quote, and verdict.
Inter for body, UI, chips. A mono face only for readout numbers.

| Size | Role |
|------|------|
| 16 | chips, kicker, source lines, footer, arc labels (never smaller) |
| 18 | body prose, 68ch measure, line-height 1.55 |
| 22 | analogy line, quote |
| 28 | scene headline, line-height 1.2 |
| 40 | hook on the title scene |

More space above a heading than below it (48 / 16).

## Layout

- ≥ 1024: single column centered; evidence drawer slides in from the right at 420px.
- 768-1023: same column; drawer is a bottom sheet; arc labels only for previous, current, next.
- < 768: arc collapses to "scene N of M" plus a scene menu; drawer is a full-screen sheet; footer sticky with a segmented dial; targets ≥ 44px.
- Footer is sticky at every width. Scenes may scroll.

## Motion

Scene crossfade 150ms ease-out; reveal fade 150ms; drawer slide 200ms; chart updates instant.
All off under `prefers-reduced-motion`.

## Browser surfaces

Selection and caret in terracotta; focus ring 2px terracotta with 2px paper offset;
thin slate scrollbars; visited tie-back links distinct from unvisited.
