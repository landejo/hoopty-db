// E2E: what Publish actually pushed (gh-pages in the sandbox's local origin),
// served as static files, versus the local workbench it was published from.
const { chromium } = require("playwright");
const path = require("path");
const fs = require("fs");
const SB = process.env.SANDBOX || __dirname;   // sandbox built by e2e/run.sh
const LOCAL = "http://127.0.0.1:8766";
const PUB = "http://127.0.0.1:8767";
const SITE = path.join(SB, "site");
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);
const results = {};
const check = (name, ok, detail = "") => { results[name] = { ok: !!ok, detail }; log(ok ? "PASS" : "FAIL", name, detail); };

function walk(dir) { return fs.readdirSync(dir, { withFileTypes: true }).flatMap((d) => d.isDirectory() ? walk(path.join(dir, d.name)) : [path.join(dir, d.name)]); }

(async () => {
  // 1. Leak scan over every published file (same patterns as publish.find_leaks).
  const VIN = /\b(?=[A-HJ-NPR-Z0-9]{17}\b)(?=[A-HJ-NPR-Z0-9]*[0-9])(?=[A-HJ-NPR-Z0-9]*[A-HJ-NPR-Z])[A-HJ-NPR-Z0-9]{17}\b/;
  const PHONE = /(?<!\d)(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?!\d)/;
  const EMAIL = /[\w.+-]+@[\w-]+\.[\w.-]+/;
  const files = walk(SITE).filter((f) => f.endsWith(".json"));
  const leaks = [];
  for (const f of files) {
    const t = fs.readFileSync(f, "utf8");
    for (const [k, re] of [["VIN", VIN], ["phone", PHONE], ["email", EMAIL]]) { const m = t.match(re); if (m) leaks.push(`${path.relative(SITE, f)}: ${k} ${m[0]}`); }
  }
  check("no VIN, phone or email in any published file", leaks.length === 0, `${files.length} files; ${leaks.slice(0, 3).join(" | ")}`);
  const index = JSON.parse(fs.readFileSync(path.join(SITE, "data", "index.json"), "utf8"));
  const rawText = files.some((f) => /"raw_text"/.test(fs.readFileSync(f, "utf8")));
  check("raw listing text is not published", !rawText);

  const browser = await chromium.launch({ channel: "chromium", headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  const errors = [];
  const orderOf = async (base) => {
    const p = await ctx.newPage();
    p.on("pageerror", (e) => errors.push(base + ": " + e.message));
    await p.goto(base + "/");
    await p.waitForSelector(".card");
    const ids = await p.$$eval(".card", (cs) => cs.map((c) => c.dataset.id));
    return { p, ids };
  };
  // Same default filters in both (fresh context = empty localStorage per origin).
  const local = await orderOf(LOCAL);
  const pub = await orderOf(PUB);
  check("published board shows the same cars as local", JSON.stringify([...local.ids].sort()) === JSON.stringify([...pub.ids].sort()), `${local.ids.length} local, ${pub.ids.length} published`);
  const firstDiff = local.ids.findIndex((id, i) => id !== pub.ids[i]);
  check("published board sorts exactly like local", firstDiff === -1, firstDiff === -1 ? "" : `first difference at rank ${firstDiff + 1}: local #${local.ids[firstDiff]} vs published #${pub.ids[firstDiff]}`);

  const p = pub.p;
  check("published mode pill says published", /published/.test(await p.textContent("#mode")), await p.textContent("#mode"));
  check("local-only controls are absent when published", !(await p.$("#check-avail")) && !(await p.$("#reassess-top")) && await p.$eval("#publish", (b) => b.hidden));
  const srcLinks = await p.$$eval(".card a.src[target=_blank]", (as) => as.length);
  check("published cards have source links", srcLinks === pub.ids.length, `${srcLinks}/${pub.ids.length}`);

  // Detail pages: fetched on demand from data/l/<id>.json.
  let detailFails = [];
  for (const id of pub.ids.slice(0, 12)) {
    await p.evaluate((id) => { location.hash = "#/l/" + id; }, id);
    try {
      await p.waitForFunction((id) => location.hash === "#/l/" + id && document.querySelector(".pager") && !/Loading/.test(document.querySelector("#app").textContent.slice(0, 200)), id, { polling: 200, timeout: 15000 });
    } catch (e) { detailFails.push(id); }
  }
  check("published listing pages load (first 12)", detailFails.length === 0, detailFails.join(","));
  const hasActions = await p.$("#analyze");
  check("published listing page has no write actions", !hasActions);
  await p.goto(PUB + "/#/market");
  await p.waitForSelector(".tiles");
  check("published market view renders", (await p.$$(".tile")).length >= 4);
  check("published index generated_at is set", !!index.generated_at, index.generated_at);
  check("no page errors", errors.length === 0, errors.slice(0, 3).join(" | "));
  await p.screenshot({ path: path.join(SB, "out", "published-market.png") });
  await browser.close();
  fs.writeFileSync(path.join(SB, "out", "results-published.json"), JSON.stringify(results, null, 2));
  log("DONE", Object.values(results).filter((r) => !r.ok).length, "failures");
})().catch((e) => { console.error("ERROR", e); process.exit(1); });
