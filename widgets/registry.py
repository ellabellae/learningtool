"""Widget template registry: manifests are the single contract.

    widgets/templates/<id>/manifest.json   params, hard bounds, defaults, outputs, labels
    widgets/templates/<id>/model.mjs       model(params) -> {output: number}, curve(), draw()

    Python side (this module)                    JS side (page bundle / node runner)
    validate_widget(): rule 5                    TEMPLATES[id].model(params)
    check_behaviors(): rule 6 via one node call  run_model.mjs batches calls from stdin
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from learningtool.schema import Widget

TEMPLATES_DIR = Path(__file__).parent / "templates"
RUNNER = Path(__file__).parent / "run_model.mjs"


class NodeMissing(Exception):
    """node is a runtime requirement for widget checks."""


@dataclass
class Param:
    name: str
    lo: float
    hi: float
    default: float
    unit: str = ""
    label: str = ""


@dataclass
class Template:
    id: str
    dir: Path
    name: str
    description: str
    params: dict[str, Param]
    outputs: list[str]
    output_labels: dict[str, str] = field(default_factory=dict)
    mechanism_keywords: list[str] = field(default_factory=list)

    @property
    def model_path(self) -> Path:
        return self.dir / "model.mjs"


def load_registry(templates_dir: Path = TEMPLATES_DIR) -> dict[str, Template]:
    registry: dict[str, Template] = {}
    for d in sorted(p for p in templates_dir.iterdir() if p.is_dir()):
        manifest = d / "manifest.json"
        if not manifest.exists():
            continue
        m = json.loads(manifest.read_text(encoding="utf-8"))
        if m.get("id") != d.name:
            raise ValueError(f"{manifest}: id {m.get('id')!r} must equal directory name {d.name!r}")
        params = {
            name: Param(name=name, lo=float(spec["lo"]), hi=float(spec["hi"]), default=float(spec["default"]), unit=spec.get("unit", ""), label=spec.get("label", name))
            for name, spec in m["params"].items()
        }
        registry[d.name] = Template(
            id=d.name, dir=d, name=m.get("name", d.name), description=m.get("description", ""), params=params,
            outputs=list(m["outputs"]), output_labels=dict(m.get("output_labels", {})), mechanism_keywords=list(m.get("mechanism_keywords", [])),
        )
    return registry


def validate_widget(widget: Widget, registry: dict[str, Template]) -> list[str]:
    """Rule 5. Returns reasons; an empty list means the widget is well-formed."""
    reasons: list[str] = []
    t = registry.get(widget.template_id)
    if t is None:
        return [f"unknown template {widget.template_id!r}"]
    values = {p.name: p.value for p in widget.params}
    ranges = {r.name: r for r in widget.ranges}
    for name in list(values) + list(ranges):
        if name not in t.params:
            reasons.append(f"param {name!r} is not declared by template {t.id!r}")
    for name, r in ranges.items():
        spec = t.params.get(name)
        if spec is None:
            continue
        if r.lo < spec.lo or r.hi > spec.hi:
            reasons.append(f"range for {name!r} [{r.lo}, {r.hi}] exceeds hard bounds [{spec.lo}, {spec.hi}]")
        if name not in values:
            reasons.append(f"ranged param {name!r} has no starting value")
        elif not (r.lo <= values[name] <= r.hi):
            reasons.append(f"value {values[name]} for {name!r} is outside its range [{r.lo}, {r.hi}]")
    for name, v in values.items():
        spec = t.params.get(name)
        if spec and name not in ranges and not (spec.lo <= v <= spec.hi):
            reasons.append(f"value {v} for {name!r} is outside hard bounds [{spec.lo}, {spec.hi}]")
    if widget.readout_output not in t.outputs:
        reasons.append(f"readout_output {widget.readout_output!r} is not an output of {t.id!r}")
    for b in widget.expected_behaviors:
        if b.param not in t.params:
            reasons.append(f"behavior references unknown param {b.param!r}")
        if b.output not in t.outputs:
            reasons.append(f"behavior references unknown output {b.output!r}")
    return _dedupe(reasons)


def evaluate(calls: list[tuple[str, dict[str, float]]], templates_dir: Path = TEMPLATES_DIR) -> list[dict]:
    """One node process for every call. Each result is {'ok': bool, 'outputs': {...}} or {'ok': False, 'error': str}."""
    node = shutil.which("node")
    if not node:
        raise NodeMissing("node is not installed; widget checks need Node 20+ (https://nodejs.org)")
    payload = json.dumps({"templates_dir": str(templates_dir), "calls": [{"template_id": t, "params": p} for t, p in calls]})
    proc = subprocess.run([node, str(RUNNER)], input=payload, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"node runner failed: {proc.stderr.strip()[:500]}")
    return json.loads(proc.stdout)


def check_behaviors(widgets: list[Widget], registry: dict[str, Template], templates_dir: Path = TEMPLATES_DIR) -> dict[str, list[str]]:
    """Rule 6 for many widgets in one node call. Returns {widget_id: [reasons]}; empty list = all behaviors hold.

    For each behavior: evaluate model() with the named param at its range lo and hi
    (other params at their starting values) and require the named output to move
    strictly in the stated direction."""
    calls: list[tuple[str, dict[str, float]]] = []
    index: list[tuple[str, int, str]] = []  # (widget_id, behavior_no, 'lo'|'hi')
    for w in widgets:
        if w.template_id not in registry or validate_widget(w, registry):
            continue
        base = {p.name: p.value for p in w.params}
        for name, spec in registry[w.template_id].params.items():
            base.setdefault(name, spec.default)
        ranges = {r.name: r for r in w.ranges}
        for i, b in enumerate(w.expected_behaviors):
            r = ranges.get(b.param)
            lo, hi = (r.lo, r.hi) if r else (registry[w.template_id].params[b.param].lo, registry[w.template_id].params[b.param].hi)
            calls.append((w.template_id, dict(base, **{b.param: lo}))); index.append((w.id, i, "lo"))
            calls.append((w.template_id, dict(base, **{b.param: hi}))); index.append((w.id, i, "hi"))
    results = evaluate(calls, templates_dir) if calls else []
    got: dict[tuple[str, int], dict[str, dict]] = {}
    for (wid, i, end), res in zip(index, results):
        got.setdefault((wid, i), {})[end] = res
    out: dict[str, list[str]] = {w.id: [] for w in widgets}
    for w in widgets:
        for i, b in enumerate(w.expected_behaviors):
            pair = got.get((w.id, i))
            if not pair:
                continue
            for end in ("lo", "hi"):
                if not pair[end].get("ok"):
                    out[w.id].append(f"behavior {i + 1}: model() failed at {b.param}={end}: {pair[end].get('error')}")
            if out[w.id]:
                continue
            lo_v, hi_v = pair["lo"]["outputs"].get(b.output), pair["hi"]["outputs"].get(b.output)
            if lo_v is None or hi_v is None:
                out[w.id].append(f"behavior {i + 1}: output {b.output!r} missing from model()")
                continue
            rises = hi_v > lo_v
            falls = hi_v < lo_v
            expected_rise = (b.param_delta == "+") == (b.output_delta == "+")
            if (expected_rise and not rises) or (not expected_rise and not falls):
                out[w.id].append(
                    f"behavior {i + 1}: raising {b.param} should make {b.output} go {'up' if expected_rise else 'down'}, "
                    f"but it went from {lo_v:.4g} to {hi_v:.4g}"
                )
    return out


def params_used_by(model_path: Path) -> set[str]:
    """Names the model.mjs reads from params, for the manifest parity test."""
    src = model_path.read_text(encoding="utf-8")
    return set(re.findall(r"params\.([A-Za-z_]\w*)", src)) | set(re.findall(r"params\[\"([A-Za-z_]\w*)\"\]", src))


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    return [x for x in items if not (x in seen or seen.add(x))]
