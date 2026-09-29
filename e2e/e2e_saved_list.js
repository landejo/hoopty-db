// E2E: the Facebook saved-list collector against a grid that behaves like
// Facebook's (2026-09-28): cards load in pages as you scroll, and only ~26 cards
// near the viewport stay mounted. The real adapter code (common.js + facebook.js)
// runs in Chromium; every saved card, top and bottom, must be collected.
const { chromium } = require("playwright");
const path = require("path");
const fs = require("fs");
const SB = process.env.SANDBOX || __dirname;
const EXT = fs.existsSync(path.join(SB, "ext")) ? path.join(SB, "ext") : path.join(__dirname, "..", "extension");
const results = {};
const check = (name, ok, detail = "") => { results[name] = { ok: !!ok, detail }; console.log(ok ? "PASS" : "FAIL", name, detail); };

const TOTAL = 76, PAGE = 12, WINDOW = 26, ROW_H = 330;
const PAGE_HTML = `<!doctype html><html><body style="margin:0"><div role="main" id="grid" style="position:relative"></div><script>
  const TOTAL=${TOTAL}, PAGE=${PAGE}, WINDOW=${WINDOW}, ROW_H=${ROW_H};
  let loaded = PAGE;
  const grid = document.getElementById("grid");
  function render() {
    grid.style.height = (loaded * ROW_H + 400) + "px";
    const first = Math.max(0, Math.floor(scrollY / ROW_H) - Math.floor(WINDOW / 2));
    grid.innerHTML = "";
    for (let i = first; i < Math.min(loaded, first + WINDOW); i++) {
      const d = document.createElement("div");
      d.style.cssText = "position:absolute;left:0;top:" + (i * ROW_H) + "px;height:300px";
      d.innerHTML = '<a href="https://www.facebook.com/marketplace/item/' + (9000 + i) + '/?ref=saved">$' + (10000 + i) + '<br>200' + (i % 10) + ' Car number ' + i + '<br>Somewhere, CA</a>';
      grid.appendChild(d);
    }
  }
  addEventListener("scroll", () => {
    if (scrollY + innerHeight >= document.body.scrollHeight - 200 && loaded < TOTAL) setTimeout(() => { loaded = Math.min(TOTAL, loaded + PAGE); render(); }, 400);
    render();
  });
  render();
</script></body></html>`;

(async () => {
  const browser = await chromium.launch({ channel: "chromium", headless: true });
  const page = await browser.newPage({ viewport: { width: 1200, height: 900 } });
  await page.route("https://www.facebook.com/marketplace/you/saved", (r) => r.fulfill({ contentType: "text/html", body: PAGE_HTML }));
  await page.goto("https://www.facebook.com/marketplace/you/saved");
  await page.addScriptTag({ path: path.join(EXT, "common.js") });
  await page.addScriptTag({ path: path.join(EXT, "adapters", "facebook.js") });
  const ids = await page.evaluate(async () => (await window.__scoutAdapter.collectSaved()).map((i) => i.site_id));
  const mountedAtEnd = await page.evaluate(() => document.querySelectorAll("a[href]").length);
  const want = Array.from({ length: TOTAL }, (_, i) => String(9000 + i));
  const missing = want.filter((id) => !ids.includes(id));
  check("page really unmounts cards (the bug's precondition)", mountedAtEnd <= WINDOW, `${mountedAtEnd} mounted`);
  check("newest saves at the top are collected", ids.includes("9000") && ids.includes("9005"));
  check("every saved card is collected", missing.length === 0, `${ids.length}/${TOTAL}; missing ${missing.slice(0, 8).join(",")}`);
  check("no duplicates", new Set(ids).size === ids.length);
  await browser.close();
  fs.mkdirSync(path.join(SB, "out"), { recursive: true });
  fs.writeFileSync(path.join(SB, "out", "results-saved-list.json"), JSON.stringify(results, null, 2));
  if (Object.values(results).some((r) => !r.ok)) process.exit(1);
})().catch((e) => { console.error("ERROR", e); process.exit(1); });
