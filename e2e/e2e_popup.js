// E2E: the extension popup's status line while a sync is under way.
// Loads the real unpacked extension, marks a Bring a Trailer sync as running in
// the service worker's session state, opens popup.html and reads the status.
const { chromium } = require("playwright");
const path = require("path");
const fs = require("fs");
const SB = process.env.SANDBOX || __dirname;
const EXT = path.join(SB, "ext");
const results = {};
const check = (name, ok, detail = "") => { results[name] = { ok: !!ok, detail }; console.log(ok ? "PASS" : "FAIL", name, detail); };

(async () => {
  const ctx = await chromium.launchPersistentContext(path.join(SB, "profile-popup"), {
    channel: "chromium", headless: true,
    args: [`--disable-extensions-except=${EXT}`, `--load-extension=${EXT}`],
  });
  let [sw] = ctx.serviceWorkers();
  if (!sw) sw = await ctx.waitForEvent("serviceworker", { timeout: 15000 });
  const extId = new URL(sw.url()).host;
  await sw.evaluate(async () => {
    await chrome.storage.session.set({
      syncState: { running: true, startedAt: Date.now(), beat: Date.now(), kind: "sync", tabIds: [] },
      progress: { state: "scraping", done: 3, total: 30, message: "Scraping 30 listing(s)…" },
    });
  });
  // The popup asks the worker for status; the worker reports the site it is syncing.
  await sw.evaluate(() => { runningInfo.site = "bat"; runningInfo.kind = "sync"; });
  const page = await ctx.newPage();
  await page.goto(`chrome-extension://${extId}/popup.html`);
  await page.waitForFunction(() => /under way|running/i.test(document.querySelector("#status").textContent), null, { timeout: 10000, polling: 200 });
  const status = (await page.textContent("#status")).trim();
  const cls = await page.getAttribute("#status", "class");
  check("status names the site in full", /Bring a Trailer sync under way/.test(status), status);
  check("no \"still running\" / raw site key", !/still running|\bbat\b/i.test(status), status);
  check("shown as information, not a warning", /\binfo\b/.test(cls) && !/warning/.test(cls), cls);
  check("says the popup can be closed and how to stop", /close this popup/.test(status) && /Stop/.test(status));
  await ctx.close();
  fs.mkdirSync(path.join(SB, "out"), { recursive: true });
  fs.writeFileSync(path.join(SB, "out", "results-popup.json"), JSON.stringify(results, null, 2));
})().catch((e) => { console.error("ERROR", e); process.exit(1); });
