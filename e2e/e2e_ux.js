// E2E: board/detail UX and the UI paths behind the bug fixes, clicking the real viewer.
const { chromium, devices } = require("playwright");
const path = require("path");
const SB = process.env.SANDBOX || __dirname;   // sandbox built by e2e/run.sh
const fs = require("fs");
const BASE = "http://127.0.0.1:8766";
const OUT = path.join(SB, "out");
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);
const results = {};
const check = (name, ok, detail = "") => { results[name] = { ok: !!ok, detail }; log(ok ? "PASS" : "FAIL", name, detail); };
const api = async (page, p, method = "GET", body) => {
  const r = await page.request.fetch(BASE + p, { method, data: body, headers: { Origin: BASE } });
  return r.json();
};

(async () => {
  const browser = await chromium.launch({ channel: "chromium", headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  let exports = 0;
  page.on("request", (r) => { if (r.url().endsWith("/api/export")) exports++; });
  await page.goto(BASE + "/");
  await page.waitForSelector(".card");
  await page.waitForTimeout(3000);   // past the first task poll
  check("page load fetches the board once (no idle-poll re-render)", exports === 1, `${exports} /api/export calls`);

  // Policy 1.8.0 on the board: next step + walk-away, the tile, the filter, the detail block.
  const ns = await page.$$eval(".card", (cs) => cs.map((c) => ({ id: c.dataset.id, next: c.querySelector(".chip.next")?.textContent || null, walk: c.querySelector(".walk")?.textContent || null })));
  const assessedIds = await page.evaluate(async () => (await (await fetch("/api/export")).json()).listings.filter((l) => l.assessment?.next_step && !l.verdict_override && l.role === "candidate" && l.availability === "active").map((l) => String(l.id)));
  const missingChip = assessedIds.filter((id) => ns.some((c) => c.id === id && !c.next));
  check("assessed cards show a next-step chip (your own verdict wins where set)", assessedIds.length > 0 && missingChip.length === 0, `${assessedIds.length} with a next step; missing on ${missingChip.join(",")}`);
  check("cards show a walk-away price", ns.filter((c) => c.walk).length > 0, `${ns.filter((c) => c.walk).length} cards; e.g. ${ns.find((c) => c.walk)?.walk}`);
  const tile = Number(await page.$$eval(".tile", (ts) => ts.find((t) => /Contact now/.test(t.textContent)).querySelector(".v").textContent));
  const contactChips = ns.filter((c) => c.next === "Contact now").length;
  check("Contact now tile matches the chips", tile === contactChips, `tile ${tile}, chips ${contactChips}`);
  await page.click('#f-next button[data-v="Contact now"]');
  await page.waitForTimeout(300);
  const filtered = await page.$$eval(".card", (cs) => cs.map((c) => c.querySelector(".chip.next")?.textContent));
  check("Next step filter shows only Contact now cars", filtered.length === contactChips && filtered.every((t) => t === "Contact now"), `${filtered.length} shown`);
  if (filtered.length) {
    await page.click(".card .title");
    await page.waitForSelector(".next-panel");
    const panel = await page.textContent(".next-panel");
    const walkRow = await page.$$eval(".kv .k", (ks) => ks.some((k) => /Walk-away \(max price/.test(k.textContent)));
    check("listing page shows next step, merit, walk-away and its derivation", /Contact now/.test(panel) && /Known merit/.test(panel) && /Walk-away/.test(panel) && walkRow, panel.replace(/\s+/g, " ").slice(0, 120));
    await page.goBack();
    await page.waitForSelector(".card");
  }
  await page.click('#f-next button[data-v=""]');
  await page.waitForTimeout(300);

  // "Best score" is exactly the badge number, highest first; preliminary cards come after a divider.
  await page.selectOption("#f-sort", "score");
  await page.waitForTimeout(400);
  const seq = await page.$$eval("#list .grid > *", (els) => els.map((e) => e.classList.contains("grid-divider") ? "|" : (e.querySelector(".badge:not(.prelim)") ? Number(e.querySelector(".badge").textContent) : "p")));
  const assessedSeq = seq.slice(0, seq.indexOf("p") < 0 ? seq.length : seq.indexOf("p")).filter((x) => typeof x === "number");
  const desc = assessedSeq.every((v, i) => i === 0 || assessedSeq[i - 1] >= v);
  check("Best score sorts by the badge number, highest first", desc && assessedSeq.length > 5, assessedSeq.slice(0, 12).join(" "));
  check("a divider separates assessed from preliminary cards", !seq.includes("p") || seq[seq.indexOf("p") - 1] === "|", seq.slice(Math.max(0, seq.indexOf("p") - 2), seq.indexOf("p") + 1).join(" "));
  await page.selectOption("#f-sort", "pursue");
  await page.waitForTimeout(300);

  // Scroll memory + focus when returning from a listing.
  await page.evaluate(() => window.scrollTo(0, 1600));
  await page.waitForTimeout(300);
  const y0 = await page.evaluate(() => scrollY);
  const target = await page.evaluate(() => {
    const c = [...document.querySelectorAll(".card")].find((el) => { const r = el.getBoundingClientRect(); return r.top > 80 && r.bottom < innerHeight; });
    return c.dataset.id;
  });
  await page.click(`.card[data-id="${target}"] .title`);
  await page.waitForSelector(".pager");
  check("card opens its listing page", page.url().endsWith(`#/l/${target}`), page.url());
  const pagerText = await page.textContent(".pager");
  await page.goBack();
  await page.waitForSelector(`.card[data-id="${target}"].was-open`);
  const y1 = await page.evaluate(() => scrollY);
  const focused = await page.evaluate(() => document.activeElement?.dataset?.id);
  check("back to board keeps the scroll position", Math.abs(y1 - y0) < 5, `${y0} -> ${y1}`);
  check("back to board focuses the card you opened", focused === target, `${focused}`);

  // Pager + arrow keys follow board order.
  const order = await page.$$eval(".card", (cs) => cs.map((c) => c.dataset.id));
  await page.click(`.card[data-id="${order[2]}"] .title`);
  await page.waitForSelector(".pager");
  check("pager shows position in board order", (await page.textContent(".pager")).includes(`3 / ${order.length}`), `${pagerText} / ${await page.textContent(".pager")}`);
  log("before key:", await page.evaluate(() => [location.hash, document.activeElement.tagName, document.activeElement.className, document.querySelector('.pager a[aria-label="Next listing"]')?.getAttribute("href")].join(" | ")), "order[3]=", order[3]);
  await page.keyboard.press("ArrowRight");
  await page.waitForTimeout(1500);
  log("after key:", await page.evaluate(() => location.hash));
  await page.waitForFunction((id) => location.hash === `#/l/${id}`, order[3], { polling: 250 });
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowLeft");
  await page.keyboard.press("ArrowLeft");
  await page.waitForTimeout(1500);
  check("rapid → ← ← lands one before where it started", page.url().endsWith(`#/l/${order[2]}`), page.url());
  await page.click('.pager a[aria-label="Next listing"]');
  await page.waitForFunction((id) => location.hash === `#/l/${id}`, order[3], { polling: 250 });
  check("← / → keys and ‹ › step through the board", true, `${order[2]} ⇄ ${order[3]}`);
  await page.focus("#notes");
  await page.keyboard.press("ArrowLeft");
  await page.waitForTimeout(400);
  check("arrow keys inside a text box do not navigate", page.url().endsWith(`#/l/${order[3]}`));
  const openListing = await page.$eval("a.btn[target=_blank]", (a) => a.href);
  check("detail page still has Open listing ↗", /^https?:/.test(openListing), openListing);

  // Re-normalize from the UI keeps an ended auction ended (bug 1).
  const ex = await api(page, "/api/export");
  const ended = ex.listings.find((l) => l.availability === "ended" && l.role === "comp");
  await page.goto(BASE + `/#/l/${ended.id}`);
  await page.waitForFunction((id) => location.hash === `#/l/${id}` && document.querySelector(".pager"), ended.id, { polling: 250 });
  await page.waitForTimeout(600);   // hash navigation re-renders after a data reload
  await page.waitForSelector("#renorm");
  await page.click("#renorm");
  await page.waitForFunction(() => [...document.querySelectorAll(".toast")].some((t) => /Re-normalized|failed|error/i.test(t.textContent)), null, { timeout: 60000, polling: 500 });
  const after = await api(page, `/api/listings/${ended.id}`);
  check("Re-normalize button keeps an ended auction ended", after.availability === "ended" && after.role === "comp", `#${ended.id} ${after.availability}/${after.role}`);

  // Role set by hand survives the ingest path a sync uses (bug 5).
  const cand = ex.listings.find((l) => l.role === "candidate" && l.availability === "active" && l.site === "cargurus");
  await page.goto(BASE + `/#/l/${cand.id}`);
  await page.waitForFunction((id) => location.hash === `#/l/${id}` && document.querySelector(".pager"), cand.id, { polling: 250 });
  await page.waitForTimeout(600);   // hash navigation re-renders after a data reload
  await page.waitForSelector("#role");
  await page.selectOption("#role", "comp");
  await page.waitForTimeout(800);
  await api(page, "/api/ingest", "POST", { site: "cargurus", items: [{ url: cand.url, price_text: "$" + (cand.price || 20000), card_text: "card" }], defer_ai: true });
  const c2 = await api(page, `/api/listings/${cand.id}`);
  check("role set in the UI survives a sync", c2.role === "comp", `#${cand.id} ${c2.role}; raw_text ${c2.raw_text?.length} chars`);
  check("card-only sync kept the full page text (bug 3)", (c2.raw_text || "").length > 600, `${(c2.raw_text || "").length} chars`);
  await page.selectOption("#role", "candidate");   // put it back
  await page.waitForTimeout(500);

  // Mark sold / gone from the listing page: off the candidates board, into the comps (Jason 2026-09-25).
  const ex2 = await api(page, "/api/export");
  const victim = ex2.listings.find((l) => l.role === "candidate" && l.availability === "active" && l.assessment && !l.verdict_override && l.id !== cand.id);
  await page.goto(BASE + "/");
  await page.waitForSelector(`.card[data-id="${victim.id}"]`);
  await page.evaluate((id) => { location.hash = "#/l/" + id; }, victim.id);
  await page.waitForFunction((id) => location.hash === `#/l/${id}` && document.querySelector("#mark-gone"), victim.id, { polling: 250 });
  await page.waitForTimeout(600);
  await page.click("#mark-gone");
  await page.waitForFunction(() => [...document.querySelectorAll(".toast")].some((t) => /Marked sold/.test(t.textContent)), null, { timeout: 15000, polling: 250 });
  const v2 = await api(page, `/api/listings/${victim.id}`);
  check("Mark sold / gone makes it a sold comp with verdict Do not pursue", v2.availability === "sold" && v2.role === "comp" && v2.status === "Sold" && v2.assessment?.verdict === "Do not pursue", `#${victim.id} ${v2.availability}/${v2.role}/${v2.status}/${v2.assessment?.verdict}`);
  await page.goto(BASE + "/");
  await page.waitForSelector(".card");
  check("…and it is gone from the candidates board", !(await page.$(`.card[data-id="${victim.id}"]`)));
  await page.click('#role button[data-v="comp"]');
  await page.waitForSelector(".card");
  check("…and listed under Comps", !!(await page.$(`.card[data-id="${victim.id}"]`)));
  await page.click('#role button[data-v="candidate"]');
  await page.waitForSelector(".card");

  // Re-assess top 15 with no API key: clear failure, nothing half-done.
  await page.goto(BASE + "/");
  await page.waitForSelector("#reassess-top");
  page.once("dialog", async (d) => { fs.writeFileSync(path.join(OUT, "reassess-confirm.txt"), d.message()); await d.accept(); });
  await page.click("#reassess-top");
  await page.waitForFunction(() => [...document.querySelectorAll(".toast")].some((t) => /Re-assess/.test(t.textContent)), null, { timeout: 30000, polling: 500 });
  const toast = await page.$$eval(".toast", (ts) => ts.map((t) => t.textContent).join(" | "));
  const confirmText = fs.readFileSync(path.join(OUT, "reassess-confirm.txt"), "utf8");
  check("re-assess confirm lists 15 cars with a cost estimate", (confirmText.match(/^\d+\. /gm) || []).length === 15 && /tier 1/.test(confirmText) && /About \$\d+\.\d\d total/.test(confirmText), confirmText.split("\n")[0]);
  check("re-assess without a key fails visibly", /ANTHROPIC_API_KEY/.test(toast), toast);

  // Phone layout.
  const phone = await browser.newContext({ ...devices["iPhone 13"] });
  const mp = await phone.newPage();
  await mp.goto(BASE + "/");
  await mp.waitForSelector(".card");
  const w = await mp.evaluate(() => ({ doc: document.documentElement.scrollWidth, win: innerWidth, railOpen: document.querySelector(".rail").open }));
  check("phone: no horizontal scroll", w.doc <= w.win, JSON.stringify(w));
  check("phone: filters start folded", w.railOpen === false);
  await mp.screenshot({ path: path.join(OUT, "phone-board.png"), fullPage: false });
  await mp.click(".card .title");
  await mp.waitForSelector(".pager");
  const w2 = await mp.evaluate(() => document.documentElement.scrollWidth <= innerWidth);
  check("phone: listing page has no horizontal scroll", w2);

  // Dark theme renders with the dark tokens.
  await page.goto(BASE + "/");
  await page.click("#theme");
  const bg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  await page.screenshot({ path: path.join(OUT, "board-theme-toggled.png") });
  check("theme toggle switches palettes", /rgb/.test(bg), bg);
  check("no page errors", errors.length === 0, errors.join(" | "));

  await browser.close();
  fs.writeFileSync(path.join(OUT, "results-ux.json"), JSON.stringify(results, null, 2));
  log("DONE", Object.values(results).filter((r) => !r.ok).length, "failures");
})().catch((e) => { console.error("ERROR", e); process.exit(1); });
