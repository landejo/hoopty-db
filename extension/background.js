// Service worker: orchestrates a sync. Opens each saved listing in a
// background tab, scrapes it via the content script, posts batches to the
// local server, and reports progress through chrome.storage.session.
const DEFAULT_API = "http://127.0.0.1:8765";
const BATCH = 8;
const MIN_GAP = 1800, MAX_GAP = 3800; // ms between listing loads (be polite)

let running = false;
let cancel = false;
let runningInfo = { site: "", kind: "" };

async function getApi() {
  const { api_base } = await chrome.storage.local.get("api_base");
  return api_base || DEFAULT_API;
}

async function setProgress(p) {
  const { progress } = await chrome.storage.session.get("progress");
  await chrome.storage.session.set({ progress: Object.assign({}, progress || {}, p, { ts: Date.now() }) });
}

function log(line) {
  chrome.storage.session.get("log").then(({ log }) => {
    const arr = (log || []).concat([`${new Date().toLocaleTimeString()} ${line}`]).slice(-80);
    chrome.storage.session.set({ log: arr });
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const jitter = () => MIN_GAP + Math.random() * (MAX_GAP - MIN_GAP);

function sendToTab(tabId, msg, tries = 6) {
  return new Promise((resolve) => {
    const attempt = (n) => {
      chrome.tabs.sendMessage(tabId, msg, (resp) => {
        if (chrome.runtime.lastError || !resp) {
          if (n > 0) return setTimeout(() => attempt(n - 1), 1000);
          return resolve({ ok: false, error: chrome.runtime.lastError ? chrome.runtime.lastError.message : "no response" });
        }
        resolve(resp);
      });
    };
    attempt(tries);
  });
}

function waitForLoad(tabId, timeoutMs = 30000) {
  return new Promise((resolve) => {
    let done = false;
    const finish = () => { if (!done) { done = true; chrome.tabs.onUpdated.removeListener(h); resolve(); } };
    const h = (id, info) => { if (id === tabId && info.status === "complete") finish(); };
    chrome.tabs.onUpdated.addListener(h);
    setTimeout(finish, timeoutMs);
  });
}

async function scrapeUrl(url) {
  const tab = await chrome.tabs.create({ url, active: false });
  try {
    await waitForLoad(tab.id);
    await sleep(1500);
    let resp = await sendToTab(tab.id, { type: "detail", waitMs: 12000 });
    if (resp.ok && resp.detail && resp.detail.blocked) {
      // Bot walls often only clear while the tab is visible. Foreground it briefly.
      log(`bot wall on ${url}; foregrounding the tab for a moment`);
      try { await chrome.tabs.update(tab.id, { active: true }); } catch (e) {}
      await sleep(4000);
      resp = await sendToTab(tab.id, { type: "detail", waitMs: 15000 });
      if (resp.ok && resp.detail && !resp.detail.blocked) log(`cleared: ${url}`);
    }
    return resp.ok ? resp.detail : { error: resp.error };
  } finally {
    try { await chrome.tabs.remove(tab.id); } catch (e) {}
  }
}

async function post(path, body) {
  const api = await getApi();
  const r = await fetch(api + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const text = await r.text();
  let json = null;
  try { json = JSON.parse(text); } catch (e) {}
  if (!r.ok) throw new Error(`HTTP ${r.status}: ${text.slice(0, 300)}`);
  return json;
}

async function runSync({ tabId, includeSold, scrapeDetails, onlyNew }) {
  if (running) return { ok: false, error: "A sync is already running." };
  running = true; cancel = false; runningInfo = { site: "", kind: "sync" };
  await chrome.storage.session.set({ log: [] });
  const totals = { created: 0, updated: 0, normalized: 0, comps: 0, candidates: 0, profiles_created: 0, skipped_sold: 0, errors: [] };
  try {
    await setProgress({ state: "collecting", done: 0, total: 0, message: "Scrolling the saved list…" });
    const ping = await sendToTab(tabId, { type: "ping" });
    if (!ping.ok) throw new Error("This tab has no Hoopty Scout adapter. Open a supported saved-listings page and reload it.");
    const site = ping.site;
    runningInfo.site = site;
    const col = await sendToTab(tabId, { type: "collect" });
    if (!col.ok) throw new Error("Collect failed: " + col.error);
    let items = col.items || [];
    const allUrls = items.map((i) => i.url);
    log(`${site}: found ${items.length} saved listing(s)`);
    if (!includeSold) {
      const before = items.length;
      items = items.filter((i) => !i.sold && !i.ended);
      totals.skipped_sold = before - items.length;
    }
    // Ask the server what it already has for this site: lets us skip re-scraping
    // comps, and find listings that vanished from the saved page (ended auctions,
    // sold cars) so their final result can be captured from the listing page.
    let vanished = [];
    try {
      const api = await getApi();
      const r = await fetch(api + "/api/export");
      const data = await r.json();
      const known = new Map((data.listings || []).map((l) => [l.url, l]));
      if (onlyNew) items = items.map((i) => Object.assign(i, { _known: known.get(i.url) }));
      const present = new Set(allUrls);
      vanished = (data.listings || []).filter((l) => l.site === site && l.availability === "active" && !present.has(l.url))
        .map((l) => ({ site, url: l.url, title: l.title, _vanished: true }));
      if (vanished.length) log(`${vanished.length} previously active listing(s) are gone from the saved page; checking their pages.`);
    } catch (e) { log("Could not fetch known listings; scraping everything."); }
    if (scrapeDetails) items = items.concat(vanished);
    await setProgress({ state: "scraping", done: 0, total: items.length, message: `Scraping ${items.length} listing(s)…` });

    let batch = [];
    const flush = async () => {
      if (!batch.length) return;
      const res = await post("/api/ingest", { site, items: batch, include_sold: includeSold, full_sync: false });
      for (const k of Object.keys(totals)) if (typeof res[k] === "number") totals[k] += res[k];
      if (Array.isArray(res.errors)) totals.errors.push(...res.errors);
      log(`Server: +${res.created} new, ${res.updated} updated, ${res.normalized} normalized` + (res.profiles_created ? `, ${res.profiles_created} new profile(s)` : ""));
      batch = [];
    };

    for (let i = 0; i < items.length; i++) {
      if (cancel) { log("Cancelled."); break; }
      const it = items[i];
      const known = it._known; delete it._known;
      const skipScrape = !scrapeDetails || (known && known.role === "comp");
      if (!skipScrape) {
        try {
          it.detail = await scrapeUrl(it.url);
          if (it.detail && it.detail.error) log(`! ${it.title || it.url}: ${it.detail.error}`);
          else if (it.detail && it.detail.blocked) { log(`! blocked by a bot wall: ${it.title || it.url} (re-run the sync later)`); totals.errors.push(`blocked: ${it.url}`); }
        } catch (e) { log(`! ${it.url}: ${e.message}`); }
        await sleep(jitter());
      }
      batch.push(it);
      await setProgress({ done: i + 1, message: `${i + 1}/${items.length} · ${it.title || it.url}` });
      if (batch.length >= BATCH) await flush();
    }
    await flush();
    if (!cancel && allUrls.length) {
      // Final pass: the complete URL list lets the server mark listings that
      // vanished from the saved page as removed (no details, nothing re-scraped).
      try { await post("/api/ingest", { site, items: allUrls.map((url) => ({ url, _touch: true })), include_sold: includeSold, full_sync: true }); }
      catch (e) { log("Removal pass failed: " + e.message); }
    }
    await setProgress({ state: "done", message: "Sync complete.", totals });
    log(`Done. ${totals.candidates} candidate(s), ${totals.comps} comp(s).`);
    return { ok: true, totals };
  } catch (e) {
    await setProgress({ state: "error", message: e.message });
    log("Error: " + e.message);
    return { ok: false, error: e.message };
  } finally {
    running = false;
  }
}

async function addCurrent({ tabId, url }) {
  const ping = await sendToTab(tabId, { type: "ping" });
  if (!ping.ok || !ping.detail) return { ok: false, error: "Not on a supported listing page." };
  const resp = await sendToTab(tabId, { type: "detail" });
  if (!resp.ok) return { ok: false, error: resp.error };
  const d = resp.detail;
  const item = { site: ping.site, url: url.split("?")[0], title: d.title, price_text: (d.bid_text || ""), card_text: d.status_text, detail: d,
                 sold: /\bsold\b/i.test(d.status_text.slice(0, 200)), ended: /\bbid to\b/i.test(d.status_text.slice(0, 200)) };
  try {
    const res = await post("/api/ingest", { site: ping.site, items: [item], include_sold: true });
    return { ok: true, res };
  } catch (e) { return { ok: false, error: e.message }; }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === "sync") { runSync(msg).then(sendResponse); return true; }
  if (msg.type === "add_current") { addCurrent(msg).then(sendResponse); return true; }
  if (msg.type === "cancel") { cancel = true; sendResponse({ ok: true }); return false; }
  if (msg.type === "status") { sendResponse({ running, site: runningInfo.site, kind: runningInfo.kind }); return false; }
  return false;
});

// ---------------- Provenance investigations ----------------
// A job (queued from the workbench) carries the query set. We run each query
// in a background tab in the user's own browser, collect result links and the
// visible results text, deepen hits that point at known listing pages, post
// everything to the server, then ask the server to interpret.
const enc = encodeURIComponent;
const ENGINE_URLS = {
  duckduckgo: (q) => `https://html.duckduckgo.com/html/?q=${enc(q)}`,
  bing: (q) => `https://www.bing.com/search?q=${enc(q)}`,
  google: (q) => `https://www.google.com/search?q=${enc(q)}`,
  bat: (q) => `https://bringatrailer.com/?s=${enc(q)}`,
  ebay_sold: (q) => `https://www.ebay.com/sch/i.html?_nkw=${enc(q)}&LH_Sold=1&LH_Complete=1`,
  classic: (q) => `https://www.classic.com/search/?q=${enc(q)}`,
  reddit: (q) => `https://www.reddit.com/search/?q=${enc(q)}`,
  facebook_posts: (q) => `https://www.facebook.com/search/posts/?q=${enc(q)}`,
  facebook_marketplace: (q) => `https://www.facebook.com/marketplace/search/?query=${enc(q)}`,
};
const DETAIL_PATTERNS = [/facebook\.com\/marketplace\/item\/\d+/, /cargurus\.com\/details\/\d+/, /cars\.com\/vehicledetail\//,
  /autotrader\.com\/cars-for-sale\/vehicle\/\d+/, /carsandbids\.com\/auctions\//, /bringatrailer\.com\/listing\//];
const MAX_DEEPEN = 8;

async function searchPage(url, settleMs) {
  const tab = await chrome.tabs.create({ url, active: false });
  try {
    await waitForLoad(tab.id);
    const resp = await sendToTab(tab.id, { type: "search_results", settleMs }, 8);
    return resp.ok ? resp : { results: [], error: resp.error };
  } finally { try { await chrome.tabs.remove(tab.id); } catch (e) {} }
}

async function runInvestigation(job) {
  const api = await getApi();
  const progress = async (p) => chrome.storage.session.set({ investigation: Object.assign({ jobId: job.id, listing: job.listing_title }, p, { ts: Date.now() }) });
  const hits = new Map();
  const add = (h) => { if (h.url && !hits.has(h.url)) hits.set(h.url, h); };
  try {
    await progress({ state: "searching", done: 0, total: job.queries.length });
    for (let i = 0; i < job.queries.length; i++) {
      if (cancel) throw new Error("cancelled");
      const q = job.queries[i];
      const build = ENGINE_URLS[q.engine];
      if (!build) continue;
      const settle = /facebook|reddit|bat/.test(q.engine) ? 3500 : 1500;
      let r;
      try { r = await searchPage(build(q.q), settle); } catch (e) { r = { results: [], error: e.message }; }
      for (const res of r.results || []) add({ engine: q.engine, query: q.q, url: res.url, title: res.title, snippet: res.snippet });
      if (r.page_text) add({ engine: q.engine, query: q.q, url: r.page_url || build(q.q), title: `(${q.engine} results page)`, snippet: r.page_text });
      log(`${q.engine}: ${(r.results || []).length} result(s) for ${q.q}`);
      await progress({ done: i + 1, message: `${q.engine} · ${q.q}` });
      await sleep(jitter());
    }
    // Deepen: open the listing pages we know how to read.
    const deep = Array.from(hits.values()).filter((h) => DETAIL_PATTERNS.some((p) => p.test(h.url)) && h.url !== job.listing_url).slice(0, MAX_DEEPEN);
    await progress({ state: "deepening", done: 0, total: deep.length });
    for (let i = 0; i < deep.length; i++) {
      if (cancel) throw new Error("cancelled");
      try { const d = await scrapeUrl(deep[i].url); if (d && d.text) deep[i].detail_text = d.text.slice(0, 20000); } catch (e) {}
      await progress({ done: i + 1, message: deep[i].title || deep[i].url });
      await sleep(jitter());
    }
    const all = Array.from(hits.values());
    for (let i = 0; i < all.length; i += 25) await post(`/api/provenance/jobs/${job.id}/hits`, { hits: all.slice(i, i + 25) });
    await progress({ state: "interpreting", message: `Server is classifying ${all.length} hit(s)…` });
    const res = await post(`/api/provenance/jobs/${job.id}/complete`, {});
    await progress({ state: "done", message: res.summary || "Provenance stored.", result: { flags: res.flags, available: res.available } });
    log(`Investigation done: ${all.length} hit(s); flags ${JSON.stringify(res.flags || [])}`);
    return { ok: true, hits: all.length };
  } catch (e) {
    try { await post(`/api/provenance/jobs/${job.id}/fail`, { error: e.message }); } catch (e2) {}
    await progress({ state: "error", message: e.message });
    return { ok: false, error: e.message };
  }
}

async function runQueuedInvestigations() {
  if (running) return { ok: false, error: "A sync is already running." };
  running = true; cancel = false; runningInfo = { site: "", kind: "investigation" };
  try {
    const api = await getApi();
    const jobs = await (await fetch(api + "/api/provenance/jobs?status=queued")).json();
    let done = 0;
    for (const job of jobs) { const r = await runInvestigation(job); if (r.ok) done++; if (cancel) break; }
    return { ok: true, done, total: jobs.length };
  } catch (e) { return { ok: false, error: e.message }; } finally { running = false; }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === "investigate") { runQueuedInvestigations().then(sendResponse); return true; }
  return false;
});
