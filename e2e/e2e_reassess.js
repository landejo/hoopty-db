// E2E: tiered "Re-assess" button -> server -> real Anthropic SDK -> fake Messages API
// (fake_anthropic.py) -> parse -> policy engine -> DB -> board. Then the 3-day restart.
const { chromium } = require("playwright");
const path = require("path");
const SB = process.env.SANDBOX || __dirname;   // sandbox built by e2e/run.sh
const fs = require("fs");
const { execSync } = require("child_process");
const BASE = "http://127.0.0.1:8766";
const DIR = SB;
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);
const results = {};
const check = (name, ok, detail = "") => { results[name] = { ok: !!ok, detail }; log(ok ? "PASS" : "FAIL", name, detail); };
const requests = () => fs.existsSync(path.join(DIR, "fake_requests.jsonl")) ? fs.readFileSync(path.join(DIR, "fake_requests.jsonl"), "utf8").trim().split("\n").filter(Boolean).map(JSON.parse) : [];

async function pressTier(page) {
  let dialog = "";
  page.once("dialog", async (d) => { dialog = d.message(); await d.accept(); });
  const n0 = requests().length;
  await page.evaluate(() => document.querySelectorAll(".toast").forEach((t) => t.remove()));   // last tier's toast
  await page.click("#reassess-top");
  await page.waitForFunction(() => [...document.querySelectorAll(".toast")].some((t) => /Tier \d+:|Re-assess failed/.test(t.textContent)), null, { timeout: 15 * 60 * 1000, polling: 1000 });
  const toast = await page.$$eval(".toast", (ts) => ts.map((t) => t.textContent).join(" | "));
  const listed = (dialog.match(/^\d+\. .*/gm) || []).map((l) => Number(l.split(".")[0]));
  await page.waitForSelector("#reassess-top:not([disabled])");
  await page.waitForTimeout(1500);
  return { dialog, toast, ranks: listed, newRequests: requests().slice(n0) };
}

(async () => {
  const browser = await chromium.launch({ channel: "chromium", headless: true });
  const page = await (await browser.newContext({ viewport: { width: 1400, height: 900 } })).newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(BASE + "/");
  await page.waitForSelector("#reassess-top");
  await page.waitForFunction(() => /tier 1/i.test(document.querySelector("#reassess-top")?.textContent), null, { polling: 250 });
  check("button starts at tier 1", true, await page.textContent("#reassess-top"));

  const t1 = await pressTier(page);
  check("tier 1 confirm lists board ranks 1-15", JSON.stringify(t1.ranks) === JSON.stringify([...Array(15)].map((_, i) => i + 1)), t1.ranks.join(","));
  check("tier 1 confirm shows cost and new-cycle note", /About \$\d+\.\d\d total/.test(t1.dialog) && /starts a new cycle/.test(t1.dialog));
  check("tier 1 sent 15 calls as claude-opus-5-5", t1.newRequests.length === 15 && t1.newRequests.every((r) => r.model === "claude-opus-5-5" && r.stream), `${t1.newRequests.length} calls: ${[...new Set(t1.newRequests.map((r) => r.model))]}`);
  check("tier 1 toast", /Tier 1: 15 re-assessed on Opus 5\.5/.test(t1.toast), t1.toast);
  const ex = await (await page.request.get(BASE + "/api/export")).json();
  const cyc = await (await page.request.get(BASE + "/api/reassess/cycle")).json();
  const stored = ex.listings.filter((l) => cyc.done.includes(l.id));
  const policy = (await (await page.request.get(BASE + "/api/health")).json()).policy_version;
  check(`15 assessments stored on Opus 5.5 under the current policy`, stored.length === 15 && stored.every((l) => l.assessment?.model === "claude-opus-5-5" && l.assessment?.policy_version === policy), `${stored.length} stored, policy ${policy}`);
  await page.waitForFunction(() => /tier 2/i.test(document.querySelector("#reassess-top")?.textContent), null, { polling: 250 });
  check("button now says tier 2", true, await page.textContent("#reassess-top"));

  const t2 = await pressTier(page);
  const overlap = t2.newRequests.length && (await (await page.request.get(BASE + "/api/reassess/cycle")).json()).done;
  check("tier 2 takes 15 cars not done in tier 1", t2.newRequests.length === 15 && new Set(overlap).size === 30, `ranks ${t2.ranks.join(",")}`);
  check("tier 2 toast", /Tier 2: 15 re-assessed/.test(t2.toast), t2.toast);

  // Age the cycle past 3 days: the next press restarts at tier 1.
  execSync(`sqlite3 -cmd ".timeout 10000" "${path.join(DIR, "scout.db")}" "update settings set value_json=json_set(value_json,'$.started_at','2026-09-20T00:00:00+00:00') where key='reassess_cycle'"`);
  await page.reload();
  await page.waitForFunction(() => /tier 1/i.test(document.querySelector("#reassess-top")?.textContent), null, { polling: 250 });
  check("after 3 days the button is back to tier 1", true, await page.textContent("#reassess-top"));
  const t3 = await pressTier(page);
  check("restarted cycle re-assesses the current top 15", t3.ranks[0] === 1 && t3.ranks.length === 15 && /Tier 1:/.test(t3.toast), `${t3.ranks.join(",")} · ${t3.toast}`);
  check("no page errors", errors.length === 0, errors.join(" | "));
  await page.screenshot({ path: path.join(DIR, "out", "board-after-reassess.png") });
  await browser.close();
  fs.writeFileSync(path.join(DIR, "out", "results-reassess.json"), JSON.stringify(results, null, 2));
  log("DONE", Object.values(results).filter((r) => !r.ok).length, "failures");
})().catch((e) => { console.error("ERROR", e); process.exit(1); });
