// E2E: a board opened while the server is still updating assessments to the
// current policy shows the banner, then refreshes itself when the update ends.
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");
const SB = process.env.SANDBOX || __dirname;
const results = {};
const check = (name, ok, detail = "") => { results[name] = { ok: !!ok, detail }; console.log(ok ? "PASS" : "FAIL", name, detail); };
(async () => {
  const b = await chromium.launch({ channel: "chromium", headless: true });
  const p = await b.newPage({ viewport: { width: 1400, height: 900 } });
  await p.goto("http://127.0.0.1:8766/");
  await p.waitForSelector(".card");
  const banner = await p.$eval("#taskbar", (t) => (t.hidden ? "" : t.textContent)).catch(() => "");
  const chips0 = await p.$$eval(".card .chip.next", (c) => c.length);
  let chips = chips0;
  for (let i = 0; i < 120 && chips === 0; i++) { await p.waitForTimeout(1000); chips = await p.$$eval(".card .chip.next", (c) => c.length); }
  check("banner shows the policy update while it runs", /Running: Updating .* to policy/.test(banner), banner.replace(/\s+/g, " ").slice(0, 90));
  check("board refreshes itself with next steps when the update ends (no reload)", chips0 === 0 && chips > 0, `${chips0} chips at load, ${chips} after`);
  await b.close();
  fs.writeFileSync(path.join(SB, "out", "results-startup-board.json"), JSON.stringify(results, null, 2));
})().catch((e) => { console.error("ERROR", e); process.exit(1); });
