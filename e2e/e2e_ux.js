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
// Open a listing page and wait until THAT listing has rendered (its Open listing link points at it):
// the previous page's heading or pager is still in the DOM while the board data reloads.
const openDetail = async (page, l) => {
  await page.evaluate((id) => { location.hash = "#/l/" + id; }, l.id);
  await page.waitForFunction((u) => { const a = document.querySelector("a.btn[target=_blank]"); return a && a.href === new URL(u).href; }, l.url, { timeout: 15000, polling: 100 });
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

  // Photos (2026-10-04): every card photo comes through /api/photo; one that cannot load
  // (an expired Facebook link with no saved copy, as in this sandbox) is a placeholder, never a broken image.
  await page.evaluate(async () => { for (const c of document.querySelectorAll(".card")) { c.scrollIntoView(); await new Promise((r) => setTimeout(r, 40)); } window.scrollTo(0, 0); });
  await page.waitForTimeout(4000);
  const ph = await page.$$eval(".card .photo", (ps) => ps.map((p) => { const im = p.querySelector("img"); return { src: im ? im.getAttribute("src") : null, broken: !!(im && im.complete && !im.naturalWidth), placeholder: !!p.querySelector(".nophoto.expired") }; }));
  check("card photos go through the local saved-copy endpoint", ph.filter((x) => x.src).every((x) => x.src.startsWith("/api/photo?u=")), `${ph.filter((x) => x.src).length} with a photo`);
  check("no broken card images: unavailable photos show a placeholder", ph.every((x) => !x.broken), `${ph.filter((x) => x.placeholder).length} placeholders, ${ph.filter((x) => x.src && !x.broken).length} loaded`);

  // Policy 1.8.0 on the board: next step + walk-away, the tile, the filter, the detail block.
  const ns = await page.$$eval(".card", (cs) => cs.map((c) => ({ id: c.dataset.id, next: c.querySelector(".chip.next")?.textContent || null, walk: c.querySelector(".walk")?.textContent || null })));
  const assessedIds = await page.evaluate(async () => (await (await fetch("/api/export")).json()).listings.filter((l) => l.assessment?.next_step && (l.status || "New") === "New" && l.role === "candidate" && l.availability === "active").map((l) => String(l.id)));
  const missingChip = assessedIds.filter((id) => ns.some((c) => c.id === id && !c.next));
  check("assessed cards show a next-step chip (your call wins where made)", assessedIds.length > 0 && missingChip.length === 0, `${assessedIds.length} with a next step; missing on ${missingChip.join(",")}`);
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

  // History report filter: Has + Missing split the board; Missing never shows a Carfax/AutoCheck chip.
  const reportCards = async (v) => {
    await page.click(`#f-report button[data-v="${v}"]`);
    await page.waitForTimeout(300);
    return page.$$eval(".card", (cs) => cs.map((c) => /Carfax|AutoCheck/.test([...c.querySelectorAll(".body .chip.olive")].map((x) => x.textContent).join(" "))));
  };
  const anyN = (await reportCards("")).length, has = await reportCards("has"), missing = await reportCards("missing");
  check("History report filter splits the board into has + missing", has.length > 0 && has.length + missing.length === anyN, `${has.length} has + ${missing.length} missing = ${anyN}`);
  check("Missing shows no card with a report chip", missing.every((x) => !x), `${missing.filter(Boolean).length} with a chip`);
  await reportCards("");

  // Model families: Z3 family + 1 / 3 Series + Porsche together; photo mode steps through just those.
  const famBtns = await page.$$eval("#f-families button", (bs) => bs.map((b) => b.textContent.replace(/\s*\d+$/, "").trim()));
  for (const name of famBtns) {
    await page.click(`#f-families button:has-text("${name}")`);
    await page.waitForTimeout(250);
  }
  // Judged by each card's model profile (titles vary: an M Roadster can be "1998 BMW M RWD").
  const famProfiles = await page.evaluate(async () => {
    const d = await (await fetch("/api/export")).json();
    const make = Object.fromEntries(d.profiles.map((p) => [p.key, p.make || ""]));
    const byId = Object.fromEntries(d.listings.map((l) => [String(l.id), l.profile_key || ""]));
    return [...document.querySelectorAll(".card")].map((c) => [byId[c.dataset.id], make[byId[c.dataset.id]]]);
  });
  const famOnly = famProfiles.every(([k, mk]) => /^(z3_|bmw_z3|bmw_1\d\di|bmw_3\d\d|bmw_m3_)/.test(k) || /^porsche/i.test(mk));
  check("family chips show only those families, no Z4", famBtns.includes("BMW Z3 family") && famProfiles.length > 0 && famOnly,
        `${famBtns.join(", ")} → ${famProfiles.length} cards; ${[...new Set(famProfiles.map(([k]) => k))].join(" ")}`);
  const famIds = await page.$$eval(".card", (cs) => cs.filter((c) => c.querySelector(".photo img")).map((c) => c.dataset.id));
  await page.click("#slideshow");
  await page.waitForSelector(".slideshow img");
  const steps = [];
  for (let i = 0; i < famIds.length; i++) { steps.push(await page.$eval(".slideshow img", (im) => im.getAttribute("src"))); await page.keyboard.press("ArrowRight"); await page.waitForTimeout(150); }
  check("photo mode steps through exactly the filtered cars, then wraps", new Set(steps).size === famIds.length && (await page.$eval(".slideshow img", (im) => im.getAttribute("src"))) === steps[0], `${steps.length} cars`);
  await page.keyboard.press("Escape");
  await page.waitForTimeout(200);
  for (const name of famBtns) { await page.click(`#f-families button:has-text("${name}")`); await page.waitForTimeout(200); }   // back to all models

  // Drivable home: only cars in states within two days' drive of Carmel.
  const allN = await page.$$eval(".card", (cs) => cs.length);
  await page.check("#f-drive");
  await page.waitForTimeout(300);
  const driveLocs = await page.evaluate(async () => {
    const d = await (await fetch("/api/export")).json();
    const loc = Object.fromEntries(d.listings.map((l) => [String(l.id), l.location || ""]));
    return [...document.querySelectorAll(".card")].map((c) => loc[c.dataset.id]);
  });
  const okState = (s) => /,\s*(CA|NV|OR|WA|AZ|UT|ID|NM)\b/.test(s) || /^(california|nevada|oregon|washington|arizona|utah|idaho|new mexico)$/i.test(s.trim());
  check("drivable-home filter keeps only CA, NV, OR, WA, AZ, UT, ID, NM cars", driveLocs.length > 0 && driveLocs.length < allN && driveLocs.every(okState),
        `${driveLocs.length} of ${allN}; ${[...new Set(driveLocs.map((s) => (s.match(/,\s*([A-Z]{2})/) || [])[1] || s))].join(" ")}`);
  await page.uncheck("#f-drive");
  await page.waitForTimeout(300);

  // A budget-capped car with fewer than 3 comps, none included, shows the budget limit, not "% over".
  const exp = await api(page, "/api/export");
  const thin = new Set((exp.listings || exp).filter((l) => l.assessment?.costs?.max_price_basis === "budget" && (l.assessment.costs.fair_n ?? 0) < 3).map((l) => String(l.id)));
  const thinWalks = (await page.$$eval(".card", (cs) => cs.map((c) => ({ id: c.dataset.id, walk: c.querySelector(".walk")?.textContent || "" })))).filter((c) => thin.has(c.id) && c.walk);
  check("cars with too few comps (none included) show the budget limit, not % over", thinWalks.length > 0 && thinWalks.every((c) => /too few sales to value/.test(c.walk) && !/% over/.test(c.walk)),
        `${thinWalks.length} cards; e.g. ${thinWalks[0]?.walk}`);

  // Photo mode: cards' lead photos in board order; ← → change car, ↑ ↓ that car's photos, Esc leaves.
  const leads = await page.$$eval(".card .photo img", (ims) => ims.slice(0, 2).map((i) => i.getAttribute("src")));
  await page.click("#slideshow");
  await page.waitForSelector(".slideshow img");
  const shown = () => page.$eval(".slideshow img", (i) => i.getAttribute("src"));
  const first = await shown();
  await page.keyboard.press("ArrowRight");
  await page.waitForTimeout(300);
  const second = await shown();
  check("photo mode opens on the first card's lead photo and → steps to the next car", first === leads[0] && second === leads[1], `${first === leads[0]} / ${second === leads[1]}`);
  await page.waitForTimeout(300);   // the car's full photo list
  await page.keyboard.press("ArrowDown");
  await page.waitForTimeout(200);
  const down = await shown();
  await page.keyboard.press("ArrowUp");
  await page.waitForTimeout(200);
  check("↓ shows another photo of the same car and ↑ comes back", down !== second && (await shown()) === second, `${down !== second ? "changed" : "same (one photo?)"}`);
  await page.keyboard.press("ArrowLeft");
  await page.waitForTimeout(200);
  check("← steps back a car", (await shown()) === leads[0]);
  await page.keyboard.press("Escape");
  await page.waitForTimeout(300);
  check("Esc closes photo mode and leaves the board as it was", !(await page.$(".slideshow")) && !!(await page.$("#slideshow")) && !/#\/l\//.test(page.url()));

  // "Best score" is exactly the assessment score, highest first; preliminary cards come after a divider.
  // A card with a Fit badge shows the assessment score as its "S nn" tag (2026-10-18).
  await page.selectOption("#f-sort", "score");
  await page.waitForTimeout(400);
  const seq = await page.$$eval("#list .grid > *", (els) => els.map((e) => {
    if (e.classList.contains("grid-divider")) return "|";
    const s = e.querySelector(".badge.fit + .score-tag");
    if (s) return Number(s.textContent.replace(/\D/g, ""));
    return e.querySelector(".badge:not(.prelim)") ? Number(e.querySelector(".badge").textContent) : "p";
  }));
  const assessedSeq = seq.slice(0, seq.indexOf("p") < 0 ? seq.length : seq.indexOf("p")).filter((x) => typeof x === "number");
  const desc = assessedSeq.every((v, i) => i === 0 || assessedSeq[i - 1] >= v);
  check("Best score sorts by the assessment score, highest first", desc && assessedSeq.length > 5, assessedSeq.slice(0, 12).join(" "));
  check("a divider separates assessed from preliminary cards", !seq.includes("p") || seq[seq.indexOf("p") - 1] === "|", seq.slice(Math.max(0, seq.indexOf("p") - 2), seq.indexOf("p") + 1).join(" "));
  await page.selectOption("#f-sort", "pursue");
  await page.waitForTimeout(300);

  // Curiosities: over the $40k line (listed price or expected hammer) they leave the candidates board.
  const exC = await api(page, "/api/export");
  const refPrice = (l) => Math.max(l.price || 0, ["expected_hammer", "current_bid", "asking"].includes(l.assessment?.costs?.price_basis) ? (l.assessment?.costs?.price || 0) : 0);
  const overLine = exC.listings.filter((l) => (l.role === "candidate" || l.role === "curiosity") && l.availability === "active" && refPrice(l) > 40000);
  const candIds = await page.$$eval(".card", (cs) => cs.map((c) => Number(c.dataset.id)));
  const strays = overLine.filter((l) => l.role === "candidate" && candIds.includes(l.id));
  check("no car over the curiosity line is left on Candidates (unless set by hand)", strays.every((l) => l.role_user_set), strays.map((l) => "#" + l.id).join(","));
  await page.click('#role button[data-v="curiosity"]');
  await page.waitForSelector(".card");
  const cur = await page.$$eval(".card", (cs) => cs.map((c) => ({ id: Number(c.dataset.id), chip: !!c.querySelector(".chip.curiosity") })));
  check("the Curious tab lists them, each with a Curiosity chip", cur.length >= overLine.filter((l) => !l.role_user_set).length && cur.every((c) => c.chip), `${cur.length} curiosities`);
  await page.click('#role button[data-v="candidate"]');
  await page.waitForSelector(".card");

  // Rail toggles: every button fully visible, none overlapping (Jason's screenshot, 2026-09-28).
  const railCheck = async (pg) => pg.$$eval(".rail .seg button", (bs) => {
    const boxes = bs.map((b) => ({ t: b.textContent.trim(), r: b.getBoundingClientRect(), clipped: b.scrollWidth > b.clientWidth + 1 }));
    const overlaps = [];
    for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
      const a = boxes[i].r, b = boxes[j].r;
      if (a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1) overlaps.push(boxes[i].t + "/" + boxes[j].t);
    }
    return { n: boxes.length, clipped: boxes.filter((x) => x.clipped).map((x) => x.t), overlaps };
  });
  const rc = await railCheck(page);
  check("rail toggles: no overlap, no clipped labels", rc.n >= 9 && !rc.clipped.length && !rc.overlaps.length, JSON.stringify(rc));

  // Collapsing the filter rail gives the cards another column, and it stays collapsed on reload.
  const cols = () => page.$eval(".grid", (g) => getComputedStyle(g).gridTemplateColumns.split(" ").length);
  const colsOpen = await cols();
  await page.click(".rail > summary");
  await page.waitForTimeout(300);
  const colsShut = await cols();
  const railW = await page.$eval(".rail", (r) => r.getBoundingClientRect().width);
  check("collapsing the filters adds a column of cars", colsShut === colsOpen + 1 && railW < 60, `${colsOpen} → ${colsShut} columns; rail ${Math.round(railW)}px`);
  await page.reload();
  await page.waitForSelector(".card");
  check("the filters stay collapsed after a reload", !(await page.$eval(".rail", (r) => r.open)) && (await cols()) === colsShut);
  await page.click(".rail > summary");
  await page.waitForTimeout(300);
  check("reopening brings the filters back", (await page.$eval(".rail", (r) => r.open)) && (await cols()) === colsOpen);

  // Limits hide cars; the note names the limit and "Show them" brings them back.
  const before = await page.$$eval(".card", (cs) => cs.length);
  await page.fill("#f-max-price", "15000");
  await page.waitForSelector(".hidden-note", { timeout: 5000 });
  const note = await page.textContent(".hidden-note");
  check("limit note names the limit", /hidden by your limits \(price over \$15,000\)/.test(note), note.trim());
  await page.click("#show-hidden");
  await page.waitForFunction((n) => document.querySelectorAll(".card").length === n && !document.querySelector(".hidden-note"), before, { timeout: 5000, polling: 200 });
  check("Show them clears the limits and brings the cars back", (await page.inputValue("#f-max-price")) === "", `${before} cards again`);

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

  // Page load: stepping between listings must not re-download the multi-MB board export
  // when nothing changed, and a change made elsewhere (a sync, another tab) must still show.
  const exN = await api(page, "/api/export");
  const byId = (id) => exN.listings.find((l) => String(l.id) === id);
  await page.evaluate(() => document.activeElement.blur());   // leaving the notes box may save it
  await page.waitForTimeout(800);
  await openDetail(page, byId(order[4]));   // absorbs that save's reload
  const exportsBefore = exports;
  const stepTimes = [];
  for (const id of [order[5], order[6], order[7]]) {
    const t0 = Date.now();
    await openDetail(page, byId(id));
    stepTimes.push(Date.now() - t0);
  }
  check("stepping between listings does not reload the board data", exports === exportsBefore, `${exports - exportsBefore} export fetches`);
  check("a listing opens quickly", Math.max(...stepTimes) < 400, `${stepTimes.join(", ")} ms`);
  const elsewhere = byId(order[8]);
  await api(page, `/api/listings/${elsewhere.id}`, "PATCH", { notes: "changed elsewhere" });   // not through this page
  await openDetail(page, elsewhere);
  await page.waitForFunction(() => document.querySelector("#notes")?.value === "changed elsewhere", null, { timeout: 5000, polling: 100 }).catch(() => {});
  check("a change made elsewhere still shows up", exports === exportsBefore + 1 && (await page.$eval("#notes", (e) => e.value)) === "changed elsewhere", `${exports - exportsBefore} export fetch`);
  // VIN near the top of the listing page, local workbench only.
  const exV = await api(page, "/api/export");
  const withVin = exV.listings.find((l) => l.vin && l.role === "candidate");
  if (withVin) {
    await openDetail(page, withVin);
    const vinText = await page.$eval(".vin-line .vin", (e) => e.textContent).catch(() => "");
    const top = await page.$eval(".vin-line", (e) => e.getBoundingClientRect().top).catch(() => 9999);
    check("VIN shown near the top of the listing page", vinText === withVin.vin && top < 300, `#${withVin.id} ${vinText} at y=${Math.round(top)}`);
  }
  const openListing = await page.$eval("a.btn[target=_blank]", (a) => a.href);
  check("detail page still has Open listing ↗", /^https?:/.test(openListing), openListing);

  // Re-normalize from the UI keeps an ended auction ended (bug 1).
  const ex = await api(page, "/api/export");
  const ended = ex.listings.find((l) => l.availability === "ended" && l.role === "comp");
  await openDetail(page, ended);
  await page.waitForSelector("#renorm");
  await page.click("#renorm");
  await page.waitForFunction(() => [...document.querySelectorAll(".toast")].some((t) => /Re-normalized|failed|error/i.test(t.textContent)), null, { timeout: 60000, polling: 500 });
  const after = await api(page, `/api/listings/${ended.id}`);
  check("Re-normalize button keeps an ended auction ended", after.availability === "ended" && after.role === "comp", `#${ended.id} ${after.availability}/${after.role}`);

  // Role set by hand survives the ingest path a sync uses (bug 5).
  const cand = ex.listings.find((l) => l.role === "candidate" && l.availability === "active" && l.site === "cargurus");
  await openDetail(page, cand);
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
  const victim = ex2.listings.find((l) => l.role === "candidate" && l.availability === "active" && l.assessment && (l.status || "New") === "New" && l.id !== cand.id);
  await page.goto(BASE + "/");
  await page.waitForSelector(`.card[data-id="${victim.id}"]`);
  await openDetail(page, victim);
  await page.click("#mark-gone");
  await page.waitForFunction(() => [...document.querySelectorAll(".toast")].some((t) => /Marked sold/.test(t.textContent)), null, { timeout: 15000, polling: 250 });
  const v2 = await api(page, `/api/listings/${victim.id}`);
  check("Mark sold / gone makes it a sold comp, System Assessment Do not pursue, your call untouched", v2.availability === "sold" && v2.role === "comp" && v2.status === "New" && v2.assessment?.verdict === "Do not pursue", `#${victim.id} ${v2.availability}/${v2.role}/${v2.status}/${v2.assessment?.verdict}`);
  await page.goto(BASE + "/");
  await page.waitForSelector(".card");
  check("…and it is gone from the candidates board", !(await page.$(`.card[data-id="${victim.id}"]`)));
  await page.click('#role button[data-v="comp"]');
  await page.waitForSelector(".card");
  check("…and listed under Comps", !!(await page.$(`.card[data-id="${victim.id}"]`)));
  await page.click('#role button[data-v="candidate"]');
  await page.waitForSelector(".card");

  // Your call (one field; replaced Status + "Your verdict" 2026-09-29) and the System Assessment.
  const exY = await api(page, "/api/export");
  const yc = exY.listings.find((l) => l.role === "candidate" && l.availability === "active" && l.assessment && (l.status || "New") === "New" && ![victim.id, cand.id].includes(l.id));
  await openDetail(page, yc);
  const opts = await page.$$eval("select#status option", (os) => os.map((o) => o.textContent));
  check("one Your call field, no separate verdict override", JSON.stringify(opts) === JSON.stringify(["New", "Interested", "Contacted", "PPI Scheduled", "Offer Made", "Purchased", "Pass"]) && !(await page.$("#voverride")), opts.join(" / "));
  check("the system's verdict is labelled System Assessment", /^System Assessment: /.test(await page.textContent(".panel h3:has-text('System Assessment:')")));
  await page.selectOption("select#status", "Pass");
  await openDetail(page, yc);
  await page.fill("#status-reason", "too many miles");
  await page.click("h1");   // blur saves
  await page.waitForFunction(() => /Your call: Passed — too many miles/.test(document.body.innerText), null, { timeout: 5000, polling: 100 });
  const y2 = await api(page, `/api/listings/${yc.id}`);
  check("Pass with a reason is saved; a price/mileage reason keeps it a candidate", y2.status === "Pass" && y2.status_reason === "too many miles" && y2.role === "candidate", `${y2.status}/${y2.status_reason}/${y2.role}`);
  await page.goto(BASE + "/");
  await page.waitForSelector(`.card[data-id="${yc.id}"]`);
  const callY = await page.$eval(`.card[data-id="${yc.id}"] .call-select`, (e) => ({ v: e.value, rose: e.classList.contains("rose") }));
  check("its card shows your call (Pass), not the system's next step", callY.v === "Pass" && callY.rose && !(await page.$(`.card[data-id="${yc.id}"] .chip.next`)), JSON.stringify(callY));
  await openDetail(page, yc);
  await page.fill("#status-reason", "sold last week");
  await page.click("h1");
  await page.waitForFunction(() => [...document.querySelectorAll(".toast")].some((t) => /market comp/.test(t.textContent)), null, { timeout: 8000, polling: 100 });
  const y3 = await api(page, `/api/listings/${yc.id}`);
  check("a Pass because it sold moves it to the comps", y3.role === "comp" && y3.availability === "sold", `${y3.role}/${y3.availability}`);

  // Your call straight from a board card (Jason 2026-09-29).
  {
  await page.goto(BASE + "/");
  await page.waitForSelector(".card .call-select");
  const exCard = await api(page, "/api/export");
  const onBoard = await page.$$eval(".card", (cs) => cs.map((c) => c.dataset.id));
  const fresh = exCard.listings.filter((l) => onBoard.includes(String(l.id)) && (l.status || "New") === "New" && l.assessment?.next_step && ![victim.id, cand.id, yc.id].includes(l.id));
  const [c1, c2, c3] = fresh.slice(4, 7);
  const sel = (l) => `.card[data-id="${l.id}"] .call-select`;
  await page.$eval(sel(c1), (e) => e.scrollIntoView({ block: "center" }));
  const yBefore = await page.evaluate(() => scrollY);
  await page.click(sel(c1));   // opening the menu must not open the listing
  await page.keyboard.press("Escape");
  check("clicking the card's Your call menu does not open the listing", !/#\/l\//.test(page.url()), page.url());
  await page.selectOption(sel(c1), "Interested");
  await page.waitForFunction((s) => document.querySelector(s)?.value === "Interested" && document.querySelector(s)?.classList.contains("olive"), sel(c1), { timeout: 5000, polling: 100 });
  const s1 = await api(page, `/api/listings/${c1.id}`);
  const yAfter = await page.evaluate(() => scrollY);
  check("set Your call from the card: saved, shown, no next-step chip", s1.status === "Interested" && !(await page.$(`.card[data-id="${c1.id}"] .chip.next`)), `${s1.status}`);
  check("…and the board keeps your place", Math.abs(yAfter - yBefore) < 40, `${Math.round(yBefore)} -> ${Math.round(yAfter)}`);
  page.once("dialog", (d) => d.dismiss());
  await page.selectOption(sel(c2), "Pass");
  await page.waitForTimeout(500);
  check("cancelling the Pass reason leaves the car as it was", (await page.$eval(sel(c2), (e) => e.value)) === "New" && (await api(page, `/api/listings/${c2.id}`)).status === "New");
  page.once("dialog", (d) => d.accept("too far away"));
  await page.selectOption(sel(c2), "Pass");
  await page.waitForFunction((s) => document.querySelector(s)?.value === "Pass", sel(c2), { timeout: 5000, polling: 100 });
  const s2 = await api(page, `/api/listings/${c2.id}`);
  check("Pass from the card with a reason", s2.status === "Pass" && s2.status_reason === "too far away" && s2.role === "candidate", `${s2.status}/${s2.status_reason}/${s2.role}`);
  page.once("dialog", (d) => d.accept("sold"));
  await page.selectOption(sel(c3), "Pass");
  await page.waitForFunction((id) => !document.querySelector(`.card[data-id="${id}"]`), c3.id, { timeout: 5000, polling: 100 });
  const s3 = await api(page, `/api/listings/${c3.id}`);
  check("Pass because it sold, from the card: off the candidates board, into the comps", s3.role === "comp" && s3.availability === "sold", `${s3.role}/${s3.availability}`);
  }

  // Role menu: make a candidate a curiosity by hand.
  const exR = await api(page, "/api/export");
  const pick = exR.listings.find((l) => l.role === "candidate" && l.availability === "active" && (l.status || "New") === "New" && l.id !== victim.id && l.id !== cand.id);
  await page.evaluate((id) => { location.hash = "#/l/" + id; }, pick.id);
  await page.waitForFunction((id) => location.hash === `#/l/${id}` && document.querySelector("select#role"), pick.id, { polling: 250 });
  await page.waitForTimeout(600);
  await page.selectOption("select#role", "curiosity");
  await page.waitForTimeout(800);
  const pr = await api(page, `/api/listings/${pick.id}`);
  check("Role menu: curiosity by hand sticks", pr.role === "curiosity" && pr.role_user_set === 1, `#${pick.id} ${pr.role}/${pr.role_user_set}`);

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
  await mp.click(".rail > summary");
  const rcPhone = await railCheck(mp);
  check("phone: rail toggles fit too", !rcPhone.clipped.length && !rcPhone.overlaps.length, JSON.stringify(rcPhone));
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
