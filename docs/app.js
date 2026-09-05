/* Hoopty Scout viewer. Vanilla JS, no build. Reads data/scout.json on GitHub
   Pages; talks to the local FastAPI server when served from it. */
(() => {
  const $ = (sel, el = document) => el.querySelector(sel);
  const h = (html) => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const money = (n) => (n == null ? "—" : "$" + Number(n).toLocaleString());
  const num = (n) => (n == null ? "—" : Number(n).toLocaleString());
  const STATUSES = ["New", "Pursue", "Verify", "Contacted", "PPI Scheduled", "Offer Made", "Pass", "Purchased"];

  const state = { data: null, local: false, filters: load("filters", { profiles: [], site: "", avail: "active", status: "", analyzed: false, sort: "score", role: "candidate", view: "cards", max_price: "", max_mileage: "", max_age: "", statuses: {} }),
                  q: "", compare: load("compare", []), theme: load("theme", null) };

  function load(k, d) { try { const v = localStorage.getItem("scout." + k); const out = v ? JSON.parse(v) : d; if (k === "filters" && out && !Array.isArray(out.profiles)) out.profiles = out.profile ? [out.profile] : []; if (k === "filters" && out && typeof out.statuses !== "object") out.statuses = out.status ? { [out.status]: "include" } : {}; return out; } catch (e) { return d; } }
  function save(k, v) { try { localStorage.setItem("scout." + k, JSON.stringify(v)); } catch (e) {} }

  // ---------- theme ----------
  function applyTheme() {
    const t = state.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    document.documentElement.dataset.theme = t;
  }
  $("#theme").onclick = () => { state.theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark"; save("theme", state.theme); applyTheme(); };
  applyTheme();

  // ---------- data ----------
  async function loadData() {
    try {
      const r = await fetch("/api/health", { cache: "no-store" });
      if (r.ok) { state.local = true; state.health = await r.json(); }
    } catch (e) {}
    const url = state.local ? "/api/export" : "data/scout.json?ts=" + Date.now();
    const r = await fetch(url, { cache: "no-store" });
    state.data = await r.json();
    state.byId = new Map(state.data.listings.map((l) => [l.id, l]));
    state.profiles = new Map(state.data.profiles.map((p) => [p.key, p]));
    const m = $("#mode");
    m.textContent = state.local ? "local · " + (state.health.ai ? "AI on" : "AI off") : "published " + ago(state.data.generated_at);
    m.className = "mode-pill" + (state.local ? " local" : "");
    $("#publish").hidden = !state.local;
  }
  async function api(path, method = "GET", body) {
    const r = await fetch(path, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || r.statusText);
    return j;
  }
  // ---------- running-task banner (local mode) ----------
  let taskTimer = null, taskDoneAt = 0;
  function fmtElapsed(iso) { const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000)); return s < 60 ? s + "s" : Math.floor(s / 60) + "m " + (s % 60) + "s"; }
  function renderTask(t) {
    const bar = $("#taskbar");
    if (!t || (!t.active && (!t.ended || Date.now() - new Date(t.ended).getTime() > 8000))) { bar.hidden = true; document.querySelectorAll(".btn.busy").forEach((b) => b.classList.remove("busy")); return; }
    bar.hidden = false;
    bar.className = "taskbar" + (t.active ? "" : /fail/i.test(t.result || "") ? " failed" : " done");
    const pct = t.total ? Math.round((t.done / t.total) * 100) : null;
    bar.innerHTML = `<span class="spin"></span><span>${t.active ? "Running:" : "Finished:"} ${esc(t.name || "")}</span>${t.total > 1 ? `<span class="bar"><i style="width:${pct}%"></i></span><span class="mono">${t.done}/${t.total}</span>` : ""}${t.current && t.active ? `<span class="mono">· ${esc(t.current).slice(0, 60)}</span>` : ""}<span class="mono">· ${t.active ? fmtElapsed(t.started) + " elapsed" : esc(t.result || "")}</span>${t.errors ? `<span class="mono">· ${t.errors} failed</span>` : ""}${t.active ? `<span class="mono">· wait for this to finish before starting another run</span>` : ""}`;
    document.querySelectorAll("#app .btn").forEach((b) => { if (/assess|normalize|rescore|investigate|publish|quick/i.test(b.textContent)) b.classList.toggle("busy", !!t.active); });
    $("#publish").classList.toggle("busy", !!t.active);
  }
  async function pollTask(force = false) {
    if (!state.local) return;
    try {
      const t = await (await fetch("/api/task", { cache: "no-store" })).json();
      renderTask(t);
      const wasActive = !!taskTimer;
      if (t.active) { if (!taskTimer) taskTimer = setInterval(() => pollTask(), 2000); }
      else if (taskTimer) { clearInterval(taskTimer); taskTimer = null; await loadData(); route(); setTimeout(() => renderTask(null), 8000); }
    } catch (e) {}
  }
  function watchTask() { pollTask(); if (!taskTimer) taskTimer = setInterval(() => pollTask(), 2000); }

  function toast(msg, ms = 2600) { const t = h(`<div class="toast">${esc(msg)}</div>`); document.body.appendChild(t); setTimeout(() => t.remove(), ms); }

  // ---------- helpers ----------
  function ago(iso) {
    if (!iso) return "—";
    const d = (Date.now() - new Date(iso).getTime()) / 864e5;
    if (d < 1) return Math.max(1, Math.round(d * 24)) + "h ago";
    if (d < 45) return Math.round(d) + "d ago";
    return Math.round(d / 30) + "mo ago";
  }
  function ageDays(l) {
    if ((l.site === "bat" || l.site === "carsandbids") && l.availability === "active") return null;
    const src = l.listing_date || (l.first_seen || "").slice(0, 10);
    if (!src) return null;
    return Math.max(0, Math.round((Date.now() - new Date(src).getTime()) / 864e5));
  }
  function listedAge(l) { return l.listing_date ? ago(l.listing_date) : l.first_seen ? "seen " + ago(l.first_seen) : "—"; }
  function siteName(k) { return (state.data.sites || {})[k] || k; }
  function siteChip(k) { const c = { facebook: "teal", cargurus: "olive", carscom: "mustard", autotrader: "slate", carsandbids: "orange", bat: "rose" }[k] || ""; return `<span class="chip ${c}">${esc(siteName(k))}</span>`; }
  function scoreOf(l) { return l.assessment?.score?.total ?? null; }
  function verdictOf(l) { return l.assessment?.verdict || null; }
  function verdictTone(v) { return { "Pursue": "olive", "Pursue conditionally": "olive", "Maybe / verify": "mustard", "Reject": "rose", "Do not pursue": "rose" }[v] || ""; }
  const MISSIONS = ["enthusiast_bridge", "pragmatic_bridge", "future_keeper", "utility_capability"];
  const missionLabel = (m) => ({ enthusiast_bridge: "enthusiast bridge", pragmatic_bridge: "pragmatic bridge", future_keeper: "future keeper", utility_capability: "utility / capability" }[m] || m || "—");
  function prelimOf(l) { return l.prelim_score ?? null; }
  // Preliminary scores run optimistic; sort unassessed cards by the calibrated value.
  function calibrated(l) { const p = prelimOf(l); if (p == null) return null; const off = state.data.calibration?.offset; return off == null ? p : Math.max(0, Math.min(100, p + off)); }
  function glance(l) { return scoreOf(l) ?? calibrated(l); }
  function rankOf(l) {
    if (l.role !== "candidate" || !l.profile_key) return null;
    const pool = state.data.listings.filter((x) => x.role === "candidate" && x.availability === "active" && x.profile_key === l.profile_key && glance(x) != null);
    if (pool.length < 2) return null;
    pool.sort((a, b) => glance(b) - glance(a));
    const i = pool.findIndex((x) => x.id === l.id);
    return i < 0 ? null : { rank: i + 1, of: pool.length };
  }
  function modelTag(a) { const m = (a && a.model) || ""; return /opus/.test(m) ? "Opus" : /sonnet/.test(m) ? "Sonnet" : /haiku/.test(m) ? "Haiku" : m ? m.split("-")[1] : ""; }
  function badge(l) {
    const s = scoreOf(l);
    if (s != null) return `<span class="badge ${s >= 75 ? "hi" : s >= 60 ? "mid" : "lo"}" title="Score /100 · policy ${esc(l.assessment.policy_version)}">${s}</span>`;
    const p = prelimOf(l);
    if (p != null) { const c = calibrated(l), off = state.data.calibration?.offset; return `<span class="badge prelim ${c >= 75 ? "hi" : c >= 60 ? "mid" : "lo"}" title="Preliminary ${Math.round(p)}/100 (not yet assessed). ${off != null ? `Assessed scores have landed ${off >= 0 ? "+" : ""}${off} from preliminary on average over ${state.data.calibration.samples} cars; sorting uses ≈${Math.round(c)}.` : "Preliminary scores run optimistic: expect the assessment to land lower."}">${off != null ? "≈" : ""}${Math.round(off != null ? c : p)}</span>`; }
    return `<span class="badge none">n/a</span>`;
  }
  function availChip(a) { const c = { active: "olive", pending: "mustard", sold: "rose", ended: "walnut", removed: "slate", withdrawn: "rose" }[a] || ""; return `<span class="chip ${c === "walnut" ? "mustard" : c}">${esc(a)}</span>`; }
  function title(l) { return l.title || [l.year, l.make, l.model, l.trim].filter(Boolean).join(" ") || "Untitled listing"; }
  function photo(l) { return (l.photos && l.photos[0]) || l.thumb || null; }
  function profileLabel(k) { return state.profiles.get(k)?.label || k || "unprofiled"; }

  // ---------- routing ----------
  function route() {
    const hash = location.hash || "#/";
    const [path, qs] = hash.slice(1).split("?");
    const params = new URLSearchParams(qs || "");
    const parts = path.split("/").filter(Boolean);
    document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === (parts[0] || "board")));
    const app = $("#app"); app.innerHTML = "";
    if (!state.data) return;
    if (parts[0] === "l" && parts[1]) return renderDetail(app, Number(parts[1]));
    if (parts[0] === "market") return renderMarket(app, params.get("p"));
    if (parts[0] === "profiles") return renderProfiles(app);
    if (parts[0] === "settings") return renderSettings(app);
    if (parts[0] === "compare") return renderCompare(app);
    return renderBoard(app);
  }
  window.addEventListener("hashchange", () => (state.local ? loadData().then(route) : route()));

  // ---------- board ----------
  function filtered() {
    const f = state.filters, q = state.q.trim().toLowerCase();
    let rows = state.data.listings.filter((l) => {
      if (f.role && l.role !== f.role) return false;
      if (!f.role && l.role === "ignored") return false;
      if (f.profiles.length && !f.profiles.includes(l.profile_key || "__none__")) return false;
      if (f.site && l.site !== f.site) return false;
      if (f.avail && l.availability !== f.avail) return false;
      const st = l.status || "New", inc = Object.keys(f.statuses || {}).filter((k) => f.statuses[k] === "include");
      if (inc.length && !inc.includes(st)) return false;
      if ((f.statuses || {})[st] === "exclude") return false;
      if (f.analyzed && !l.assessment) return false;
      const px = l.sold_price || l.price;
      if (f.max_price && px && px > Number(f.max_price)) return false;
      if (f.max_mileage && l.mileage && l.mileage > Number(f.max_mileage)) return false;
      if (f.max_age) { const a = ageDays(l); if (a != null && a > Number(f.max_age)) return false; }
      if (q) {
        const hay = [title(l), l.location, l.model, l.trim, l.engine, l.exterior_color, l.notes, l.normalized?.prelim_summary, (l.options || []).join(" ")].join(" ").toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
    const sorters = {
      score: (a, b) => (glance(b) ?? -1) - (glance(a) ?? -1),
      price: (a, b) => (a.price ?? 9e9) - (b.price ?? 9e9),
      price_desc: (a, b) => (b.price ?? -1) - (a.price ?? -1),
      mileage: (a, b) => (a.mileage ?? 9e9) - (b.mileage ?? 9e9),
      newest: (a, b) => (b.listing_date || b.first_seen || "").localeCompare(a.listing_date || a.first_seen || ""),
      year: (a, b) => (b.year ?? 0) - (a.year ?? 0),
    };
    rows.sort(sorters[f.sort] || sorters.score);
    rows.sort((a, b) => (b.pinned ? 1 : 0) - (a.pinned ? 1 : 0));
    // One card per car: listings sharing a vehicle (same VIN) fold into the
    // best-ranked one, which carries the other venues as `also_on`.
    const seen = new Map();
    const out = [];
    for (const l of rows) {
      const key = l.vehicle_id && (l.role === "candidate") ? "v" + l.vehicle_id : "l" + l.id;
      const prime = seen.get(key);
      if (prime) { prime.also_on = (prime.also_on || []).concat([l]); continue; }
      l.also_on = [];
      seen.set(key, l); out.push(l);
    }
    return out;
  }

  function renderBoard(app) {
    const L = state.data.listings;
    const cands = L.filter((l) => l.role === "candidate" && l.availability === "active");
    const analyzed = cands.filter((l) => l.assessment).length;
    const comps = L.filter((l) => l.role === "comp").length;
    const pursue = cands.filter((l) => l.status === "Pursue" || /^Pursue/.test(verdictOf(l) || "")).length;
    const ignored = L.filter((l) => l.role === "ignored").length;
    app.appendChild(h(`
      <div class="hero">
        <div><h1>The board</h1><p>Everything you've saved, normalized and scored. Sold and ended listings feed the <a href="#/market">market view</a>.</p></div>
        <div class="tiles" style="margin:0;min-width:520px">
          <div class="tile"><div class="k">Active candidates</div><div class="v">${cands.length}</div><div class="s">${analyzed} assessed${state.data.calibration?.offset != null ? ` · prelim runs ${-state.data.calibration.offset} high` : ""}</div></div>
          <div class="tile"><div class="k">Pursue</div><div class="v">${pursue}</div><div class="s">by verdict or status</div></div>
          <div class="tile"><div class="k">Market comps</div><div class="v">${comps}</div><div class="s">sold + ended${ignored ? ` · ${ignored} ignored` : ""}</div></div>
          <div class="tile"><div class="k">Profiles</div><div class="v">${state.data.profiles.length}</div><div class="s">${state.data.profiles.filter((p) => !p.verified).length} unverified</div></div>
        </div>
      </div>`));
    const f = state.filters;
    const sites = Object.entries(state.data.sites);
    const bar = h(`
      <div class="filters">
        <span class="seg" id="role"><button data-v="candidate" class="${f.role === "candidate" ? "on" : ""}">Candidates</button><button data-v="comp" class="${f.role === "comp" ? "on" : ""}">Comps</button><button data-v="ignored" class="${f.role === "ignored" ? "on" : ""}">Ignored</button><button data-v="" class="${f.role === "" ? "on" : ""}">All</button></span>
        <span class="chips" id="f-profiles" title="Click to toggle · Option-click for only this one"><button data-k="" class="${f.profiles.length ? "" : "on"}">All</button>${profileChips(f)}</span>
        <select id="f-site"><option value="">All sites</option>${sites.map(([k, v]) => `<option value="${k}" ${f.site === k ? "selected" : ""}>${esc(v)}</option>`).join("")}</select>
        <select id="f-avail"><option value="">Any availability</option>${["active", "pending", "sold", "ended", "removed", "withdrawn"].map((a) => `<option ${f.avail === a ? "selected" : ""}>${a}</option>`).join("")}</select>
        <span class="chips" id="f-statuses" title="Click: show only · click again: hide · third click: clear"></span>
        <select id="f-sort">${[["score", "Best score"], ["price", "Price ↑"], ["price_desc", "Price ↓"], ["mileage", "Mileage ↑"], ["newest", "Newest listed"], ["year", "Year ↓"]].map(([k, v]) => `<option value="${k}" ${f.sort === k ? "selected" : ""}>${v}</option>`).join("")}</select>
        <label><input type="checkbox" id="f-analyzed" ${f.analyzed ? "checked" : ""}> analyzed only</label>
        <label title="Hide listings priced above this">≤ $<input type="number" class="num" id="f-max-price" min="0" step="500" placeholder="max price" value="${esc(f.max_price)}"></label>
        <label title="Hide listings with more miles than this">≤ <input type="number" class="num" id="f-max-mileage" min="0" step="5000" placeholder="max miles" value="${esc(f.max_mileage)}"> mi</label>
        <label title="Hide listings older than this many days (live auctions are never hidden)">≤ <input type="number" class="num" id="f-max-age" min="0" step="7" placeholder="max age" value="${esc(f.max_age)}" style="width:90px"> days</label>
        ${f.max_price || f.max_mileage || f.max_age ? `<button class="btn sm ghost" id="f-clear-limits">clear limits</button>` : ""}
        <span class="spacer"></span>
        <span class="seg" id="view"><button data-v="cards" class="${f.view === "cards" ? "on" : ""}">Cards</button><button data-v="table" class="${f.view === "table" ? "on" : ""}">Table</button></span>
      </div>`);
    app.appendChild(bar);
    const bindChips = () => {
      const box = $("#f-profiles", bar);
      box.innerHTML = `<button data-k="" class="${f.profiles.length ? "" : "on"}">All</button>` + profileChips(f);
      box.querySelectorAll("button").forEach((b) => (b.onclick = (e) => {
        const k = b.dataset.k;
        if (!k) f.profiles = [];
        else if (e.altKey) f.profiles = [k];
        else f.profiles = f.profiles.includes(k) ? f.profiles.filter((x) => x !== k) : f.profiles.concat([k]);
        rerender();
      }));
    };
    const rerender = () => { save("filters", f); bindChips(); bindStatusChips(); renderList(); };
    bar.querySelectorAll("#role button").forEach((b) => (b.onclick = () => { f.role = b.dataset.v; if (f.role === "comp" || f.role === "ignored") f.avail = ""; if (f.role === "candidate") f.avail = f.avail || "active"; route(); }));
    bar.querySelectorAll("#view button").forEach((b) => (b.onclick = () => { f.view = b.dataset.v; route(); }));
    bindChips();
    $("#f-site", bar).onchange = (e) => { f.site = e.target.value; rerender(); };
    $("#f-avail", bar).onchange = (e) => { f.avail = e.target.value; rerender(); };
    const bindStatusChips = () => {
      const box = $("#f-statuses", bar);
      const saved = f.statuses; f.statuses = {};
      const counts = {}; for (const l of filtered()) { const k = l.status || "New"; counts[k] = (counts[k] || 0) + 1; }
      f.statuses = saved;
      const any = Object.values(f.statuses).some(Boolean);
      box.innerHTML = `<button data-k="" class="${any ? "" : "on"}">Any</button>` + STATUSES.filter((st) => counts[st] || f.statuses[st]).map((st) => `<button data-k="${st}" class="${f.statuses[st] === "include" ? "on" : f.statuses[st] === "exclude" ? "off" : ""}">${f.statuses[st] === "exclude" ? "✕ " : ""}${st} <span class="n">${counts[st] || 0}</span></button>`).join("");
      box.querySelectorAll("button").forEach((b) => (b.onclick = () => {
        const k = b.dataset.k;
        if (!k) f.statuses = {};
        else { const cur = f.statuses[k]; f.statuses = { ...f.statuses }; if (!cur) f.statuses[k] = "include"; else if (cur === "include") f.statuses[k] = "exclude"; else delete f.statuses[k]; }
        rerender();
      }));
    };
    bindStatusChips();
    $("#f-sort", bar).onchange = (e) => { f.sort = e.target.value; rerender(); };
    $("#f-analyzed", bar).onchange = (e) => { f.analyzed = e.target.checked; rerender(); };
    let limitTimer;
    const onLimit = (key) => (e) => { clearTimeout(limitTimer); limitTimer = setTimeout(() => { f[key] = e.target.value; rerender(); const cl = $("#f-clear-limits", bar); if (!cl && (f.max_price || f.max_mileage || f.max_age)) route(); }, 250); };
    $("#f-max-price", bar).oninput = onLimit("max_price");
    $("#f-max-mileage", bar).oninput = onLimit("max_mileage");
    $("#f-max-age", bar).oninput = onLimit("max_age");
    const clearBtn = $("#f-clear-limits", bar);
    if (clearBtn) clearBtn.onclick = () => { f.max_price = ""; f.max_mileage = ""; f.max_age = ""; save("filters", f); route(); };
    const list = h(`<div id="list"></div>`); app.appendChild(list);
    function renderList() {
      const rows = filtered();
      list.innerHTML = "";
      if (f.max_price || f.max_mileage || f.max_age) {
        const saved = { p: f.max_price, m: f.max_mileage, a: f.max_age }; f.max_price = ""; f.max_mileage = ""; f.max_age = "";
        const without = filtered().length; f.max_price = saved.p; f.max_mileage = saved.m; f.max_age = saved.a;
        if (without > rows.length) list.appendChild(h(`<p class="muted small" style="margin:0 0 10px">${without - rows.length} listing${without - rows.length === 1 ? "" : "s"} hidden by your price / mileage / age limits.</p>`));
      }
      if (!state.data.listings.length) return list.appendChild(emptyState());
      if (!rows.length) return list.appendChild(h(`<div class="empty"><h2>Nothing matches</h2><p>Loosen the filters or sync more listings.</p></div>`));
      if (f.view === "table") return list.appendChild(tableView(rows));
      const grid = h(`<div class="grid"></div>`);
      rows.forEach((l) => grid.appendChild(card(l)));
      list.appendChild(grid);
    }
    renderList();
    renderTray(app);
  }

  function profileChips(f) {
    // Counts respect every other filter (role, site, availability, status, limits) so a chip never promises cards it cannot show.
    const saved = f.profiles; f.profiles = [];
    const counts = {};
    for (const l of filtered()) { const k = l.profile_key || "__none__"; counts[k] = (counts[k] || 0) + 1; }
    f.profiles = saved;
    const items = state.data.profiles.filter((p) => counts[p.key]).map((p) => [p.key, shortLabel(p.label), counts[p.key]]);
    if (counts.__none__) items.push(["__none__", "unprofiled", counts.__none__]);
    return items.map(([k, label, n]) => `<button data-k="${esc(k)}" class="${f.profiles.includes(k) ? "on" : ""}">${esc(label)} <span class="n">${n}</span></button>`).join("");
  }
  function shortLabel(label) { return label.replace(/\s*\(.*?\)\s*/g, " ").replace(/\s+/g, " ").trim().slice(0, 28); }

  function emptyState() {
    return h(`<div class="empty"><h2>No listings yet</h2>
      <p>Get the first batch in three steps:</p>
      <ol>
        <li>Start the local server: <code>.venv/bin/python run.py</code></li>
        <li>Load the <code>extension/</code> folder in Chrome (chrome://extensions → Developer mode → Load unpacked)</li>
        <li>Open your saved listings on any supported site and click <b>Sync saved listings</b></li>
      </ol></div>`);
  }

  function card(l) {
    const p = photo(l);
    const flags = l.normalized?.red_flags?.length || 0;
    const v = verdictOf(l);
    const qg = (l.normalized?.quick_gates || []).concat((l.provenance?.flags || []).map((f) => f.replace(/_/g, " ")));
    const drops = (l.normalized?.price_drops || []).reduce((a, d) => a + (d.amount || 0), 0) + (l.history || []).filter((s) => s.price).reduce((a, s, i, arr) => a + (i && arr[i - 1].price > s.price ? arr[i - 1].price - s.price : 0), 0);
    const el = h(`
      <article class="card ${l.role}" data-id="${l.id}">
        <div class="photo">${p ? `<img loading="lazy" src="${esc(p)}" alt="">` : `<div class="nophoto">⌁</div>`}
          <span class="score">${badge(l)}</span><span class="site">${siteChip(l.site)}</span></div>
        <div class="body">
          <div class="title">${esc(title(l))}${(() => { const r = rankOf(l); return r ? ` <span class="chip" title="rank among active candidates in this profile">#${r.rank} of ${r.of}</span>` : ""; })()}</div>
          <div class="price">${l.role === "comp" && (l.sold_price || l.price) ? money(l.sold_price || l.price) + `<small>${l.availability === "sold" ? "sold" : esc(l.price_kind || "")}</small>` : money(l.price) + (l.price_kind && l.price_kind !== "asking" ? `<small>${esc(l.price_kind.replace("_", " "))}</small>` : "")}</div>
          <div class="meta"><span class="mono">${l.mileage ? num(l.mileage) + " mi" : "— mi"}</span><span>${esc(l.location || "—")}</span><span>${listedAge(l)}</span>${l.transmission ? `<span>${esc(l.transmission)}</span>` : ""}</div>
          ${(l.also_on || []).length ? `<div class="row" style="gap:6px"><span class="muted small">same VIN also on</span>${l.also_on.map((o) => `<a href="#/l/${o.id}" class="chip" onclick="event.stopPropagation()" title="${esc(money(o.sold_price || o.price))}">${esc(siteName(o.site))} ${money(o.sold_price || o.price)}</a>`).join("")}</div>` : ""}
          <div class="foot">
            <div class="row" style="gap:6px">${l.assessment ? `<span class="chip ${/opus/i.test(l.assessment.model || "") ? "teal" : "olive"}" title="${esc(modelTag(l.assessment))} assessment ${ago(l.assessment.assessed_at)} · policy ${esc(l.assessment.policy_version)}">✓ ${esc(modelTag(l.assessment) || "assessed")}</span>` : `<span class="chip" title="Preliminary only: sync-time read, not yet assessed">preliminary</span>`}${v ? `<span class="chip ${verdictTone(v)}">${esc(v)}</span>` : ""}${drops ? `<span class="chip olive" title="Price reductions on record (site-reported + observed)">↓ ${money(drops)}</span>` : ""}${qg.map((g) => `<span class="chip rose" title="sync-time policy flag">${esc(g)}</span>`).join("")}${flags ? `<span class="chip orange" title="${esc(l.normalized.red_flags.join("\n"))}">⚑ ${flags}</span>` : ""}${l.availability !== "active" ? availChip(l.availability) : ""}${l.pinned ? `<span class="chip mustard">★</span>` : ""}</div>
            <span class="row" style="gap:8px"><label class="cmp" title="Add to compare"><input type="checkbox" ${state.compare.includes(l.id) ? "checked" : ""}></label><span class="pill-status">${esc(l.status || "New")}</span></span>
          </div>
        </div>
      </article>`);
    el.onclick = (e) => { if (e.target.closest(".cmp")) return; location.hash = "#/l/" + l.id; };
    $(".cmp input", el).onchange = (e) => { toggleCompare(l.id, e.target.checked); };
    return el;
  }

  function tableView(rows) {
    const cols = [["", (l) => badge(l)], ["Listing", (l) => `<a href="#/l/${l.id}">${esc(title(l))}</a>`], ["Price", (l) => `<span class="mono">${money(l.sold_price || l.price)}</span>`],
      ["Miles", (l) => `<span class="mono">${num(l.mileage)}</span>`], ["Year", (l) => l.year ?? "—"], ["Trans.", (l) => esc(l.transmission || "—")], ["Location", (l) => esc(l.location || "—")],
      ["Site", (l) => siteChip(l.site)], ["Listed", (l) => listedAge(l)], ["Assessed", (l) => l.assessment ? `<span class="chip ${/opus/i.test(l.assessment.model || "") ? "teal" : "olive"}">✓ ${esc(modelTag(l.assessment))} · ${ago(l.assessment.assessed_at)}</span>` : `<span class="muted small">preliminary</span>`], ["Verdict", (l) => esc(verdictOf(l) || "—")], ["Mission", (l) => esc(missionLabel(l.mission))], ["Status", (l) => esc(l.status || "New")]];
    return h(`<div class="tablewrap"><table class="data"><thead><tr>${cols.map(([c]) => `<th>${c}</th>`).join("")}</tr></thead>
      <tbody>${rows.map((l) => `<tr>${cols.map(([, f]) => `<td>${f(l)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
  }

  // ---------- compare tray ----------
  function toggleCompare(id, on) {
    state.compare = state.compare.filter((x) => x !== id);
    if (on) state.compare.push(id);
    state.compare = state.compare.slice(-4);
    save("compare", state.compare);
    renderTray($("#app"));
  }
  function renderTray(app) {
    $(".tray")?.remove();
    if (!state.compare.length) return;
    const t = h(`<div class="tray"><span>${state.compare.length} to compare</span><a class="btn sm primary" href="#/compare">Compare</a><button class="btn sm ghost" id="clear-cmp">Clear</button></div>`);
    $("#clear-cmp", t).onclick = () => { state.compare = []; save("compare", []); renderTray(app); document.querySelectorAll(".cmp input").forEach((i) => (i.checked = false)); };
    document.body.appendChild(t);
  }

  // ---------- detail ----------
  function renderDetail(app, id) {
    const l = state.byId.get(id);
    if (!l) return app.appendChild(h(`<div class="empty"><h2>Not found</h2></div>`));
    const A = l.assessment, N = l.normalized || {}, prof = state.profiles.get(l.profile_key);
    const E = A?.evidence, S = A?.score, C = A?.costs;
    const photos = l.photos && l.photos.length ? l.photos : l.thumb ? [l.thumb] : [];
    const peers = state.data.listings.filter((x) => x.profile_key === l.profile_key && x.role === "candidate" && x.availability === "active" && x.id !== l.id);
    const siblings = l.vehicle_id ? state.data.listings.filter((x) => x.vehicle_id === l.vehicle_id && x.id !== l.id) : [];
    const comps = state.data.listings.filter((x) => x.profile_key === l.profile_key && x.role === "comp");
    const market = state.data.markets?.[l.profile_key] || {};
    const CATS = [["documentation", "Documentation & verifiability", 25], ["condition", "Condition", 25], ["price_value", "Price & risk-adjusted value", 15], ["mission_fit", "Mission fit", 15], ["logistics", "Logistics & inspectability", 10], ["emotional_spec_fit", "Emotional / spec fit", 10]].map(([k, l, m]) => [k, l, (S && S.max && S.max[k]) || (N.prelim_breakdown && N.prelim_breakdown[k] && N.prelim_breakdown[k].max) || m]);
    const factChip = (f) => `<span class="chip ${f.status === "verified" ? "olive" : f.status === "claimed" ? "mustard" : f.status === "inferred" ? "teal" : ""}" title="${esc(f.source)}${f.note ? " · " + esc(f.note) : ""}">${esc(f.status)}</span>`;
    const list = (arr) => `<ul class="list">${(arr || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`;

    app.appendChild(h(`<div>
      <div class="row" style="justify-content:space-between;margin-bottom:12px">
        <a href="#/" class="btn sm ghost">← Board</a>
        <div class="row">${siteChip(l.site)}${availChip(l.availability)}${l.role === "comp" ? `<span class="chip dark">market comp</span>` : ""}<span class="chip" title="mission">${esc(missionLabel(l.mission))}</span>${siblings.map((o) => `<a href="#/l/${o.id}" class="chip teal" title="same VIN, other venue">also on ${esc(siteName(o.site))} · ${money(o.sold_price || o.price)}</a>`).join("")}<a class="btn sm" href="${esc(l.url)}" target="_blank" rel="noopener">Open listing ↗</a></div>
      </div>
      <div class="headline">
        <div><h1>${esc(title(l))}</h1><div class="muted">${esc([l.year, l.make, l.model, l.generation ? "(" + l.generation + ")" : "", l.trim].filter(Boolean).join(" "))} · ${esc(l.location || "location unknown")} · ${listedAge(l)}${prof ? ` · <a href="#/profiles">${esc(prof.label)}</a>${prof.verified ? "" : " <span class='chip mustard'>unverified profile</span>"}` : ""}</div></div>
        <div class="row" style="gap:18px">
          <div><div class="price">${money(l.sold_price || l.price)}</div><div class="muted small">${esc(l.price_kind ? l.price_kind.replace("_", " ") : "asking")}${l.price_pct_vs_sold != null ? ` · pricier than ${l.price_pct_vs_sold}% of sold comps` : ""}</div></div>
          ${S ? `<div class="dial" style="--pct:${S.total}"><span>${S.total}</span><small>/100</small></div>`
              : prelimOf(l) != null ? `<div class="dial prelim" style="--pct:${Math.round(calibrated(l))}"><span>${state.data.calibration?.offset != null ? "≈" : ""}${Math.round(calibrated(l))}</span><small>prelim</small></div>` : ""}
          ${A ? `<div><div class="verdict ${esc(A.verdict.split(" ")[0])}">${esc(A.verdict)}</div><div class="muted small">${esc(modelTag(A))} · confidence ${A.confidence}/100 · policy ${esc(A.policy_version)}</div></div>`
              : `<div><div class="verdict" style="color:var(--muted)">Not assessed</div><div class="muted small">${(() => { const r = rankOf(l); return r ? `#${r.rank} of ${r.of} in profile · ` : ""; })()}preliminary read only</div></div>`}
        </div>
      </div>
      ${(S || N.prelim_breakdown) ? `<div class="catstrip">${CATS.map(([k, label, max]) => { const pts = S ? S[k] : (N.prelim_breakdown[k] || {}).points; const why = S ? (E.ratings?.[k]?.rationale || "") : (N.prelim_breakdown[k] || {}).why || ""; return `<span class="cat" title="${esc(why)}"><b>${label.split(" ")[0].replace("&", "")}</b> <span class="mono">${pts ?? "—"}/${max}</span><i style="width:${Math.round(((pts || 0) / max) * 100)}%"></i></span>`; }).join("")}${S?.caps_applied?.length ? `<span class="muted small">caps: ${S.caps_applied.length}</span>` : ""}</div>` : ""}</div>`));

    const main = h(`<div></div>`), side = h(`<div></div>`);
    const grid = h(`<div class="detail"></div>`); grid.append(main, side); app.appendChild(grid);

    if (photos.length) {
      const g = h(`<div class="gallery"><div class="main"><img src="${esc(photos[0])}" alt=""></div><div class="thumbs">${photos.map((p, i) => `<img src="${esc(p)}" class="${i === 0 ? "on" : ""}" alt="">`).join("")}</div></div>`);
      g.querySelectorAll(".thumbs img").forEach((im) => (im.onclick = () => { $(".main img", g).src = im.src; g.querySelectorAll(".thumbs img").forEach((x) => x.classList.toggle("on", x === im)); }));
      main.appendChild(g); main.appendChild(h(`<div style="height:16px"></div>`));
    }

    const P = l.provenance;
    if (P) {
      const flagLabel = { major_markup: "major markup", material_markup: "material markup", very_recent_resale: "very recent resale", recent_resale: "recent resale", rapid_relisting: "rapid relisting", not_actively_available: "not actively available" };
      const pp = P.price_progression;
      main.appendChild(h(`<div class="panel ${P.flags.length ? "accent-rose" : "accent-teal"}"><h3>Provenance <span class="muted small">${esc(P.confidence_label)} · ${ago(P.analyzed_at)}</span></h3>
        <div class="row" style="gap:6px;margin-bottom:8px">${P.flags.map((f) => `<span class="chip rose">${esc(flagLabel[f] || f)}</span>`).join("")}${!P.flags.length ? `<span class="chip olive">no same-car flags</span>` : ""}</div>
        ${P.current_status && P.current_status.available === false ? `<p><b>Current status:</b> ${esc(P.current_status.note)}</p>` : `<p><b>Current status:</b> ${esc(P.current_status?.listing_availability || "active")}</p>`}
        ${P.summary ? `<p>${esc(P.summary)}</p>` : ""}
        ${pp ? `<p><b>Price progression:</b> ${esc(pp.reference_description)} Now asking ${money(pp.current_price)}: <span class="mono">${pp.dollar_change >= 0 ? "+" : ""}${money(pp.dollar_change).replace("$-", "-$")}</span> (${(pp.percent_change * 100).toFixed(1)}%)${pp.elapsed_days != null ? ` after ${pp.elapsed_days} days` : ""}${pp.mileage_added != null ? (pp.mileage_added >= 0 ? `, ${num(pp.mileage_added)} miles added` : `, <span style="color:var(--rose)">mileage ${num(-pp.mileage_added)} LOWER than before (odometer inconsistency)</span>`) : ""}.</p>` : ""}
        ${P.same_car_history?.length ? `<div class="tablewrap"><table class="checks"><tr><td class="muted small">Date</td><td class="muted small">Venue · status · evidence</td></tr>${P.same_car_history.map((e) => `<tr><td class="mono small">${esc(e.date || "undated")}</td><td>${esc(e.venue || "")} · <b>${esc(e.status)}</b>${e.mileage ? ` · ${num(e.mileage)} mi` : ""} · ${esc(e.description)}${e.identity_confidence !== "confirmed" ? ` <span class="chip mustard">${esc(e.identity_confidence.replace("_", " "))}</span>` : ""}${e.url ? ` <a href="${esc(e.url)}" target="_blank" rel="noopener">↗</a>` : ""}</td></tr>`).join("")}</table></div>` : `<p class="muted small">No prior same-car listings established.</p>`}
        ${(P.what_changed?.work_after_prior_sale?.length || P.what_changed?.work_before_prior_sale?.length) ? `<p style="margin-top:8px"><b>What changed since the previous sale:</b></p><div class="kv small"><span class="k">After the sale</span><span>${P.what_changed.work_after_prior_sale.length ? P.what_changed.work_after_prior_sale.map(esc).join("; ") : "nothing documented"}</span><span class="k">Before the sale</span><span>${P.what_changed.work_before_prior_sale.map(esc).join("; ") || "—"}</span>${P.what_changed.cosmetic_or_preference?.length ? `<span class="k">Cosmetic / preference</span><span>${P.what_changed.cosmetic_or_preference.map(esc).join("; ")}</span>` : ""}${P.what_changed.repairs_correcting_faults?.length ? `<span class="k">Repairs of faults</span><span>${P.what_changed.repairs_correcting_faults.map(esc).join("; ")}</span>` : ""}</div>` : ""}
        ${P.cross_post_findings?.length ? `<p style="margin-top:8px"><b>Seller cross-posts and comments:</b></p><ul class="list small">${P.cross_post_findings.map((c) => `<li><span class="chip ${c.kind === "keep" || c.kind === "withdrawn" || c.kind === "sold" ? "rose" : "mustard"}">${esc(c.kind.replace("_", " "))}</span> "${esc(c.quote)}" <span class="muted">${esc(c.date || "undated")}${c.factual ? "" : " · opinion"}</span>${c.url ? ` <a href="${esc(c.url)}" target="_blank" rel="noopener">↗</a>` : ""}</li>`).join("")}</ul>` : ""}
        ${P.possible_matches?.length ? `<details><summary class="muted small">${P.possible_matches.length} possible match(es) not used (no unique identifier)</summary><ul class="list small">${P.possible_matches.map((e) => `<li>${esc(e.date || "undated")} · ${esc(e.venue || "")} · ${esc(e.status)} · ${esc(e.description)}${e.url ? ` <a href="${esc(e.url)}" target="_blank" rel="noopener">↗</a>` : ""}</li>`).join("")}</ul></details>` : ""}
        ${P.effect?.length ? `<p style="margin-top:8px"><b>Effect on recommendation and price ceiling:</b></p><ul class="list">${P.effect.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>` : ""}
        ${P.sources?.length ? `<details><summary class="muted small">${P.sources.length} source link(s)</summary><ul class="list small">${P.sources.map((u) => `<li><a href="${esc(u)}" target="_blank" rel="noopener">${esc(u)}</a></li>`).join("")}</ul></details>` : ""}</div>`));
    } else if (l.timeline?.length > 1) {
      main.appendChild(h(`<div class="panel accent-teal"><h3>Vehicle timeline <span class="muted small">from tracked listings; no investigation run yet</span></h3><table class="checks">${l.timeline.map((e) => `<tr><td class="mono small">${esc(e.event_date || "undated")}</td><td>${esc(e.venue || "")} · <b>${esc(e.status)}</b>${e.price ? ` · ${money(e.price)} <span class="muted small">${esc((e.price_type || "").replace(/_/g, " "))}</span>` : ""}${e.mileage ? ` · ${num(e.mileage)} mi` : ""}${e.url ? ` <a href="${esc(e.url)}" target="_blank" rel="noopener">↗</a>` : ""}</td></tr>`).join("")}</table></div>`));
    }

    if (A) {
      const hard = A.gates.filter((g) => g.kind === "hard" || g.kind === "strategy" || g.kind === "configuration");
      const cond = A.gates.filter((g) => g.kind === "conditional");
      main.appendChild(h(`<div class="panel ${verdictTone(A.verdict) === "rose" ? "accent-rose" : verdictTone(A.verdict) === "mustard" ? "accent-mustard" : "accent-olive"}">
        <h3>Verdict: ${esc(A.verdict)} <span class="muted small">${ago(A.assessed_at)} · ${esc(A.mission.replace("_", " "))} · ${esc(A.urgency_mode.replace("_", " "))}</span></h3>
        <p><b>${esc(A.verdict_reason)}</b></p>
        ${E.mission_note ? `<p><b>Jason fit:</b> ${esc(E.mission_note)}</p>` : ""}
        <p>${esc(E.rationale)}</p>
        ${E.next_action ? `<p class="small" style="margin:8px 0 0"><b>Next action:</b> ${esc(E.next_action)}</p>` : ""}</div>`));
      if (hard.length) main.appendChild(h(`<div class="panel accent-rose"><h3>Hard gates (override the score)</h3>${list(hard.map((g) => g.reason))}</div>`));
      if (cond.length) main.appendChild(h(`<div class="panel accent-mustard"><h3>Unresolved conditions (cap the verdict)</h3>${list(cond.map((g) => g.reason))}</div>`));
      if (E.contradictions?.length) main.appendChild(h(`<div class="panel accent-rose"><h3>Contradictions</h3>${list(E.contradictions.map((c) => `${c.severity}: ${c.topic} — ${c.detail}`))}</div>`));
      main.appendChild(h(`<div class="panel accent-olive"><h3>Why it works</h3>${list(E.positives)}</div>`));
      main.appendChild(h(`<div class="panel accent-mustard"><h3>Main risks</h3>${list(E.concerns)}</div>`));
      main.appendChild(h(`<div class="panel"><h3>Missing evidence <span class="muted small">unknown is not good</span></h3>${list(E.unknowns)}</div>`));
      if (E.critical_evidence?.length) main.appendChild(h(`<div class="panel accent-walnut"><h3>Model-critical evidence</h3><table class="checks">${E.critical_evidence.map((c) => { const req = (prof?.critical_evidence || []).find((r) => r.key === c.key); const strong = c.status === "failed" && req?.severity !== "hard"; return `<tr><td class="st-${c.status === "satisfied" ? "pass" : c.status === "failed" && !strong ? "fail" : c.status === "claimed_only" || strong ? "concern" : "unknown"}">${strong ? "strong reservations" : esc(c.status.replace("_", " "))}</td><td><b>${esc(req?.label || c.key)}</b>${req?.severity === "hard" ? ` <span class="chip rose">hard</span>` : ""}${c.evidence ? `<div class="muted small">${esc(c.evidence)} <i>(${esc(c.source)})</i></div>` : ""}</td></tr>`; }).join("")}</table></div>`));
      main.appendChild(h(`<div class="panel accent-teal"><h3>Ask the seller</h3><ol class="list">${(E.seller_questions || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ol></div>`));
      main.appendChild(h(`<div class="panel accent-walnut"><h3>PPI focus</h3>${list(E.ppi_focus)}</div>`));
      if (E.what_would_change_verdict?.length) main.appendChild(h(`<div class="panel"><h3>What would change the verdict</h3>${list(E.what_would_change_verdict)}</div>`));
      if (E.facts?.length) main.appendChild(h(`<details class="panel"><summary class="muted">Facts with provenance (${E.facts.length})</summary><table class="checks">${E.facts.map((f) => `<tr><td>${factChip(f)}</td><td><b>${esc(f.key)}</b> ${esc(f.value ?? "—")}<div class="muted small">${esc(f.source)}${f.note ? " · " + esc(f.note) : ""}</div></td></tr>`).join("")}</table></details>`));
      const vh = A.vin_history || {};
      if ((vh.prior_listings || []).length || vh.vin_decode || (vh.recalls || []).length) main.appendChild(h(`<details class="panel" open><summary class="muted">VIN history &amp; decode</summary>
        ${vh.vin_decode ? `<p class="small"><b>NHTSA decode:</b> ${esc([vh.vin_decode.year, vh.vin_decode.make, vh.vin_decode.model, vh.vin_decode.trim || vh.vin_decode.series, vh.vin_decode.engine_liters ? vh.vin_decode.engine_liters + "L" : "", vh.vin_decode.body_class].filter(Boolean).join(" · "))}</p>` : ""}
        ${(vh.vin_decode_contradictions || []).length ? `<p class="small" style="color:var(--rose)"><b>Decode vs listing:</b> ${vh.vin_decode_contradictions.map((c) => esc(c.detail)).join("; ")}</p>` : ""}
        ${vh.markup_vs_last_sale != null ? `<p class="small"><b>Relist markup vs last sale:</b> <span class="mono">${(vh.markup_vs_last_sale * 100).toFixed(0)}%</span></p>` : ""}
        ${(vh.prior_listings || []).length ? `<table class="checks">${vh.prior_listings.map((p) => `<tr><td class="mono small">${esc((p.first_seen || "").slice(0, 10))}</td><td>${siteChip(p.site)} <span class="mono">${money(p.sold_price || p.price)}</span> ${esc(p.availability)} ${p.mileage ? "· " + num(p.mileage) + " mi" : ""} <a href="${esc(p.url)}" target="_blank" rel="noopener">↗</a></td></tr>`).join("")}</table>` : `<p class="muted small">No other listings with this VIN in the tracker.</p>`}
        ${(vh.recalls || []).length ? `<p class="small"><b>NHTSA campaigns for this make/model/year:</b> ${vh.recalls.length} (completion unknown)</p>` : ""}</details>`));
    } else {
      main.appendChild(h(`<div class="panel accent-mustard"><h3>Quick read <span class="chip mustard">sync-time</span></h3>
        <p>${esc(N.prelim_summary || "Not normalized yet. Run a sync with the server's API key set.")}</p>
        ${N.highlights?.length ? `<b>Highlights</b>${list(N.highlights)}` : ""}
        ${N.red_flags?.length ? `<b>Red flags</b>${list(N.red_flags)}` : ""}
        ${N.vin_decode ? `<p class="small"><b>NHTSA decode:</b> ${esc([N.vin_decode.year, N.vin_decode.make, N.vin_decode.model, N.vin_decode.trim || N.vin_decode.series, N.vin_decode.engine_liters ? N.vin_decode.engine_liters + "L" : ""].filter(Boolean).join(" · "))}</p>` : ""}
        ${(N.vin_contradictions || []).length ? `<p class="small" style="color:var(--rose)"><b>Decode vs listing:</b> ${N.vin_contradictions.map((c) => esc(c.detail)).join("; ")}</p>` : ""}
        <p class="muted small">No deep assessment yet${state.local ? "; run one from Actions." : "."}</p></div>`));
    }
    main.appendChild(h(`<details class="panel"><summary class="muted">Listing as captured${l.options?.length ? ` · ${l.options.length} options` : ""}</summary>${l.options?.length ? `<p><b>Options:</b> ${l.options.map(esc).join(", ")}</p>` : ""}<p class="muted small">Raw listing text stays on the local server and is not published. The assessment above is stored separately from it.</p></details>`));

    // ----- side -----
    if (state.local) {
      const act = h(`<div class="panel"><h3>Actions</h3>
        <div class="row"><button class="btn primary" id="analyze">${A ? "Re-assess" : "Assess"} <span class="muted small" style="color:inherit;opacity:.8">Opus · ~$1</span></button><button class="btn" id="analyze-quick" title="Same prompt and photos on Sonnet: triage tier">Quick assess <span class="muted small">Sonnet · ~30¢</span></button><button class="btn sm ghost" id="renorm" title="Re-run sync-time normalization">Re-normalize</button></div>
        <div class="row" style="margin-top:8px"><button class="btn sm warm" id="investigate" title="Queue a same-car search; the extension runs it in your browser">${P ? "Re-investigate provenance" : "Investigate provenance"}</button><span class="muted small" id="inv-status"></span></div>
        <label style="display:block;margin-top:10px">Mission <select id="mission">${MISSIONS.map((m) => `<option value="${m}" ${l.mission === m ? "selected" : ""}>${missionLabel(m)}</option>`).join("")}</select> <span class="muted small">pragmatic bridge lifts the manual gate</span></label>
        <div class="row" style="margin-top:10px"><label>Status <select id="status">${STATUSES.map((s) => `<option ${l.status === s ? "selected" : ""}>${s}</option>`).join("")}</select></label>
        <label><input type="checkbox" id="pin" ${l.pinned ? "checked" : ""}> pinned</label>
        <label>Role <select id="role"><option value="candidate" ${l.role === "candidate" ? "selected" : ""}>candidate</option><option value="comp" ${l.role === "comp" ? "selected" : ""}>comp</option><option value="ignored" ${l.role === "ignored" ? "selected" : ""}>ignored (not a car / not for me)</option></select></label></div>
        <label style="display:block;margin-top:10px">Profile <select id="prof"><option value="">— none —</option>${state.data.profiles.map((p) => `<option value="${p.key}" ${l.profile_key === p.key ? "selected" : ""}>${esc(p.label)}</option>`).join("")}</select></label>
        <textarea class="notes" id="notes" placeholder="Your notes (saved on blur)">${esc(l.notes || "")}</textarea>
        <div class="row" style="margin-top:10px;justify-content:flex-end"><button class="btn sm ghost" id="delete" title="Remove this listing and its history from the workbench">Delete listing</button></div></div>`);
      side.appendChild(act);
      if (l.last_error) side.appendChild(h(`<div class="panel accent-rose"><h3>Last run failed <span class="muted small">${ago(l.last_error.ts)}</span></h3><p class="small" style="margin:0">${esc(l.last_error.kind.replace("_", " "))}: ${esc(l.last_error.detail)}</p><p class="muted small" style="margin:6px 0 0">The paid call completed but its answer was rejected. This class of failure is now retried and trimmed automatically; run it again.</p></div>`));
      const runAssess = (tier) => async (e) => {
        const btn = e.currentTarget; btn.disabled = true; btn.textContent = "Assessing… (30–120s)"; setTimeout(watchTask, 300);
        try { await api(`/api/listings/${l.id}/assess?tier=${tier}`, "POST"); await loadData(); route(); toast("Assessment stored"); }
        catch (err) { toast("Assessment failed: " + err.message, 6000); btn.disabled = false; btn.textContent = tier === "quick" ? "Quick assess" : "Assess"; }
      };
      $("#analyze", act).onclick = runAssess("full");
      $("#analyze-quick", act).onclick = runAssess("quick");
      $("#investigate", act).onclick = async (e) => {
        e.target.disabled = true;
        try { await api(`/api/listings/${l.id}/provenance/queue`, "POST"); $("#inv-status", act).textContent = "Queued. Open the Hoopty Scout extension popup and click Run."; toast("Investigation queued"); }
        catch (err) { toast(err.message, 4000); e.target.disabled = false; }
      };
      api(`/api/listings/${l.id}/provenance`).then((r) => { const j = (r.jobs || [])[0]; if (j && j.status !== "done") $("#inv-status", act).textContent = `Investigation ${j.status}${j.hits ? ` · ${j.hits} hits` : ""}${j.error ? ` · ${j.error}` : ""}`; }).catch(() => {});
      $("#renorm", act).onclick = async (e) => { e.target.disabled = true; setTimeout(watchTask, 300); try { await api(`/api/listings/${l.id}/renormalize`, "POST"); await loadData(); route(); toast("Re-normalized"); } catch (err) { toast(err.message, 4000); e.target.disabled = false; } };
      const patch = async (body) => { try { await api(`/api/listings/${l.id}`, "PATCH", body); Object.assign(l, body); toast("Saved"); } catch (err) { toast(err.message, 4000); } };
      $("#delete", act).onclick = async () => {
        if (!confirm(`Delete "${title(l)}" and its snapshots, assessments and provenance? A future sync will re-add it as new if it is still saved on the site.`)) return;
        try { await api(`/api/listings/${l.id}`, "DELETE"); await loadData(); location.hash = "#/"; toast("Deleted"); } catch (err) { toast(err.message, 4000); }
      };
      $("#mission", act).onchange = (e) => patch({ mission: e.target.value });
      $("#status", act).onchange = (e) => patch({ status: e.target.value });
      $("#pin", act).onchange = (e) => patch({ pinned: e.target.checked });
      $("#role", act).onchange = (e) => patch({ role: e.target.value }).then(() => { if (e.target.value === "ignored") { toast("Hidden from the board"); location.hash = "#/"; } });
      $("#prof", act).onchange = (e) => patch({ profile_key: e.target.value });
      $("#notes", act).onblur = (e) => { if (e.target.value !== (l.notes || "")) patch({ notes: e.target.value }); };
    } else {
      side.appendChild(h(`<div class="panel"><div class="row" style="justify-content:space-between"><span class="pill-status">${esc(l.status || "New")}</span>${l.pinned ? `<span class="chip mustard">★ pinned</span>` : ""}</div>${l.notes ? `<p style="white-space:pre-wrap">${esc(l.notes)}</p>` : `<p class="muted small">Notes, mission, and status are edited on the local workbench.</p>`}</div>`));
    }

    if (C) {
      side.appendChild(h(`<div class="panel accent-orange"><h3>Price discipline</h3><div class="kv">
        <span class="k">${esc(C.price_basis.replace("_", " "))}</span><span class="mono">${money(C.price)}</span>
        <span class="k">Buyer fee</span><span class="mono">${money(C.buyer_fee)}</span>
        <span class="k">Transport / travel</span><span class="mono">${money(C.transport)}</span>
        <span class="k">Immediate work</span><span class="mono">${money(C.immediate_service_low)}–${money(C.immediate_service_high)}</span>
        <span class="k">Overdue allowance</span><span class="mono">${money(C.overdue_allowance)}</span>
        <span class="k">Risk reserve</span><span class="mono">${money(C.risk_reserve)}</span>
        <span class="k">Tax &amp; registration</span><span class="mono">${money(C.tax_and_registration)}</span>
        <span class="k"><b>All-in</b></span><span class="mono"><b>${money(C.all_in_low)}–${money(C.all_in_high)}</b></span>
        <span class="k">Recommended offer</span><span class="mono">${money(C.offer_low)}–${money(C.offer_high)}</span>
        <span class="k"><b>Maximum price / hammer</b></span><span class="mono"><b>${money(C.max_price)}</b></span></div>
        ${C.notes?.length ? `<ul class="list small" style="margin-top:8px">${C.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}</div>`));
    }
    if (S) {
      side.appendChild(h(`<div class="panel accent-teal"><h3>Score ${S.total}/100 <span class="muted small">confidence ${A.confidence}</span></h3><div class="scores">${CATS.map(([k, label, max]) => `<div class="score-row"><span title="${esc(E.ratings?.[k]?.rationale || "")}">${label}</span><span class="bar"><i style="width:${(S[k] / max) * 100}%"></i></span><span class="mono">${S[k]}/${max}</span></div>`).join("")}</div>
        ${S.caps_applied?.length ? `<p class="muted small" style="margin:8px 0 0">Caps: ${S.caps_applied.map(esc).join("; ")}</p>` : ""}</div>`));
    } else if (N.prelim_breakdown) {
      const B = N.prelim_breakdown;
      side.appendChild(h(`<div class="panel accent-mustard"><h3>Preliminary ${Math.round(prelimOf(l) ?? 0)}/100 <span class="muted small">same rubric, cheap inputs</span></h3><div class="scores">${CATS.map(([k, label, max]) => B[k] ? `<div class="score-row"><span title="${esc(B[k].why)}">${label}</span><span class="bar"><i class="prelim" style="width:${(B[k].points / max) * 100}%"></i></span><span class="mono">${B[k].points}/${max}</span></div>` : "").join("")}</div>
        <p class="muted small" style="margin:8px 0 0">${CATS.map(([k]) => B[k] ? `<b>${esc(k.replace(/_/g, " "))}:</b> ${esc(B[k].why)}` : "").filter(Boolean).join(" · ")}</p>
        ${N.ratings ? "" : `<p class="small" style="margin:8px 0 0">Documentation, condition and spec are defaults until this listing is re-normalized (Policy page → Re-normalize).</p>`}</div>`));
    }
    side.appendChild(h(`<div class="panel"><h3>Facts</h3><div class="facts">${[["Year", l.year], ["Mileage", l.mileage ? num(l.mileage) + " mi" : null], ["Engine", l.engine || (l.engine_liters ? l.engine_liters + "L" : null)], ["Transmission", l.transmission], ["Drivetrain", l.drivetrain], ["Body", l.body_style], ["Exterior", l.exterior_color], ["Interior", l.interior_color], ["Title", l.title_status], ["Accidents", l.accidents], ["Owners", l.num_owners], ["Seller", [l.seller_type, l.seller_name].filter(Boolean).join(" · ")], ["Listed", l.listing_date], ["Auction ends", l.auction_end || l.raw?.time_left]].filter(([, v]) => v != null && v !== "").map(([k, v]) => `<div><div class="k">${k}</div><div class="v">${esc(v)}</div></div>`).join("")}</div></div>`));

    const hist = (l.history || []).filter((s) => s.price);
    const hp = h(`<div class="panel"><h3>Price &amp; availability</h3><div class="spark" id="spark"></div><table class="checks">${(l.history || []).slice().reverse().slice(0, 8).map((s) => `<tr><td class="mono small">${s.t.slice(0, 10)}</td><td><span class="mono">${money(s.price)}</span> <span class="muted small">${esc(s.kind || "")} ${esc(s.availability || "")}${s.bids != null ? " · " + s.bids + " bids" : ""}</span></td></tr>`).join("") || `<tr><td class="muted">no history yet</td></tr>`}</table></div>`);
    side.appendChild(hp);
    if (hist.length >= 2) sparkline($("#spark", hp), hist.map((s) => ({ x: new Date(s.t).getTime(), y: s.price }))); else $("#spark", hp).remove();

    if (l.profile_key) {
      side.appendChild(h(`<div class="panel accent-walnut"><h3>Market context</h3><div class="kv">
        <span class="k">Sold comps</span><span class="mono">${market.sold_count ?? 0}${market.sold_median ? " · median " + money(market.sold_median) : ""}</span>
        <span class="k">Active peers</span><span class="mono">${peers.length}${market.asking_median ? " · median " + money(market.asking_median) : ""}</span>
        ${market.mileage_median ? `<span class="k">Median miles</span><span class="mono">${num(market.mileage_median)}</span>` : ""}</div>
        <p class="small" style="margin-bottom:0"><a href="#/market?p=${esc(l.profile_key)}">Open market view →</a>${comps.length ? "" : " (no comps yet)"}</p></div>`));
    }
  }

  // ---------- market ----------
  function renderMarket(app, pkey) {
    const profiles = state.data.profiles;
    const key = pkey || state.filters.profiles[0] || profiles[0]?.key;
    const prof = state.profiles.get(key);
    app.appendChild(h(`<div class="hero"><div><h1>State of the market</h1><p>Sold listings and ended auctions become comps; active candidates plot against them.</p></div>
      <select id="mp">${profiles.map((p) => `<option value="${p.key}" ${p.key === key ? "selected" : ""}>${esc(p.label)}</option>`).join("")}</select></div>`));
    $("#mp", app).onchange = (e) => (location.hash = "#/market?p=" + e.target.value);
    if (!prof) return app.appendChild(h(`<div class="empty"><h2>No profiles</h2></div>`));
    const rows = state.data.listings.filter((l) => l.profile_key === key);
    const comps = rows.filter((l) => l.role === "comp"), actives = rows.filter((l) => l.role === "candidate" && l.availability === "active");
    const m = state.data.markets?.[key] || {};
    app.appendChild(h(`<div class="tiles">
      <div class="tile"><div class="k">Sold comps</div><div class="v">${m.sold_count ?? 0}</div><div class="s">${m.comp_count ?? 0} incl. ended</div></div>
      <div class="tile"><div class="k">Median sold</div><div class="v">${money(m.sold_median)}</div><div class="s">${m.sold_low ? money(m.sold_low) + " – " + money(m.sold_high) : "—"}</div></div>
      <div class="tile"><div class="k">Median asking</div><div class="v">${money(m.asking_median)}</div><div class="s">${actives.length} active</div></div>
      <div class="tile"><div class="k">Median mileage</div><div class="v">${num(m.mileage_median)}</div><div class="s">all rows</div></div>
      <div class="tile"><div class="k">Ask vs sold</div><div class="v">${m.sold_median && m.asking_median ? (100 * (m.asking_median / m.sold_median - 1)).toFixed(0) + "%" : "—"}</div><div class="s">median asking over median sold</div></div>
    </div>`));
    if (prof.market_notes) app.appendChild(h(`<div class="panel accent-walnut"><h3>What moves price</h3><p style="margin:0">${esc(prof.market_notes)}</p></div>`));
    const pts = rows.filter((l) => l.mileage && (l.sold_price || l.price)).map((l) => ({ x: l.mileage, y: l.sold_price || l.price, l, sold: l.role === "comp" }));
    const ch = h(`<div class="panel"><h3>Price vs mileage</h3><div class="legend"><span><i style="background:var(--data-a)"></i>Sold / ended</span><span><i style="background:var(--data-b)"></i>Active</span></div><div class="chart" id="scatter"></div></div>`);
    app.appendChild(ch);
    if (pts.length) scatter($("#scatter", ch), pts); else $("#scatter", ch).innerHTML = `<p class="muted">Nothing to plot yet.</p>`;
    const tbl = h(`<div class="panel"><h3>Comps</h3>${comps.length ? "" : `<p class="muted">No comps yet. Sync with "Include sold / ended" on, and add ended auctions from BaT and Cars &amp; Bids watch lists.</p>`}<div class="tablewrap" id="ct"></div></div>`);
    app.appendChild(tbl);
    let sortKey = "date", dir = -1;
    const draw = () => {
      const r = comps.slice().sort((a, b) => { const va = cell(a, sortKey), vb = cell(b, sortKey); return (va > vb ? 1 : va < vb ? -1 : 0) * dir; });
      $("#ct", tbl).innerHTML = `<table class="data"><thead><tr>${[["year", "Year"], ["title", "Listing"], ["mileage", "Miles"], ["price", "Price"], ["avail", "Result"], ["site", "Site"], ["date", "Date"]].map(([k, v]) => `<th data-k="${k}">${v}${sortKey === k ? (dir > 0 ? " ↑" : " ↓") : ""}</th>`).join("")}</tr></thead>
        <tbody>${r.map((l) => `<tr><td>${l.year ?? "—"}</td><td><a href="#/l/${l.id}">${esc(title(l))}</a><div class="muted small">${esc([l.trim, l.transmission, l.location].filter(Boolean).join(" · "))}</div></td><td class="mono">${num(l.mileage)}</td><td class="mono">${money(l.sold_price || l.price)}</td><td>${availChip(l.availability)}${l.price_kind === "reserve_not_met" ? ` <span class="muted small">RNM</span>` : ""}</td><td>${siteChip(l.site)}</td><td class="mono small">${esc((l.listing_date || l.auction_end || l.last_seen || "").slice(0, 10))}</td></tr>`).join("")}</tbody></table>`;
      $("#ct", tbl).querySelectorAll("th").forEach((th) => (th.onclick = () => { if (sortKey === th.dataset.k) dir *= -1; else { sortKey = th.dataset.k; dir = 1; } draw(); }));
    };
    const cell = (l, k) => ({ year: l.year ?? 0, title: title(l), mileage: l.mileage ?? 0, price: l.sold_price || l.price || 0, avail: l.availability, site: l.site, date: l.listing_date || l.auction_end || l.last_seen || "" }[k]);
    if (comps.length) draw();
  }

  // ---------- profiles ----------
  function renderProfiles(app) {
    app.appendChild(h(`<div class="hero"><div><h1>Profiles</h1><p>What "good" looks like per model: weak points, the PPI checklist, and how the axes are weighted. AI-generated profiles stay flagged until you verify them.</p></div></div>`));
    state.data.profiles.forEach((p) => {
      const n = state.data.listings.filter((l) => l.profile_key === p.key).length;
      const d = h(`<details class="profile"><summary><span>${esc(p.label)}</span>${p.verified ? `<span class="chip olive">verified</span>` : `<span class="chip mustard">AI-generated · unverified</span>`}<span class="chip">${p.source}</span><span class="muted small">${n} listing${n === 1 ? "" : "s"}</span><span class="spacer"></span>${state.local && !p.verified ? `<button class="btn sm" data-verify="${p.key}">Mark verified</button>` : ""}</summary>
        <p class="muted small">${esc([p.make, (p.models || []).join(", "), p.years?.length === 2 ? p.years.join("–") : ""].filter(Boolean).join(" · "))}</p>
        <p>${esc(p.framing || "")}</p>
        <h3>Weak points</h3><p style="white-space:pre-wrap">${esc(p.weak_points || "")}</p>
        ${p.market_notes ? `<h3>Market notes</h3><p>${esc(p.market_notes)}</p>` : ""}
        <h3>Weights</h3><div class="weights">${Object.entries(p.weights || {}).sort((a, b) => b[1] - a[1]).map(([k, v]) => `<span class="chip teal">${esc(k.replace("_", " "))} <span class="mono">${v.toFixed(2)}</span></span>`).join("")}</div>
        ${p.mission_default ? `<p class="small"><b>Default mission:</b> ${esc(missionLabel(p.mission_default))}${p.risk_reserve ? ` · <b>risk reserve</b> ${money(p.risk_reserve)}` : ""}${p.automatic_ok ? " · automatic OK" : ""}</p>` : ""}
        ${p.critical_evidence?.length ? `<h3 style="margin-top:10px">Model-critical evidence</h3><ul class="list">${p.critical_evidence.map((c) => `<li>${esc(c.label)} ${c.severity === "hard" ? `<span class="chip rose">hard gate</span>` : `<span class="chip mustard">conditional</span>`}</li>`).join("")}</ul>` : ""}
        ${p.dealbreakers?.length ? `<h3 style="margin-top:10px">Dealbreaker rules</h3><ul class="list">${p.dealbreakers.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
        <h3 style="margin-top:10px">Checklist</h3><ul class="list">${(p.checks || []).map((c) => `<li>${esc(c.label)}</li>`).join("")}</ul>
        <p class="muted small">Immediate: ${esc(p.immediate_repairs || "—")}<br>12-month: ${esc(p.repairs_12mo || "—")}</p></details>`);
      const vb = $("[data-verify]", d);
      if (vb) vb.onclick = async (e) => { e.preventDefault(); try { await api(`/api/profiles/${p.key}`, "PATCH", { verified: true }); await loadData(); route(); toast("Profile verified"); } catch (err) { toast(err.message); } };
      app.appendChild(d);
    });
  }

  // ---------- settings (temporary state; durable preferences live in code) ----------
  async function renderSettings(app) {
    app.appendChild(h(`<div class="hero"><div><h1>Policy state</h1><p>Temporary preferences and thresholds: budget, urgency mode, current vehicles, exclusions, fees, transport, tax. Durable preferences and the scoring model are versioned in code with the guide.</p></div></div>`));
    if (!state.local) return app.appendChild(h(`<div class="empty"><h2>Local only</h2><p>Edit policy state on the local workbench. Published policy version: ${esc(state.data.policy_version || "—")}</p></div>`));
    let cfg;
    try { cfg = await api("/api/settings"); } catch (e) { return app.appendChild(h(`<div class="empty"><h2>${esc(e.message)}</h2></div>`)); }
    const tools = h(`<div class="panel"><h3>Scores</h3><div class="row"><button class="btn sm warm" id="assess-all">Quick-assess all unassessed active candidates (Sonnet, ~30¢ each)</button></div><div class="row" style="margin-top:8px"><button class="btn sm" id="rescore">Recompute preliminary scores (free)</button><button class="btn sm warm" id="renorm-missing">Re-normalize listings missing ratings (${esc((state.health?.models?.fast || "fast model").replace("claude-", "").replace(/-\d.*$/, ""))}, ~1–2¢ each)</button><button class="btn sm ghost" id="renorm-all">Re-normalize everything</button><span class="muted small" id="tool-status"></span></div><p class="muted small" style="margin:8px 0 0">Preliminary scores use the guide's 100-point rubric: documentation 30, condition 25, price/value 15, mission fit 15, logistics 10, spec 5. Price, budget, transmission and location are computed; documentation, condition and spec come from the fast model's read of the listing.</p></div>`);
    app.appendChild(tools);
    const run = async (path, label, q = "") => { $("#tool-status", tools).textContent = label + "…"; setTimeout(watchTask, 300); try { const r = await api(path + q, "POST"); $("#tool-status", tools).textContent = JSON.stringify(r).slice(0, 200); await loadData(); } catch (e) { $("#tool-status", tools).textContent = e.message; } pollTask(); };
    $("#rescore", tools).onclick = () => run("/api/rescore", "Rescoring");
    $("#assess-all", tools).onclick = () => { const n = state.data.listings.filter((l) => l.role === "candidate" && l.availability === "active" && l.profile_key && !l.assessment).length; if (!n) return toast("Nothing unassessed"); if (confirm(`Quick-assess ${n} listing(s) on Sonnet, roughly $${(n * 0.3).toFixed(2)}? This runs one at a time and can take ${Math.ceil(n * 1.2)} minutes.`)) run("/api/assess-all", `Quick-assessing ${n} listing(s)`, "?tier=quick"); };
    $("#renorm-missing", tools).onclick = () => run("/api/renormalize-all", "Re-normalizing (this can take a few minutes)");
    $("#renorm-all", tools).onclick = () => { if (confirm("Re-run the fast model on every listing?")) run("/api/renormalize-all", "Re-normalizing everything", "?only_missing_ratings=false"); };
    const panel = h(`<div class="panel"><div class="row" style="justify-content:space-between"><h3>Policy ${esc(cfg.policy_version)}</h3><div class="row"><button class="btn sm" id="save">Save</button><button class="btn sm ghost" id="reset">Reset to defaults</button></div></div>
      <p class="muted small">JSON. Unknown keys are kept; nested objects merge. Urgency mode must be one of accelerated_bridge, emergency, casual_search.</p>
      <textarea class="notes mono" id="json" style="min-height:420px">${esc(JSON.stringify(cfg.state, null, 2))}</textarea></div>`);
    app.appendChild(panel);
    $("#save", panel).onclick = async () => { try { const body = JSON.parse($("#json", panel).value); const r = await api("/api/settings", "PUT", body); $("#json", panel).value = JSON.stringify(r.state, null, 2); toast("Saved"); } catch (e) { toast("Not saved: " + e.message, 5000); } };
    $("#reset", panel).onclick = async () => { if (!confirm("Reset policy state to code defaults?")) return; const r = await api("/api/settings/reset", "POST"); $("#json", panel).value = JSON.stringify(r.state, null, 2); toast("Reset"); };
  }

  // ---------- compare ----------
  function renderCompare(app) {
    const rows = state.compare.map((id) => state.byId.get(id)).filter(Boolean);
    app.appendChild(h(`<div class="hero"><div><h1>Compare</h1><p>Side by side. Pick up to four from the board.</p></div><a class="btn sm ghost" href="#/">← Board</a></div>`));
    if (!rows.length) return app.appendChild(h(`<div class="empty"><h2>Nothing selected</h2></div>`));
    const facts = [["Price", (l) => money(l.sold_price || l.price)], ["Mileage", (l) => l.mileage ? num(l.mileage) + " mi" : "—"], ["Year", (l) => l.year ?? "—"], ["Trim", (l) => l.trim || "—"], ["Engine", (l) => l.engine || "—"], ["Transmission", (l) => l.transmission || "—"], ["Location", (l) => l.location || "—"], ["Listed", listedAge], ["Site", (l) => siteName(l.site)],
      ["Score", (l) => scoreOf(l) ?? "—"], ["Confidence", (l) => l.assessment?.confidence ?? "—"], ["Verdict", (l) => verdictOf(l) || "—"], ["Mission", (l) => missionLabel(l.mission)], ["Max price", (l) => money(l.assessment?.costs?.max_price)], ["All-in", (l) => l.assessment ? money(l.assessment.costs.all_in_low) + "–" + money(l.assessment.costs.all_in_high) : "—"], ["Hard gates", (l) => (l.assessment?.gates || []).filter((g) => g.kind !== "conditional").length], ["Unresolved", (l) => (l.assessment?.gates || []).filter((g) => g.kind === "conditional").length],
      ["Red flags", (l) => (l.normalized?.red_flags || []).length], ["Status", (l) => l.status || "New"]];
    const c = h(`<div class="compare">${rows.map((l) => `<div class="col">${photo(l) ? `<img src="${esc(photo(l))}" alt="">` : ""}<h3 style="margin:10px 0 6px"><a href="#/l/${l.id}">${esc(title(l))}</a></h3>
      <div class="kv">${facts.map(([k, f]) => `<span class="k">${k}</span><span class="mono">${esc(f(l))}</span>`).join("")}</div>
      ${l.assessment ? `<h3 style="margin-top:12px">Main risks</h3><ul class="list small">${(l.assessment.evidence.concerns || []).slice(0, 4).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}</div>`).join("")}</div>`);
    app.appendChild(c);
  }

  // ---------- charts (plain SVG) ----------
  const tip = h(`<div class="tip" hidden></div>`); document.body.appendChild(tip);
  function showTip(e, html) { tip.innerHTML = html; tip.hidden = false; tip.style.left = e.clientX + 12 + "px"; tip.style.top = e.clientY + 12 + "px"; }
  function hideTip() { tip.hidden = true; }

  function scatter(el, pts) {
    const W = el.clientWidth || 800, H = 320, m = { t: 12, r: 16, b: 34, l: 62 };
    const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y);
    const x0 = 0, x1 = Math.max(...xs) * 1.05, y0 = Math.min(...ys) * 0.9, y1 = Math.max(...ys) * 1.05;
    const sx = (v) => m.l + (v - x0) / (x1 - x0) * (W - m.l - m.r), sy = (v) => H - m.b - (v - y0) / (y1 - y0) * (H - m.t - m.b);
    const ticks = (a, b, n) => { const step = niceStep((b - a) / n); const out = []; for (let v = Math.ceil(a / step) * step; v <= b; v += step) out.push(v); return out; };
    const svg = h(`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
      <g class="grid">${ticks(y0, y1, 5).map((v) => `<line x1="${m.l}" x2="${W - m.r}" y1="${sy(v)}" y2="${sy(v)}"/>`).join("")}</g>
      <g class="axis"><line x1="${m.l}" x2="${W - m.r}" y1="${H - m.b}" y2="${H - m.b}"/></g>
      ${ticks(y0, y1, 5).map((v) => `<text x="${m.l - 8}" y="${sy(v) + 4}" text-anchor="end">$${Math.round(v / 1000)}k</text>`).join("")}
      ${ticks(x0, x1, 6).map((v) => `<text x="${sx(v)}" y="${H - m.b + 18}" text-anchor="middle">${Math.round(v / 1000)}k mi</text>`).join("")}
      ${pts.map((p, i) => `<circle data-i="${i}" cx="${sx(p.x)}" cy="${sy(p.y)}" r="${p.sold ? 5 : 6}" fill="${p.sold ? "var(--data-a)" : "var(--data-b)"}" stroke="var(--card)" stroke-width="2" style="cursor:pointer"/>`).join("")}
    </svg>`);
    svg.querySelectorAll("circle").forEach((c) => {
      const p = pts[c.dataset.i];
      c.onmousemove = (e) => showTip(e, `<b>${esc(title(p.l))}</b><br>${money(p.y)} · ${num(p.x)} mi · ${esc(p.l.availability)}`);
      c.onmouseleave = hideTip;
      c.onclick = () => (location.hash = "#/l/" + p.l.id);
    });
    el.innerHTML = ""; el.appendChild(svg);
  }
  function niceStep(raw) { const p = Math.pow(10, Math.floor(Math.log10(raw))); const r = raw / p; return (r < 1.5 ? 1 : r < 3.5 ? 2 : r < 7.5 ? 5 : 10) * p; }
  function sparkline(el, pts) {
    const W = el.clientWidth || 300, H = 56, pad = 6;
    const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y);
    const x0 = Math.min(...xs), x1 = Math.max(...xs) || x0 + 1, y0 = Math.min(...ys), y1 = Math.max(...ys) || y0 + 1;
    const sx = (v) => pad + (v - x0) / (x1 - x0 || 1) * (W - 2 * pad), sy = (v) => H - pad - (v - y0) / (y1 - y0 || 1) * (H - 2 * pad);
    const d = pts.map((p, i) => (i ? "L" : "M") + sx(p.x) + " " + sy(p.y)).join(" ");
    el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><path d="${d}" fill="none" stroke="var(--data-b)" stroke-width="2"/>${pts.map((p) => `<circle cx="${sx(p.x)}" cy="${sy(p.y)}" r="4" fill="var(--data-b)" stroke="var(--card)" stroke-width="2"/>`).join("")}</svg>`;
  }

  // ---------- global wiring ----------
  $("#search").oninput = (e) => { state.q = e.target.value; if ((location.hash || "#/") === "#/" || location.hash.startsWith("#/?")) { const list = $("#list"); if (list) { route(); } } else location.hash = "#/"; };
  $("#publish").onclick = async (e) => { e.target.disabled = true; e.target.textContent = "Publishing…"; try { const r = await api("/api/publish", "POST"); toast(/Everything up-to-date|nothing to commit/.test(r.git) ? "Nothing new to publish" : "Published"); } catch (err) { toast("Publish failed: " + err.message, 5000); } e.target.disabled = false; e.target.textContent = "Publish"; };

  loadData().then(() => { route(); if (state.local) watchTask(); }).catch((e) => { $("#app").innerHTML = `<div class="empty"><h2>Could not load data</h2><p>${esc(e.message)}</p></div>`; });
})();
