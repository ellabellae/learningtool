// Threshold-gated learning curve.
//
//   power(0) = baseline
//   each session: forgetting pulls power toward baseline (more between-session
//   days -> more forgetting), then reward-gated learning pushes it up in
//   proportion to how likely the signal is to beat the threshold.
//
//   reward_prob = sigmoid((power - threshold) / k)
//   power <- baseline + (power - baseline) * decay      decay = exp(-0.08 * 7 / sessions_per_week)
//   power <- power + rate * reward_prob * (1 - power)
//
// Illustrative only. The math is owned here; the lesson only supplies params.
// `manifest` is injected by the page bundle (window.TEMPLATES) and is also
// importable by the node test runner, which passes params directly.

const BASELINE = 0.35;
const RATE = 0.25;
const K = 0.15;

function simulate(params) {
  const sessions = 20;
  const threshold = Number(params.threshold);
  const perWeek = Math.min(7, Math.max(1, Number(params.sessions_per_week)));
  const decay = Math.exp(-0.08 * (7 / perWeek));
  let power = BASELINE;
  const ys = [power];
  let firstReward = null;
  for (let s = 1; s <= sessions; s++) {
    const rewardProb = 1 / (1 + Math.exp(-(power - threshold) / K));
    if (firstReward === null && rewardProb > 0.5) firstReward = s;
    power = BASELINE + (power - BASELINE) * decay;
    power = power + RATE * rewardProb * (1 - power);
    ys.push(power);
  }
  return { ys, final_power: power, sessions_to_reward: firstReward === null ? sessions + 1 : firstReward };
}

export function model(params) {
  const r = simulate(params);
  return { final_power: r.final_power, sessions_to_reward: r.sessions_to_reward };
}

export function curve(params, xs) {
  const r = simulate(params);
  return (xs || r.ys.map((_, i) => i)).map((x) => r.ys[Math.min(r.ys.length - 1, Math.max(0, Math.round(x)))]);
}

export function draw(ctx, params, w, h, opts) {
  const pad = { l: 44, r: 16, t: 20, b: 34 };
  const ys = curve(params);
  const startYs = opts && opts.start ? curve(opts.start) : null;
  const n = ys.length - 1;
  const x = (i) => pad.l + (i / n) * (w - pad.l - pad.r);
  const y = (v) => pad.t + (1 - v) * (h - pad.t - pad.b);
  ctx.clearRect(0, 0, w, h);
  ctx.font = "13px Inter, system-ui, sans-serif";
  ctx.fillStyle = "#5B5F66";
  ctx.strokeStyle = "#D9D2C5";
  ctx.lineWidth = 1;
  // axes
  ctx.beginPath(); ctx.moveTo(pad.l, pad.t); ctx.lineTo(pad.l, h - pad.b); ctx.lineTo(w - pad.r, h - pad.b); ctx.stroke();
  ctx.fillText("session 1", pad.l, h - 12);
  ctx.fillText("session " + n, w - pad.r - 64, h - 12);
  ctx.save(); ctx.translate(12, h / 2); ctx.rotate(-Math.PI / 2); ctx.fillText("signal power", -40, 0); ctx.restore();
  // threshold line
  ctx.setLineDash([4, 4]); ctx.strokeStyle = "#B5502F";
  ctx.beginPath(); ctx.moveTo(pad.l, y(Number(params.threshold))); ctx.lineTo(w - pad.r, y(Number(params.threshold))); ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = "#B5502F"; ctx.fillText("reward threshold", w - pad.r - 110, y(Number(params.threshold)) - 6);
  // starting curve (faint) then current
  if (startYs) {
    ctx.strokeStyle = "#D9D2C5"; ctx.lineWidth = 2; ctx.beginPath();
    startYs.forEach((v, i) => (i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v)))); ctx.stroke();
  }
  ctx.strokeStyle = "#1F1D1A"; ctx.lineWidth = 2; ctx.beginPath();
  ys.forEach((v, i) => (i ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v)))); ctx.stroke();
}
