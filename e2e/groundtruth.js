// Independent ground truth: open each URL in plain headless Chromium (no extension,
// none of our code) and print what the top of the page says.
const { chromium } = require("playwright");
(async () => {
  const b = await chromium.launch({ channel: "chromium", headless: true });
  const p = await b.newPage();
  for (const u of process.argv.slice(2)) {
    try { await p.goto(u, { waitUntil: "domcontentloaded", timeout: 30000 }); await p.waitForTimeout(4000);
      const t = (await p.evaluate(() => (document.querySelector("main") || document.body).innerText)).replace(/\s+/g, " ").slice(0, 160);
      console.log(u.split("/").slice(-2).join("/"), "|", p.url() === u ? "" : "-> " + p.url(), "|", t);
    } catch (e) { console.log(u, "| ERROR", e.message.slice(0, 80)); }
  }
  await b.close();
})();
