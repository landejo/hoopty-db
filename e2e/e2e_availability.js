// E2E: real Chromium + the unpacked extension + the sandbox server (:8766).
// Board source links, then the real "Check availability" button against live listing pages.
const { chromium } = require("playwright");
const path = require("path");
const SB = process.env.SANDBOX || __dirname;   // sandbox built by e2e/run.sh
const fs = require("fs");
const BASE = "http://127.0.0.1:8766";
const EXT = path.join(SB, "ext");
const OUT = path.join(SB, "out");
fs.mkdirSync(OUT, { recursive: true });
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);
const results = {};
const check = (name, ok, detail = "") => { results[name] = { ok: !!ok, detail }; log(ok ? "PASS" : "FAIL", name, detail); };

(async () => {
  const ctx = await chromium.launchPersistentContext(path.join(SB, "profile"), {
    channel: "chromium", headless: true, viewport: { width: 1400, height: 900 },
    args: [`--disable-extensions-except=${EXT}`, `--load-extension=${EXT}`],
  });
  let [sw] = ctx.serviceWorkers();
  if (!sw) sw = await ctx.waitForEvent("serviceworker", { timeout: 15000 });
  check("extension service worker loaded", !!sw, sw.url());

  const page = await ctx.newPage();
  page.on("pageerror", (e) => log("pageerror", e.message));
  await page.goto(BASE + "/");
  await page.waitForSelector(".card");
  check("brand renamed", (await page.textContent(".brand")).includes("Hoopty-Matic"));
  const ext = await page.evaluate(() => document.documentElement.dataset.scoutExtension);
  const want = JSON.parse(fs.readFileSync(path.join(EXT, "manifest.json"), "utf8")).version;
  check("workbench bridge present", ext === want, `${ext} (manifest ${want})`);

  // 1. Source link on every card, opening the listing in a new tab.
  const cards = await page.$$eval(".card", (els) => els.map((c) => {
    const a = c.querySelector("a.src"); return { id: c.dataset.id, href: a && a.href, target: a && a.target };
  }));
  const missing = cards.filter((c) => !c.href || c.target !== "_blank");
  check("every card has a new-tab source link", cards.length > 0 && missing.length === 0, `${cards.length} cards, ${missing.length} without`);
  const first = await page.$(".card a.src");
  const expected = await first.getAttribute("href");
  const [popup] = await Promise.all([ctx.waitForEvent("page", { timeout: 15000 }), first.click()]);
  await popup.waitForLoadState("domcontentloaded").catch(() => {});
  check("source link opened a new tab at the listing URL", popup.url().split("?")[0].replace(/\/$/, "").startsWith(expected.replace(/\/$/, "").split("?")[0]) || popup.url().includes(new URL(expected).hostname), `${expected} -> ${popup.url()}`);
  check("board did not navigate away", page.url() === BASE + "/" || page.url().endsWith("#/"), page.url());
  await popup.close();
  await page.click('#view button[data-v="table"]');
  const tableLinks = await page.$$eval("table.data a.src", (as) => as.filter((a) => a.target === "_blank").length);
  check("table view has source links", tableLinks > 0, String(tableLinks));
  await page.click('#view button[data-v="cards"]');

  // 2. Availability check from the board button, through the real extension.
  const before = await (await page.request.get(BASE + "/api/export")).json();
  fs.writeFileSync(path.join(OUT, "before.json"), JSON.stringify(before.listings.map((l) => ({ id: l.id, site: l.site, url: l.url, role: l.role, availability: l.availability, status: l.status }))));
  await page.click("#check-avail");
  const t0 = Date.now();
  let task = null;
  const done = page.waitForFunction(() => [...document.querySelectorAll(".toast")].some((t) => /Availability/.test(t.textContent)), null, { timeout: 40 * 60 * 1000, polling: 2000 });
  const ticker = setInterval(async () => {
    try { task = await (await page.request.get(BASE + "/api/task")).json(); log("task", task.done + "/" + task.total, (task.current || "").slice(0, 60)); } catch (e) {}
  }, 30000);
  await done;
  clearInterval(ticker);
  const toast = await page.$$eval(".toast", (ts) => ts.map((t) => t.textContent).join(" | "));
  task = await (await page.request.get(BASE + "/api/task")).json();
  check("availability check finished from the board button", /Availability: \d+ checked/.test(toast), `${toast} · ${Math.round((Date.now() - t0) / 1000)}s · task: ${task.result}`);
  await page.screenshot({ path: path.join(OUT, "board-after-check.png") });
  await ctx.close();
  fs.writeFileSync(path.join(OUT, "results-availability.json"), JSON.stringify(results, null, 2));
  log("DONE", Object.values(results).filter((r) => !r.ok).length, "failures");
})().catch((e) => { console.error("ERROR", e); process.exit(1); });
