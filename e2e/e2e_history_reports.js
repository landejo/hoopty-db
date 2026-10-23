// E2E: free history-report links and report capture in the extension's content scripts.
// Loads the real adapters into a page built from the markup Autotrader and CARFAX used
// on 2026-10-23 (trimmed), with a stub chrome.runtime, and checks:
//  - autotrader.js / carfax.js report a free report link and skip a buy-only one;
//  - document.js reads Autotrader's AutoCheck report from its iframe[srcdoc], waiting for it.
const { chromium } = require("playwright");
const path = require("path");
const fs = require("fs");
const SB = process.env.SANDBOX || __dirname;
const EXT = path.join(__dirname, "..", "extension");
const results = {};
const check = (name, ok, detail = "") => { results[name] = { ok: !!ok, detail }; console.log(ok ? "PASS" : "FAIL", name, detail); };

// The stub keeps each content script's onMessage listener so the test can message it.
const STUB = `window.__listeners = []; window.chrome = { runtime: { onMessage: { addListener: (f) => window.__listeners.push(f) } } };
  window.__send = (msg) => new Promise((resolve) => { for (const f of window.__listeners) if (f(msg, {}, resolve)) return; resolve(null); });`;
const REPORT_LINES = Array.from({ length: 60 }, (_, i) => `<p>Event ${i}: Registration Event/Renewal</p>`).join("");

async function load(page, html, scripts) {
  await page.setContent(html);
  await page.addScriptTag({ content: STUB });
  for (const s of scripts) await page.addScriptTag({ path: path.join(EXT, s) });
}

(async () => {
  const browser = await chromium.launch({ channel: "chromium", headless: true });
  const page = await browser.newPage();

  // Autotrader, free AutoCheck (#648): the link goes to the listing's /experian route.
  await load(page, `<main><h1>Used 2001 BMW Z3 3.0i</h1><p>${"x ".repeat(300)}</p>
    <a href="https://www.autotrader.com/cars-for-sale/vehicle/793102614/experian?ps=false&make=BMW&encryptedVin=abc&brand=atc">Vehicle History Report</a>
    <button>View the Free Vehicle History Report</button></main>`, ["common.js", "adapters/autotrader.js"]);
  let d = await page.evaluate(() => window.__scoutAdapter.scrapeDetail());
  check("autotrader: free AutoCheck link found", d.history_report && d.history_report.kind === "autocheck" && /\/experian\?/.test(d.history_report.url), JSON.stringify(d.history_report));

  // Autotrader, buy-only (#645): the link goes to autocheck.com; nothing to fetch.
  await load(page, `<main><h1>Used 2002 BMW Z3 3.0i</h1><p>${"x ".repeat(300)}</p>
    <a href="https://www.autocheck.com/vehiclehistory/?vin=4USCN53462LL51050&retail=true&siteId=7101">Vehicle History Report</a>
    <button>Buy a Vehicle History Report</button></main>`, ["common.js", "adapters/autotrader.js"]);
  d = await page.evaluate(() => window.__scoutAdapter.scrapeDetail());
  check("autotrader: buy-only report skipped", d.history_report === null, JSON.stringify(d.history_report));

  // CARFAX listing (#637): "View FREE CARFAX Report" -> /vehiclehistory/ccl/<token>.
  await load(page, `<main><h1>Used 2000 Toyota MR2 Spyder Base</h1><p>${"x ".repeat(300)}</p>
    <a href="https://www.carfax.com/vehicle-history-reports/">CARFAX Reports</a>
    <a href="https://www.carfax.com/vehiclehistory/ccl/Sej5ap4JfR3q">View FREE CARFAX Report</a></main>`, ["common.js", "adapters/carfax.js"]);
  d = await page.evaluate(() => window.__scoutAdapter.scrapeDetail());
  check("carfax: free report link found", d.history_report && d.history_report.kind === "carfax" && /\/vehiclehistory\/ccl\//.test(d.history_report.url), JSON.stringify(d.history_report));

  // CARFAX listing without a free report: only the generic "CARFAX Reports" nav link.
  await load(page, `<main><h1>Used 2018 Mazda Miata RF Club</h1><p>${"x ".repeat(300)}</p>
    <a href="https://www.carfax.com/vehicle-history-reports/">CARFAX Reports</a></main>`, ["common.js", "adapters/carfax.js"]);
  d = await page.evaluate(() => window.__scoutAdapter.scrapeDetail());
  check("carfax: no free link, nothing reported", d.history_report === null, JSON.stringify(d.history_report));

  // Autotrader's report page: <main> is empty chrome, the report arrives late in an iframe[srcdoc].
  await load(page, `<main><nav>Shop for a Car Used Cars for Sale</nav></main>`, ["common.js", "adapters/document.js"]);
  await page.evaluate((lines) => setTimeout(() => {
    const f = document.createElement("iframe");
    f.srcdoc = `<body><h1>Experian AutoCheck Report</h1><p>VIN: WBACN53401LJ58815</p>${lines}</body>`;
    document.body.appendChild(f);
  }, 1500), REPORT_LINES);
  let r = await page.evaluate(() => window.__send({ type: "capture_document", kind: "autocheck", waitMs: 8000 }));
  check("document: reads the report inside iframe[srcdoc]", r && r.ok && /WBACN53401LJ58815/.test(r.text) && r.text.length >= 800, r && `${r.text.length} chars`);
  check("document: keeps the kind it was asked for", r && r.kind === "autocheck", r && r.kind);

  // A plain CARFAX report page: the text is in <main>, and the kind is guessed as before.
  await load(page, `<title>CARFAX Vehicle History Report</title><main><h1>CARFAX Vehicle History Report</h1>
    <p>VIN: JTDFR3209Y0019866</p>${REPORT_LINES}</main>`, ["common.js", "adapters/document.js"]);
  r = await page.evaluate(() => window.__send({ type: "capture_document" }));
  check("document: main-text report still captured", r && r.ok && /JTDFR3209Y0019866/.test(r.text), r && `${r.text.length} chars`);
  check("document: kind still guessed without a hint", r && r.kind === "carfax", r && r.kind);

  await browser.close();
  fs.mkdirSync(path.join(SB, "out"), { recursive: true });
  fs.writeFileSync(path.join(SB, "out", "results-reports.json"), JSON.stringify(results, null, 2));
  if (Object.values(results).some((x) => !x.ok)) process.exit(1);
})().catch((e) => { console.error("ERROR", e); process.exit(1); });
