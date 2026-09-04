const $ = (id) => document.getElementById(id);
const DEFAULT_API = "http://127.0.0.1:8765";
let tab = null, page = null, apiBase = DEFAULT_API;

function setStatus(msg, kind = "info") { $("status").className = "status " + kind; $("status").textContent = msg; }

async function refreshProgress() {
  const { progress, log } = await chrome.storage.session.get(["progress", "log"]);
  if (log) $("log").textContent = log.join("\n");
  if (!progress) return;
  const running = progress.state === "scraping" || progress.state === "collecting";
  $("progress").hidden = false;
  $("cancel").hidden = !running;
  $("sync").disabled = running || !(page && page.saved);
  const pct = progress.total ? Math.round(100 * progress.done / progress.total) : (progress.state === "done" ? 100 : 5);
  $("bar-fill").style.width = pct + "%";
  $("progress-msg").textContent = progress.message || "";
  if (progress.state === "done" && progress.totals) {
    const t = progress.totals;
    setStatus(`Synced: ${t.candidates} candidate(s), ${t.comps} comp(s), ${t.normalized} normalized` + (t.errors.length ? `, ${t.errors.length} error(s)` : ""), t.errors.length ? "warning" : "success");
  } else if (progress.state === "error") setStatus(progress.message, "error");
}

async function init() {
  const stored = await chrome.storage.local.get("api_base");
  apiBase = stored.api_base || DEFAULT_API;
  $("api-base").textContent = apiBase;
  [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) return setStatus("No active tab.", "error");

  let health = null;
  try { health = await (await fetch(apiBase + "/api/health")).json(); } catch (e) {}
  if (!health) setStatus(`Workbench not running at ${apiBase}. Start it with: .venv/bin/python run.py`, "error");

  const resp = await new Promise((res) => chrome.tabs.sendMessage(tab.id, { type: "ping" }, (r) => res(chrome.runtime.lastError ? null : r)));
  page = resp && resp.ok ? resp : null;
  if (!page) {
    $("site-label").textContent = "Unsupported page";
    if (health) setStatus("Open a saved-listings page on Facebook Marketplace, CarGurus, Cars.com, Cars & Bids, or Bring a Trailer (reload it if you just installed the extension).", "warning");
  } else {
    $("site-label").textContent = `${page.site} · ${page.saved ? "saved list" : page.detail ? "listing page" : "other page"}`;
    if (health) {
      if (page.saved) setStatus(health.ai ? "Ready to sync this saved list." : "Ready to sync (AI disabled: no API key on the server).", health.ai ? "success" : "warning");
      else if (page.detail) setStatus("On a listing page. You can add just this one.", "info");
      else setStatus("Navigate to your saved listings on this site to sync.", "info");
    }
    $("sync").disabled = !(health && page.saved);
    $("add").disabled = !(health && page.detail);
  }
  await refreshProgress();
}

$("sync").addEventListener("click", () => {
  $("sync").disabled = true; $("cancel").hidden = false; $("progress").hidden = false;
  setStatus("Syncing… you can close this popup; progress continues in the background.", "info");
  chrome.runtime.sendMessage({ type: "sync", tabId: tab.id, includeSold: $("include-sold").checked, scrapeDetails: $("scrape-details").checked, onlyNew: $("only-new").checked }, () => refreshProgress());
});
$("add").addEventListener("click", () => {
  $("add").disabled = true; setStatus("Scraping this listing…");
  chrome.runtime.sendMessage({ type: "add_current", tabId: tab.id, url: tab.url }, (r) => {
    if (r && r.ok) setStatus(`Added. ${r.res.normalized ? "Normalized with AI." : ""}`, "success");
    else setStatus("Failed: " + (r ? r.error : "no response"), "error");
    $("add").disabled = false;
  });
});
$("cancel").addEventListener("click", () => chrome.runtime.sendMessage({ type: "cancel" }));
$("open").addEventListener("click", () => chrome.tabs.create({ url: apiBase }));
chrome.storage.onChanged.addListener(refreshProgress);
init();
