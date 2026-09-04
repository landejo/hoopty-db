/* Hoopty Scout viewer. Vanilla JS, no build. Reads data/scout.json on GitHub
   Pages; talks to the local FastAPI server when served from it. */
(() => {
  const $ = (sel, el = document) => el.querySelector(sel);
  const h = (html) => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const money = (n) => (n == null ? "—" : "$" + Number(n).toLocaleString());
  const num = (n) => (n == null ? "—" : Number(n).toLocaleString());
  const STATUSES = ["New", "Pursue", "Verify", "Contacted", "PPI Scheduled", "Offer Made", "Pass", "Purchased"];

  const state = { data: null, local: false, filters: load("filters", { profile: "", site: "", avail: "active", status: "", analyzed: false, sort: "score", role: "candidate", view: "cards" }),
                  q: "", compare: load("compare", []), theme: load("theme", null) };

  function load(k, d) { try { const v = localStorage.getItem("scout." + k); return v ? JSON.parse(v) : d; } catch (e) { return d; } }
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
  function toast(msg, ms = 2600) { const t = h(`<div class="toast">${esc(msg)}</div>`); document.body.appendChild(t); setTimeout(() => t.remove(), ms); }

  // ---------- helpers ----------
  function ago(iso) {
    if (!iso) return "—";
    const d = (Date.now() - new Date(iso).getTime()) / 864e5;
    if (d < 1) return Math.max(1, Math.round(d * 24)) + "h ago";
    if (d < 45) return Math.round(d) + "d ago";
    return Math.round(d / 30) + "mo ago";
  }
  function listedAge(l) { return l.listing_date ? ago(l.listing_date) : l.first_seen ? "seen " + ago(l.first_seen) : "—"; }
  function siteName(k) { return (state.data.sites || {})[k] || k; }
  function siteChip(k) { const c = { facebook: "teal", cargurus: "olive", carscom: "mustard", autotrader: "slate", carsandbids: "orange", bat: "rose" }[k] || ""; return `<span class="chip ${c}">${esc(siteName(k))}</span>`; }
  function scoreOf(l) { return l.analysis?.deal_score ?? null; }
  function prelimOf(l) { return l.prelim_score ?? null; }
  function badge(l) {
    const s = scoreOf(l);
    if (s != null) return `<span class="badge ${s >= 70 ? "hi" : s >= 45 ? "mid" : "lo"}" title="Deal score (Opus)">${s}</span>`;
    const p = prelimOf(l);
    if (p != null) return `<span class="badge prelim ${p >= 3.8 ? "hi" : p >= 2.8 ? "mid" : "lo"}" title="Preliminary score (Haiku, 1–5)">${p.toFixed(1)}</span>`;
    return `<span class="badge none">n/a</span>`;
  }
  function availChip(a) { const c = { active: "olive", sold: "rose", ended: "walnut", removed: "slate" }[a] || ""; return `<span class="chip ${c === "walnut" ? "mustard" : c}">${esc(a)}</span>`; }
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
    if (parts[0] === "compare") return renderCompare(app);
    return renderBoard(app);
  }
  window.addEventListener("hashchange", () => (state.local ? loadData().then(route) : route()));

  // ---------- board ----------
  function filtered() {
    const f = state.filters, q = state.q.trim().toLowerCase();
    let rows = state.data.listings.filter((l) => {
      if (f.role && l.role !== f.role) return false;
      if (f.profile && l.profile_key !== f.profile) return false;
      if (f.site && l.site !== f.site) return false;
      if (f.avail && l.availability !== f.avail) return false;
      if (f.status && l.status !== f.status) return false;
      if (f.analyzed && !l.analysis) return false;
      if (q) {
        const hay = [title(l), l.location, l.model, l.trim, l.engine, l.exterior_color, l.notes, l.normalized?.prelim_summary, (l.options || []).join(" ")].join(" ").toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
    const sorters = {
      score: (a, b) => (scoreOf(b) ?? -1) - (scoreOf(a) ?? -1) || (prelimOf(b) ?? -1) - (prelimOf(a) ?? -1),
      price: (a, b) => (a.price ?? 9e9) - (b.price ?? 9e9),
      price_desc: (a, b) => (b.price ?? -1) - (a.price ?? -1),
      mileage: (a, b) => (a.mileage ?? 9e9) - (b.mileage ?? 9e9),
      newest: (a, b) => (b.listing_date || b.first_seen || "").localeCompare(a.listing_date || a.first_seen || ""),
      year: (a, b) => (b.year ?? 0) - (a.year ?? 0),
    };
    rows.sort(sorters[f.sort] || sorters.score);
    rows.sort((a, b) => (b.pinned ? 1 : 0) - (a.pinned ? 1 : 0));
    return rows;
  }

  function renderBoard(app) {
    const L = state.data.listings;
    const cands = L.filter((l) => l.role === "candidate" && l.availability === "active");
    const analyzed = cands.filter((l) => l.analysis).length;
    const comps = L.filter((l) => l.role === "comp").length;
    const pursue = cands.filter((l) => l.status === "Pursue" || l.analysis?.verdict === "Pursue").length;
    app.appendChild(h(`
      <div class="hero">
        <div><h1>The board</h1><p>Everything you've saved, normalized and scored. Sold and ended listings feed the <a href="#/market">market view</a>.</p></div>
        <div class="tiles" style="margin:0;min-width:520px">
          <div class="tile"><div class="k">Active candidates</div><div class="v">${cands.length}</div><div class="s">${analyzed} deep-analyzed</div></div>
          <div class="tile"><div class="k">Pursue</div><div class="v">${pursue}</div><div class="s">by verdict or status</div></div>
          <div class="tile"><div class="k">Market comps</div><div class="v">${comps}</div><div class="s">sold + ended</div></div>
          <div class="tile"><div class="k">Profiles</div><div class="v">${state.data.profiles.length}</div><div class="s">${state.data.profiles.filter((p) => !p.verified).length} unverified</div></div>
        </div>
      </div>`));
    const f = state.filters;
    const sites = Object.entries(state.data.sites);
    const bar = h(`
      <div class="filters">
        <span class="seg" id="role"><button data-v="candidate" class="${f.role === "candidate" ? "on" : ""}">Candidates</button><button data-v="comp" class="${f.role === "comp" ? "on" : ""}">Comps</button><button data-v="" class="${f.role === "" ? "on" : ""}">All</button></span>
        <select id="f-profile"><option value="">All profiles</option>${state.data.profiles.map((p) => `<option value="${p.key}" ${f.profile === p.key ? "selected" : ""}>${esc(p.label)}</option>`).join("")}</select>
        <select id="f-site"><option value="">All sites</option>${sites.map(([k, v]) => `<option value="${k}" ${f.site === k ? "selected" : ""}>${esc(v)}</option>`).join("")}</select>
        <select id="f-avail"><option value="">Any availability</option>${["active", "sold", "ended", "removed"].map((a) => `<option ${f.avail === a ? "selected" : ""}>${a}</option>`).join("")}</select>
        <select id="f-status"><option value="">Any status</option>${STATUSES.map((s) => `<option ${f.status === s ? "selected" : ""}>${s}</option>`).join("")}</select>
        <select id="f-sort">${[["score", "Best score"], ["price", "Price ↑"], ["price_desc", "Price ↓"], ["mileage", "Mileage ↑"], ["newest", "Newest listed"], ["year", "Year ↓"]].map(([k, v]) => `<option value="${k}" ${f.sort === k ? "selected" : ""}>${v}</option>`).join("")}</select>
        <label><input type="checkbox" id="f-analyzed" ${f.analyzed ? "checked" : ""}> analyzed only</label>
        <span class="spacer"></span>
        <span class="seg" id="view"><button data-v="cards" class="${f.view === "cards" ? "on" : ""}">Cards</button><button data-v="table" class="${f.view === "table" ? "on" : ""}">Table</button></span>
      </div>`);
    app.appendChild(bar);
    const rerender = () => { save("filters", f); renderList(); };
    bar.querySelectorAll("#role button").forEach((b) => (b.onclick = () => { f.role = b.dataset.v; if (f.role === "comp") f.avail = ""; if (f.role === "candidate") f.avail = f.avail || "active"; route(); }));
    bar.querySelectorAll("#view button").forEach((b) => (b.onclick = () => { f.view = b.dataset.v; route(); }));
    $("#f-profile", bar).onchange = (e) => { f.profile = e.target.value; rerender(); };
    $("#f-site", bar).onchange = (e) => { f.site = e.target.value; rerender(); };
    $("#f-avail", bar).onchange = (e) => { f.avail = e.target.value; rerender(); };
    $("#f-status", bar).onchange = (e) => { f.status = e.target.value; rerender(); };
    $("#f-sort", bar).onchange = (e) => { f.sort = e.target.value; rerender(); };
    $("#f-analyzed", bar).onchange = (e) => { f.analyzed = e.target.checked; rerender(); };
    const list = h(`<div id="list"></div>`); app.appendChild(list);
    function renderList() {
      const rows = filtered();
      list.innerHTML = "";
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
    const v = l.analysis?.verdict;
    const el = h(`
      <article class="card ${l.role}" data-id="${l.id}">
        <div class="photo">${p ? `<img loading="lazy" src="${esc(p)}" alt="">` : `<div class="nophoto">⌁</div>`}
          <span class="score">${badge(l)}</span><span class="site">${siteChip(l.site)}</span></div>
        <div class="body">
          <div class="title">${esc(title(l))}</div>
          <div class="price">${l.role === "comp" && (l.sold_price || l.price) ? money(l.sold_price || l.price) + `<small>${l.availability === "sold" ? "sold" : esc(l.price_kind || "")}</small>` : money(l.price) + (l.price_kind && l.price_kind !== "asking" ? `<small>${esc(l.price_kind.replace("_", " "))}</small>` : "")}</div>
          <div class="meta"><span class="mono">${l.mileage ? num(l.mileage) + " mi" : "— mi"}</span><span>${esc(l.location || "—")}</span><span>${listedAge(l)}</span>${l.transmission ? `<span>${esc(l.transmission)}</span>` : ""}</div>
          <div class="foot">
            <div class="row" style="gap:6px">${v ? `<span class="chip ${v === "Pursue" ? "olive" : v === "Pass" ? "rose" : "mustard"}">${v}</span>` : ""}${flags ? `<span class="chip orange" title="${esc(l.normalized.red_flags.join("\n"))}">⚑ ${flags}</span>` : ""}${l.availability !== "active" ? availChip(l.availability) : ""}${l.pinned ? `<span class="chip mustard">★</span>` : ""}</div>
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
      ["Site", (l) => siteChip(l.site)], ["Listed", (l) => listedAge(l)], ["Verdict", (l) => esc(l.analysis?.verdict || "—")], ["Status", (l) => esc(l.status || "New")]];
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
    const A = l.analysis, N = l.normalized || {}, prof = state.profiles.get(l.profile_key);
    const photos = l.photos && l.photos.length ? l.photos : l.thumb ? [l.thumb] : [];
    const peers = state.data.listings.filter((x) => x.profile_key === l.profile_key && x.role === "candidate" && x.availability === "active" && x.id !== l.id);
    const comps = state.data.listings.filter((x) => x.profile_key === l.profile_key && x.role === "comp");
    const market = state.data.markets?.[l.profile_key] || {};
    const scoreRows = (scores, weights, cls = "") => Object.entries(scores || {}).sort((a, b) => (weights?.[b[0]] || 0) - (weights?.[a[0]] || 0))
      .map(([k, v]) => `<div class="score-row"><span>${esc(k.replace("_", " "))}${weights?.[k] ? ` <span class="muted small">×${weights[k].toFixed(2)}</span>` : ""}</span><span class="bar"><i class="${cls}" style="width:${v * 20}%"></i></span><span class="mono">${v}</span></div>`).join("");

    app.appendChild(h(`<div>
      <div class="row" style="justify-content:space-between;margin-bottom:12px">
        <a href="#/" class="btn sm ghost">← Board</a>
        <div class="row">${siteChip(l.site)}${availChip(l.availability)}${l.role === "comp" ? `<span class="chip dark">market comp</span>` : ""}<a class="btn sm" href="${esc(l.url)}" target="_blank" rel="noopener">Open listing ↗</a></div>
      </div>
      <div class="headline">
        <div><h1>${esc(title(l))}</h1><div class="muted">${esc([l.year, l.make, l.model, l.generation ? "(" + l.generation + ")" : "", l.trim].filter(Boolean).join(" "))} · ${esc(l.location || "location unknown")} · ${listedAge(l)}${prof ? ` · <a href="#/profiles">${esc(prof.label)}</a>${prof.verified ? "" : " <span class='chip mustard'>unverified profile</span>"}` : ""}</div></div>
        <div class="row" style="gap:18px">
          <div><div class="price">${money(l.sold_price || l.price)}</div><div class="muted small">${esc(l.price_kind ? l.price_kind.replace("_", " ") : "asking")}${l.price_pct_vs_sold != null ? ` · pricier than ${l.price_pct_vs_sold}% of sold comps` : ""}</div></div>
          ${A?.deal_score != null ? `<div class="dial" style="--pct:${A.deal_score}"><span>${A.deal_score}</span><small>deal</small></div>` : ""}
          ${A?.verdict ? `<div><div class="verdict ${esc(A.verdict)}">${esc(A.verdict)}</div><div class="muted small">confidence ${A.confidence ?? "—"}/5</div></div>` : ""}
        </div>
      </div></div>`));

    const main = h(`<div></div>`), side = h(`<div></div>`);
    const grid = h(`<div class="detail"></div>`); grid.append(main, side); app.appendChild(grid);

    // gallery
    if (photos.length) {
      const g = h(`<div class="gallery"><div class="main"><img src="${esc(photos[0])}" alt=""></div><div class="thumbs">${photos.map((p, i) => `<img src="${esc(p)}" class="${i === 0 ? "on" : ""}" alt="">`).join("")}</div></div>`);
      g.querySelectorAll(".thumbs img").forEach((im) => (im.onclick = () => { $(".main img", g).src = im.src; g.querySelectorAll(".thumbs img").forEach((x) => x.classList.toggle("on", x === im)); }));
      main.appendChild(g);
      main.appendChild(h(`<div style="height:16px"></div>`));
    }

    // analysis or quick read
    if (A) {
      main.appendChild(h(`<div class="panel accent-teal"><h3>Deep analysis <span class="chip teal">Opus</span><span class="muted small">${ago(l.analyzed_at)}</span></h3>
        <p>${esc(A.summary)}</p>${A.verdict_reasoning ? `<p><b>Why ${esc(A.verdict)}:</b> ${esc(A.verdict_reasoning)}</p>` : ""}${A.market_position ? `<h3 style="margin-top:12px">Market position</h3><p>${esc(A.market_position)}</p>` : ""}</div>`));
      if (A.dealbreakers?.length) main.appendChild(h(`<div class="panel accent-rose"><h3>Dealbreakers</h3><ul class="list">${A.dealbreakers.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>`));
      main.appendChild(h(`<div class="panel accent-olive"><h3>Positives</h3><ul class="list">${(A.positives || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>`));
      main.appendChild(h(`<div class="panel accent-mustard"><h3>Concerns</h3><ul class="list">${(A.concerns || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>`));
      if (A.checks?.length) main.appendChild(h(`<div class="panel"><h3>Model-specific checks (from the listing's evidence)</h3><table class="checks">${A.checks.map((c) => `<tr><td class="st-${c.status}">${esc(c.status)}</td><td><b>${esc(prof?.checks?.find((k) => k.key === c.key)?.label || c.key)}</b>${c.notes ? `<div class="muted small">${esc(c.notes)}</div>` : ""}</td></tr>`).join("")}</table></div>`));
      if (A.inspection_focus?.length) main.appendChild(h(`<div class="panel accent-walnut"><h3>PPI focus</h3><ul class="list">${A.inspection_focus.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>`));
      if (A.seller_questions?.length) main.appendChild(h(`<div class="panel accent-teal"><h3>Ask the seller</h3><ol class="list">${A.seller_questions.map((x) => `<li>${esc(x)}</li>`).join("")}</ol></div>`));
      if (A.negotiation?.length) main.appendChild(h(`<div class="panel accent-orange"><h3>Negotiation plays</h3><ul class="list">${A.negotiation.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>`));
    } else {
      main.appendChild(h(`<div class="panel accent-mustard"><h3>Quick read <span class="chip mustard">Haiku</span></h3>
        <p>${esc(N.prelim_summary || "Not normalized yet. Run a sync with the server's API key set.")}</p>
        ${N.highlights?.length ? `<b>Highlights</b><ul class="list">${N.highlights.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
        ${N.red_flags?.length ? `<b>Red flags</b><ul class="list">${N.red_flags.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}</div>`));
    }
    if (A && (N.red_flags?.length || N.highlights?.length)) main.appendChild(h(`<details class="panel"><summary class="muted">Quick read from sync (Haiku)</summary><p>${esc(N.prelim_summary || "")}</p>${N.red_flags?.length ? `<b>Red flags</b><ul class="list">${N.red_flags.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}</details>`));

    // description
    main.appendChild(h(`<details class="panel"><summary class="muted">Listing text as captured (${num((l.raw?.text_len) || 0) !== "0" ? "" : ""}${l.options?.length ? l.options.length + " options" : "raw"})</summary>${l.options?.length ? `<p><b>Options:</b> ${l.options.map(esc).join(", ")}</p>` : ""}<p class="muted small">Full raw text stays on the local server and is not published.</p></details>`));

    // ----- side -----
    if (state.local) {
      const act = h(`<div class="panel"><h3>Actions</h3>
        <div class="row"><button class="btn primary" id="analyze">${A ? "Re-analyze" : "Analyze"} with Opus <span class="muted small" style="color:inherit;opacity:.8">~$0.50–1.50</span></button><button class="btn sm ghost" id="renorm" title="Re-run Haiku normalization">Re-normalize</button></div>
        <div class="row" style="margin-top:10px"><label>Status <select id="status">${STATUSES.map((s) => `<option ${l.status === s ? "selected" : ""}>${s}</option>`).join("")}</select></label>
        <label><input type="checkbox" id="pin" ${l.pinned ? "checked" : ""}> pinned</label>
        <label>Role <select id="role"><option value="candidate" ${l.role === "candidate" ? "selected" : ""}>candidate</option><option value="comp" ${l.role === "comp" ? "selected" : ""}>comp</option></select></label></div>
        <label style="display:block;margin-top:10px">Profile <select id="prof"><option value="">— none —</option>${state.data.profiles.map((p) => `<option value="${p.key}" ${l.profile_key === p.key ? "selected" : ""}>${esc(p.label)}</option>`).join("")}</select></label>
        <textarea class="notes" id="notes" placeholder="Your notes (saved on blur)">${esc(l.notes || "")}</textarea></div>`);
      side.appendChild(act);
      $("#analyze", act).onclick = async (e) => {
        e.target.disabled = true; e.target.textContent = "Analyzing… (30–90s)";
        try { await api(`/api/listings/${l.id}/analyze`, "POST"); await loadData(); route(); toast("Analysis complete"); }
        catch (err) { toast("Analysis failed: " + err.message, 5000); e.target.disabled = false; e.target.textContent = "Analyze with Opus"; }
      };
      $("#renorm", act).onclick = async (e) => { e.target.disabled = true; try { await api(`/api/listings/${l.id}/renormalize`, "POST"); await loadData(); route(); toast("Re-normalized"); } catch (err) { toast(err.message, 4000); e.target.disabled = false; } };
      const patch = async (body) => { try { await api(`/api/listings/${l.id}`, "PATCH", body); Object.assign(l, body); toast("Saved"); } catch (err) { toast(err.message, 4000); } };
      $("#status", act).onchange = (e) => patch({ status: e.target.value });
      $("#pin", act).onchange = (e) => patch({ pinned: e.target.checked });
      $("#role", act).onchange = (e) => patch({ role: e.target.value });
      $("#prof", act).onchange = (e) => patch({ profile_key: e.target.value });
      $("#notes", act).onblur = (e) => { if (e.target.value !== (l.notes || "")) patch({ notes: e.target.value }); };
    } else {
      side.appendChild(h(`<div class="panel"><div class="row" style="justify-content:space-between"><span class="pill-status">${esc(l.status || "New")}</span>${l.pinned ? `<span class="chip mustard">★ pinned</span>` : ""}</div>${l.notes ? `<p style="white-space:pre-wrap">${esc(l.notes)}</p>` : `<p class="muted small">Notes and status are edited on the local workbench.</p>`}</div>`));
    }

    side.appendChild(h(`<div class="panel"><h3>Facts</h3><div class="facts">${[["Year", l.year], ["Mileage", l.mileage ? num(l.mileage) + " mi" : null], ["Engine", l.engine || (l.engine_liters ? l.engine_liters + "L" : null)], ["Transmission", l.transmission], ["Drivetrain", l.drivetrain], ["Body", l.body_style], ["Exterior", l.exterior_color], ["Interior", l.interior_color], ["Title", l.title_status], ["Accidents", l.accidents], ["Owners", l.num_owners], ["Seller", [l.seller_type, l.seller_name].filter(Boolean).join(" · ")], ["Listed", l.listing_date], ["Auction ends", l.auction_end || l.raw?.time_left]].filter(([, v]) => v != null && v !== "").map(([k, v]) => `<div><div class="k">${k}</div><div class="v">${esc(v)}</div></div>`).join("")}</div></div>`));

    if (A?.pricing && Object.keys(A.pricing).length) {
      const P = A.pricing;
      side.appendChild(h(`<div class="panel accent-orange"><h3>Pricing</h3><div class="kv">${[["Fair value", P.fair_value], ["Target offer", P.target_offer], ["Walk away", P.walk_away], ["Immediate repairs", P.immediate_repairs], ["12-month repairs", P.twelve_month_repairs]].filter(([, v]) => v != null).map(([k, v]) => `<span class="k">${k}</span><span class="mono">${money(v)}</span>`).join("")}${P.target_offer && P.immediate_repairs != null ? `<span class="k">All-in year one</span><span class="mono"><b>${money((P.target_offer || 0) + (P.immediate_repairs || 0) + (P.twelve_month_repairs || 0))}</b></span>` : ""}</div></div>`));
    }
    if (A?.scores && Object.keys(A.scores).length) side.appendChild(h(`<div class="panel accent-teal"><h3>Scores <span class="muted small">weighted by profile</span></h3><div class="scores">${scoreRows(A.scores, prof?.weights)}</div></div>`));
    else if (N.prelim_scores && Object.keys(N.prelim_scores).length) side.appendChild(h(`<div class="panel accent-mustard"><h3>Preliminary scores <span class="muted small">${prelimOf(l) != null ? prelimOf(l).toFixed(2) + " weighted" : ""}</span></h3><div class="scores">${scoreRows(N.prelim_scores, prof?.weights, "prelim")}</div></div>`));

    // price history
    const hist = (l.history || []).filter((s) => s.price);
    const hp = h(`<div class="panel"><h3>Price &amp; availability</h3><div class="spark" id="spark"></div><table class="checks">${(l.history || []).slice().reverse().slice(0, 8).map((s) => `<tr><td class="mono small">${s.t.slice(0, 10)}</td><td><span class="mono">${money(s.price)}</span> <span class="muted small">${esc(s.kind || "")} ${esc(s.availability || "")}${s.bids != null ? " · " + s.bids + " bids" : ""}</span></td></tr>`).join("") || `<tr><td class="muted">no history yet</td></tr>`}</table></div>`);
    side.appendChild(hp);
    if (hist.length >= 2) sparkline($("#spark", hp), hist.map((s) => ({ x: new Date(s.t).getTime(), y: s.price })));
    else $("#spark", hp).remove();

    // market context
    if (l.profile_key) {
      side.appendChild(h(`<div class="panel accent-walnut"><h3>Market context</h3><div class="kv">
        <span class="k">Sold comps</span><span class="mono">${market.sold_count ?? 0}${market.sold_median ? " · median " + money(market.sold_median) : ""}</span>
        <span class="k">Active peers</span><span class="mono">${peers.length}${market.asking_median ? " · median " + money(market.asking_median) : ""}</span>
        ${market.mileage_median ? `<span class="k">Median miles</span><span class="mono">${num(market.mileage_median)}</span>` : ""}</div>
        <p class="small" style="margin-bottom:0"><a href="#/market?p=${esc(l.profile_key)}">Open market view →</a>${comps.length ? "" : " (no comps yet: sync sold listings and ended auctions)"}</p></div>`));
    }
  }

  // ---------- market ----------
  function renderMarket(app, pkey) {
    const profiles = state.data.profiles;
    const key = pkey || state.filters.profile || profiles[0]?.key;
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
        ${p.dealbreakers?.length ? `<h3 style="margin-top:10px">Dealbreaker rules</h3><ul class="list">${p.dealbreakers.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
        <h3 style="margin-top:10px">Checklist</h3><ul class="list">${(p.checks || []).map((c) => `<li>${esc(c.label)}</li>`).join("")}</ul>
        <p class="muted small">Immediate: ${esc(p.immediate_repairs || "—")}<br>12-month: ${esc(p.repairs_12mo || "—")}</p></details>`);
      const vb = $("[data-verify]", d);
      if (vb) vb.onclick = async (e) => { e.preventDefault(); try { await api(`/api/profiles/${p.key}`, "PATCH", { verified: true }); await loadData(); route(); toast("Profile verified"); } catch (err) { toast(err.message); } };
      app.appendChild(d);
    });
  }

  // ---------- compare ----------
  function renderCompare(app) {
    const rows = state.compare.map((id) => state.byId.get(id)).filter(Boolean);
    app.appendChild(h(`<div class="hero"><div><h1>Compare</h1><p>Side by side. Pick up to four from the board.</p></div><a class="btn sm ghost" href="#/">← Board</a></div>`));
    if (!rows.length) return app.appendChild(h(`<div class="empty"><h2>Nothing selected</h2></div>`));
    const facts = [["Price", (l) => money(l.sold_price || l.price)], ["Mileage", (l) => l.mileage ? num(l.mileage) + " mi" : "—"], ["Year", (l) => l.year ?? "—"], ["Trim", (l) => l.trim || "—"], ["Engine", (l) => l.engine || "—"], ["Transmission", (l) => l.transmission || "—"], ["Location", (l) => l.location || "—"], ["Listed", listedAge], ["Site", (l) => siteName(l.site)],
      ["Deal score", (l) => scoreOf(l) ?? "—"], ["Verdict", (l) => l.analysis?.verdict || "—"], ["Prelim", (l) => prelimOf(l)?.toFixed(2) ?? "—"], ["Target offer", (l) => money(l.analysis?.pricing?.target_offer)], ["Year-one repairs", (l) => l.analysis?.pricing ? money((l.analysis.pricing.immediate_repairs || 0) + (l.analysis.pricing.twelve_month_repairs || 0)) : "—"],
      ["Red flags", (l) => (l.normalized?.red_flags || []).length], ["Status", (l) => l.status || "New"]];
    const c = h(`<div class="compare">${rows.map((l) => `<div class="col">${photo(l) ? `<img src="${esc(photo(l))}" alt="">` : ""}<h3 style="margin:10px 0 6px"><a href="#/l/${l.id}">${esc(title(l))}</a></h3>
      <div class="kv">${facts.map(([k, f]) => `<span class="k">${k}</span><span class="mono">${esc(f(l))}</span>`).join("")}</div>
      ${l.analysis ? `<h3 style="margin-top:12px">Concerns</h3><ul class="list small">${(l.analysis.concerns || []).slice(0, 5).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}</div>`).join("")}</div>`);
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

  loadData().then(route).catch((e) => { $("#app").innerHTML = `<div class="empty"><h2>Could not load data</h2><p>${esc(e.message)}</p></div>`; });
})();
