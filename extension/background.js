// Service worker: orchestrates a sync. Opens each saved listing in a
// background tab, scrapes it via the content script, posts batches to the
// local server, and reports progress through chrome.storage.session.
const DEFAULT_API = "http://127.0.0.1:8765";
const BATCH = 8;
const MIN_GAP = 1800, MAX_GAP = 3800; // ms between listing loads (be polite)

let running = false;
let cancel = false;

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
    const resp = await sendToTab(tab.id, { type: "detail" });
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
  running = true; cancel = false;
  await chrome.storage.session.set({ log: [] });
  const totals = { created: 0, updated: 0, normalized: 0, comps: 0, candidates: 0, profiles_created: 0, skipped_sold: 0, errors: [] };
  try {
    await setProgress({ state: "collecting", done: 0, total: 0, message: "Scrolling the saved list…" });
    const ping = await sendToTab(tabId, { type: "ping" });
    if (!ping.ok) throw new Error("This tab has no Hoopty Scout adapter. Open a supported saved-listings page and reload it.");
    const site = ping.site;
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
  if (msg.type === "status") { sendResponse({ running }); return false; }
  return false;
});
