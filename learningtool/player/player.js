/* learningtool lesson player.
   Every scene is server-rendered; this script only navigates, filters by depth,
   records predictions (localStorage, never the repo), opens the evidence drawer,
   and mounts widgets from window.TEMPLATES. No network. */
(() => {
  "use strict";
  const DEPTHS = ["brief", "standard", "deep"];
  const body = document.body;
  const scenes = Array.from(document.querySelectorAll("section.scene"));
  const arcItems = Array.from(document.querySelectorAll("#arc li"));
  const announce = document.getElementById("announce");
  const counter = document.getElementById("counter");
  const storeKey = `learningtool:${body.dataset.paper}:${body.dataset.profile}`;

  // ---- storage (private mode degrades to in-memory) -------------------------
  let mem = {};
  const store = {
    get() { try { return JSON.parse(localStorage.getItem(storeKey) || "{}"); } catch { return mem; } },
    set(v) { mem = v; try { localStorage.setItem(storeKey, JSON.stringify(v)); } catch { /* private mode */ } },
  };
  const state = Object.assign({ predictions: {}, depth: null, idx: 0 }, store.get());
  if (!DEPTHS.includes(state.depth)) state.depth = body.dataset.defaultDepth || "standard";

  // ---- depth ----------------------------------------------------------------
  const depthRank = (d) => DEPTHS.indexOf(d);
  const visible = () => scenes.filter((s) => depthRank(s.dataset.depth) <= depthRank(state.depth));
  const isVisible = (s) => depthRank(s.dataset.depth) <= depthRank(state.depth);

  function setDepth(d, fromUser) {
    const current = scenes[state.idx];
    state.depth = d;
    document.querySelectorAll('input[name="depth"], input[name="depth-foot"]').forEach((r) => { r.checked = r.value === d; });
    arcItems.forEach((li) => li.classList.toggle("hidden-depth", depthRank(li.dataset.depth) > depthRank(d)));
    if (!isVisible(current)) {
      // land on the nearest earlier visible scene and say so
      let j = state.idx;
      while (j > 0 && !isVisible(scenes[j])) j--;
      show(j, { announceText: `Depth set to ${d}; moved to “${headingOf(scenes[j])}”.` });
    } else {
      updateChrome();
      if (fromUser) say(`Depth set to ${d}: ${visible().length} scenes.`);
    }
    store.set(state);
  }

  // ---- navigation -------------------------------------------------------------
  function headingOf(s) { const h = s.querySelector("h1"); return h ? h.textContent.trim() : ""; }

  function show(idx, opts = {}) {
    const target = scenes[idx];
    if (!target || !isVisible(target)) return;
    scenes.forEach((s) => { s.hidden = s !== target; s.classList.remove("enter"); });
    target.classList.add("enter");
    state.idx = idx;
    store.set(state);
    location.hash = `#s${idx}`;
    mountReveal(target);
    mountClose(target);
    updateChrome();
    target.focus({ preventScroll: false });
    window.scrollTo({ top: 0 });
    say(opts.announceText || `${headingOf(target)}. Scene ${positionOf(idx)} of ${visible().length}.`);
  }

  function positionOf(idx) { return visible().indexOf(scenes[idx]) + 1; }

  function updateChrome() {
    const vis = visible();
    const pos = positionOf(state.idx);
    const secondsLeft = vis.slice(pos).reduce((a, s) => a + (Number(s.dataset.seconds) || 0), 0);
    counter.textContent = `scene ${pos} of ${vis.length}` + (secondsLeft ? ` · ~${Math.max(1, Math.round(secondsLeft / 60))} min left` : "");
    arcItems.forEach((li) => {
      const i = Number(li.dataset.idx);
      li.classList.toggle("cur", i === state.idx);
      li.classList.toggle("done", i < state.idx);
      li.querySelector("button").setAttribute("aria-current", i === state.idx ? "true" : "false");
    });
    // Overflow rule: past 8 visible dots, label only first, previous, current, next, last.
    const visLis = arcItems.filter((li) => !li.classList.contains("hidden-depth"));
    const k = visLis.findIndex((li) => Number(li.dataset.idx) === state.idx);
    visLis.forEach((li, j) => {
      const near = j === 0 || j === visLis.length - 1 || Math.abs(j - k) <= 1;
      li.classList.toggle("near", near);
      li.classList.toggle("nolabel", visLis.length > 8 && !near);
    });
    const back = document.getElementById("back"), next = document.getElementById("next");
    const narrow = window.matchMedia("(max-width: 767px)").matches;
    back.disabled = pos <= 1;
    const current = scenes[state.idx];
    if (current.dataset.role === "predict") {
      const locked = !!state.predictions[current.dataset.id];
      next.disabled = !locked;
      next.textContent = narrow ? "Next →" : locked ? "Next: what they found →" : "Lock in a prediction to continue";
    } else {
      next.disabled = pos >= vis.length;
      const nxt = vis[pos];
      next.textContent = narrow || !nxt ? "Next →" : `Next: ${nxt.querySelector(".kick")?.textContent.trim().toLowerCase() || "scene"} →`;
    }
    document.querySelectorAll(".foot").forEach((f) => { f.hidden = current.dataset.role === "title"; });
  }

  function step(delta) {
    const vis = visible();
    const j = vis.indexOf(scenes[state.idx]) + delta;
    if (j < 0 || j >= vis.length) return;
    show(scenes.indexOf(vis[j]));
  }

  // ---- predictions ------------------------------------------------------------
  scenes.filter((s) => s.dataset.role === "predict").forEach((s) => {
    const fs = s.querySelector(".predict"), lock = s.querySelector(".lock"), skip = s.querySelector(".skip"), note = s.querySelector(".locked-note");
    const radios = Array.from(fs.querySelectorAll("input[type=radio]"));
    const applyLocked = () => {
      const p = state.predictions[s.dataset.id];
      if (!p) return;
      fs.classList.add("locked");
      radios.forEach((r) => { r.disabled = true; r.checked = p.choice !== null && Number(r.value) === p.choice; });
      lock.disabled = true; skip.hidden = true; note.hidden = false;
      note.textContent = p.choice === null ? "You skipped the prediction. Next shows what the paper found." : "Locked in. Next shows what the paper found.";
    };
    radios.forEach((r) => r.addEventListener("change", () => { lock.disabled = false; }));
    lock.addEventListener("click", () => {
      const chosen = radios.find((r) => r.checked);
      if (!chosen) return;
      state.predictions[s.dataset.id] = { choice: Number(chosen.value), text: chosen.parentElement.textContent.trim() };
      store.set(state); applyLocked(); updateChrome();
    });
    skip.addEventListener("click", () => {
      state.predictions[s.dataset.id] = { choice: null, text: null };
      store.set(state); applyLocked(); updateChrome();
    });
    fs.addEventListener("keydown", (e) => { if (e.key === "Enter" && !lock.disabled) lock.click(); });
    applyLocked();
  });

  function mountReveal(scene) {
    if (scene.dataset.role !== "found") return;
    const said = scene.querySelector(".said"); if (!said) return;
    const predictId = said.dataset.predict;
    const p = state.predictions[predictId];
    const verdict = scene.querySelector("[data-verdict]");
    const quote = scene.querySelector("[data-reveal-quote]");
    const predictScene = scenes.find((s) => s.dataset.id === predictId);
    const answerIndex = predictScene ? Number(predictScene.querySelector(".predict").dataset.answer ?? "NaN") : NaN;
    const note = scene.dataset.revealNote || "";
    said.hidden = false;
    if (!p) { said.textContent = "You have not made a prediction yet."; }
    else if (p.choice === null) { said.textContent = "You skipped the prediction."; }
    else { said.textContent = `You said: ${p.text}`; }
    if (quote) requestAnimationFrame(() => quote.classList.add("in"));
    verdict.hidden = false;
    if (p && p.choice !== null && Number.isFinite(answerIndex)) {
      verdict.textContent = p.choice === answerIndex ? "That's what they found." : `Close. The paper found: ${note || headingOf(scene).toLowerCase()}.`;
    } else if (note) {
      verdict.textContent = note;
    } else { verdict.hidden = true; }
  }

  function mountClose(scene) {
    if (scene.dataset.role !== "close") return;
    const el = document.getElementById("close-prediction");
    const entries = Object.values(state.predictions);
    if (!entries.length) el.textContent = "You did not make a prediction.";
    else if (entries[0].choice === null) el.textContent = "You skipped the prediction.";
    else el.textContent = `You said: ${entries[0].text}`;
  }

  // ---- evidence drawer --------------------------------------------------------
  const drawer = document.getElementById("drawer"), drawerBody = document.getElementById("drawer-body");
  function openDrawer(scene, focusClaim, focusSpan) {
    const tpl = scene.querySelector("template.drawer-content");
    drawerBody.innerHTML = "";
    if (tpl) drawerBody.appendChild(tpl.content.cloneNode(true));
    drawerBody.querySelectorAll("li[data-claim]").forEach((li) => {
      const hit = (focusClaim && li.dataset.claim === focusClaim) || (focusSpan && li.dataset.span === focusSpan);
      li.classList.toggle("active", !!hit);
    });
    drawerBody.querySelectorAll(".zoom").forEach((b) => b.addEventListener("click", () => b.closest(".crop").classList.toggle("zoomed")));
    if (typeof drawer.showModal === "function") drawer.showModal(); else drawer.setAttribute("open", "");
    const active = drawerBody.querySelector("li.active"); if (active) active.scrollIntoView({ block: "start" });
  }
  document.addEventListener("click", (e) => {
    const b = e.target.closest(".open-drawer"); if (!b) return;
    openDrawer(b.closest("section.scene"), b.dataset.claim, b.dataset.span);
  });
  document.querySelector(".close-drawer").addEventListener("click", () => drawer.close());
  drawer.addEventListener("click", (e) => { if (e.target === drawer) drawer.close(); });

  // ---- scene menu (phone) -----------------------------------------------------
  const menu = document.getElementById("scene-menu"), menuList = document.getElementById("menu-list");
  document.getElementById("menu").addEventListener("click", () => {
    menuList.innerHTML = "";
    visible().forEach((s) => {
      const li = document.createElement("li"), b = document.createElement("button");
      b.type = "button"; b.className = "linkish"; b.textContent = headingOf(s);
      b.addEventListener("click", () => { menu.close(); show(scenes.indexOf(s)); });
      li.appendChild(b); menuList.appendChild(li);
    });
    menu.showModal();
  });
  document.querySelector(".close-menu").addEventListener("click", () => menu.close());

  // ---- widgets ------------------------------------------------------------------
  document.querySelectorAll(".widget").forEach((w) => {
    const cfg = JSON.parse(w.dataset.config);
    const tpl = (window.TEMPLATES || {})[w.dataset.template];
    const canvas = w.querySelector("canvas"), sliders = w.querySelector(".sliders"), readout = w.querySelector(".readout");
    if (!tpl) { canvas.hidden = true; w.querySelector(".unavailable").hidden = false; sliders.hidden = true; w.querySelector(".read").hidden = true; return; }
    const params = Object.assign({}, tpl.manifest.defaults || {}, cfg.params);
    const start = Object.assign({}, params);
    const inputs = {};
    Object.entries(cfg.ranges).forEach(([name, [lo, hi]]) => {
      const id = `${w.dataset.widget}-${name}`;
      const label = document.createElement("label"); label.htmlFor = id;
      const val = document.createElement("span"); val.className = "val";
      label.append(document.createTextNode(cfg.labels[name] || name), val);
      const spec = (tpl.manifest.params || {})[name] || {};
      const input = document.createElement("input");
      input.type = "range"; input.id = id; input.min = lo; input.max = hi; input.step = spec.step || (hi - lo) / 100; input.value = params[name];
      const nudge = document.createElement("div"); nudge.className = "nudge"; nudge.textContent = cfg.nudges[name] || "";
      const wrap = document.createElement("div"); wrap.append(label, input, nudge); sliders.appendChild(wrap);
      inputs[name] = { input, val };
    });
    const draw = () => {
      Object.entries(inputs).forEach(([n, { input, val }]) => { params[n] = Number(input.value); val.textContent = fmt(params[n], tpl.manifest.units && tpl.manifest.units[n]); });
      const out = tpl.model(params), startOut = tpl.model(start);
      const k = cfg.readout;
      readout.innerHTML = `${(tpl.manifest.output_labels || {})[k] || k}: ${fmt(startOut[k])} → <b>${fmt(out[k])}</b>`;
      const ctx = canvas.getContext("2d"); ctx.clearRect(0, 0, canvas.width, canvas.height);
      tpl.draw(ctx, params, canvas.width, canvas.height, { start });
    };
    Object.values(inputs).forEach(({ input }) => input.addEventListener("input", draw));
    w.querySelector(".reset").addEventListener("click", () => { Object.entries(inputs).forEach(([n, { input }]) => { input.value = start[n]; }); draw(); });
    draw();
  });
  function fmt(v, unit) { const s = Number.isInteger(v) ? String(v) : Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 10 ? v.toFixed(1) : v.toFixed(2); return unit ? `${s} ${unit}` : s; }

  // ---- wiring -----------------------------------------------------------------
  document.getElementById("start").addEventListener("click", () => step(1));
  document.getElementById("restart").addEventListener("click", () => show(0));
  document.getElementById("back").addEventListener("click", () => step(-1));
  document.getElementById("next").addEventListener("click", () => step(1));
  arcItems.forEach((li) => li.querySelector("button").addEventListener("click", () => show(Number(li.dataset.idx))));
  document.querySelectorAll('input[name="depth"], input[name="depth-foot"]').forEach((r) => r.addEventListener("change", () => setDepth(r.value, true)));
  document.addEventListener("keydown", (e) => {
    if (e.target.matches("input, textarea") || drawer.open || menu.open) return;
    if (e.key === "ArrowRight") { e.preventDefault(); if (!document.getElementById("next").disabled) step(1); }
    if (e.key === "ArrowLeft") { e.preventDefault(); step(-1); }
  });
  function say(t) { announce.textContent = ""; setTimeout(() => { announce.textContent = t; }, 30); }

  // ---- boot -------------------------------------------------------------------
  scenes.forEach((s) => { const sec = s.dataset.seconds; if (sec === undefined) s.dataset.seconds = "0"; });
  setDepth(state.depth, false);
  const fromHash = Number((location.hash.match(/^#s(\d+)$/) || [])[1]);
  show(Number.isFinite(fromHash) && scenes[fromHash] && isVisible(scenes[fromHash]) ? fromHash : (isVisible(scenes[state.idx]) ? state.idx : 0));
})();
