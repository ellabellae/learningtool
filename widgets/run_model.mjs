// Batch runner used by the Python checker (rule 6) and tests.
// stdin: {"templates_dir": "...", "calls": [{"template_id": "...", "params": {...}}, ...]}
// stdout: [{"ok": true, "outputs": {...}} | {"ok": false, "error": "..."}, ...]
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { join } from "node:path";

const req = JSON.parse(readFileSync(0, "utf8"));
const cache = new Map();
const out = [];
for (const call of req.calls) {
  try {
    let mod = cache.get(call.template_id);
    if (!mod) {
      mod = await import(pathToFileURL(join(req.templates_dir, call.template_id, "model.mjs")).href);
      cache.set(call.template_id, mod);
    }
    const outputs = mod.model(call.params);
    out.push({ ok: true, outputs });
  } catch (e) {
    out.push({ ok: false, error: String(e && e.message ? e.message : e) });
  }
}
process.stdout.write(JSON.stringify(out));
