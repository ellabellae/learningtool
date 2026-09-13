// Before/after, two groups. Baseline is fixed; the params are the changes.
//   treated_after = baseline + treatment_effect
//   control_after = baseline + control_drift
//   difference    = treatment_effect - control_drift
// Illustrative only: bars show the shape of a two-group comparison, not the study's values.

const BASELINE = 0.5;

export function model(params) {
  const t = BASELINE + Number(params.treatment_effect);
  const c = BASELINE + Number(params.control_drift);
  return { treated_after: t, control_after: c, difference: t - c };
}

export function curve(params) {
  const m = model(params);
  return [BASELINE, m.treated_after, BASELINE, m.control_after];
}

export function draw(ctx, params, w, h, opts) {
  const pad = { l: 44, r: 16, t: 20, b: 34 };
  const bars = curve(params);
  const labels = ["treated · before", "treated · after", "control · before", "control · after"];
  const colors = ["#D9D2C5", "#1F1D1A", "#D9D2C5", "#5B5F66"];
  const maxV = 1.1;
  const y = (v) => pad.t + (1 - Math.max(0, v) / maxV) * (h - pad.t - pad.b);
  const slot = (w - pad.l - pad.r) / 4;
  ctx.clearRect(0, 0, w, h);
  ctx.font = "13px Inter, system-ui, sans-serif";
  ctx.strokeStyle = "#D9D2C5"; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(pad.l, pad.t); ctx.lineTo(pad.l, h - pad.b); ctx.lineTo(w - pad.r, h - pad.b); ctx.stroke();
  bars.forEach((v, i) => {
    const x0 = pad.l + i * slot + slot * 0.2;
    ctx.fillStyle = colors[i];
    ctx.fillRect(x0, y(v), slot * 0.6, h - pad.b - y(v));
    ctx.fillStyle = "#5B5F66";
    ctx.fillText(labels[i], x0 - 4, h - 12);
  });
  if (opts && opts.start) {
    const s = model(opts.start);
    ctx.strokeStyle = "#B5502F"; ctx.setLineDash([4, 4]);
    ctx.beginPath(); ctx.moveTo(pad.l, y(s.treated_after)); ctx.lineTo(w - pad.r, y(s.treated_after)); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "#B5502F"; ctx.fillText("starting value", w - pad.r - 90, y(s.treated_after) - 6);
  }
}
