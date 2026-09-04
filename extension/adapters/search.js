// Search-results extractor for provenance investigations. Runs on the search
// engines and venue search pages; background.js asks it for {type:"search_results"}.
(function () {
  const S = window.__scout || (window.__scout = {});
  const text = (el) => (el ? (el.innerText || el.textContent || "") : "").replace(/\s+/g, " ").trim();
  const clean = (href) => {
    try {
      const u = new URL(href, location.href);
      if (/duckduckgo\.com$/.test(u.hostname) && u.pathname.startsWith("/l/")) return decodeURIComponent(u.searchParams.get("uddg") || href);
      if (/bing\.com$/.test(u.hostname) && u.pathname.startsWith("/ck/")) { const m = href.match(/u=a1([^&]+)/); if (m) { try { return atob(m[1].replace(/-/g, "+").replace(/_/g, "/")); } catch (e) {} } }
      if (/google\./.test(u.hostname) && u.pathname === "/url") return u.searchParams.get("q") || u.searchParams.get("url") || href;
      return u.href;
    } catch (e) { return href; }
  };
  const push = (out, seen, url, title, snippet) => {
    url = clean(url);
    if (!/^https?:/.test(url) || seen.has(url)) return;
    if (/^(https?:\/\/)?(www\.)?(duckduckgo|bing|google)\./.test(url)) return;
    seen.add(url);
    out.push({ url, title: (title || "").slice(0, 300), snippet: (snippet || "").slice(0, 1200) });
  };
  const engines = {
    "html.duckduckgo.com": () => Array.from(document.querySelectorAll(".result")).map((r) => [r.querySelector("a.result__a"), r.querySelector(".result__snippet")]),
    "duckduckgo.com": () => Array.from(document.querySelectorAll("article, .result")).map((r) => [r.querySelector("a[href]"), r]),
    "www.bing.com": () => Array.from(document.querySelectorAll("li.b_algo")).map((r) => [r.querySelector("h2 a"), r.querySelector(".b_caption") || r]),
    "www.google.com": () => Array.from(document.querySelectorAll("div#search a h3")).map((h3) => [h3.closest("a"), h3.closest("div[data-hveid], div.g") || h3.parentElement]),
    "www.ebay.com": () => Array.from(document.querySelectorAll("li.s-item, li[data-viewport]")).map((r) => [r.querySelector("a.s-item__link, a[href*='/itm/']"), r]),
    "www.classic.com": () => Array.from(document.querySelectorAll("a[href*='/veh/'], a[href*='/listing/']")).map((a) => [a, a.closest("div, li, article") || a]),
    "www.reddit.com": () => Array.from(document.querySelectorAll("a[href*='/comments/']")).map((a) => [a, a.closest("article, div[data-testid], shreddit-post, div") || a]),
    "bringatrailer.com": () => Array.from(document.querySelectorAll("a[href*='/listing/']")).map((a) => [a, S.cardOf ? S.cardOf(a) : a.parentElement]),
    "www.facebook.com": () => Array.from(document.querySelectorAll("a[href*='/posts/'], a[href*='permalink'], a[href*='/marketplace/item/'], a[href*='/groups/'][href*='/posts/'], a[href*='story_fbid']")).map((a) => [a, S.cardOf ? S.cardOf(a, 10) : a.parentElement]),
  };
  function extract() {
    const out = [], seen = new Set();
    const fn = engines[location.hostname] || engines[location.hostname.replace(/^m\./, "www.")];
    if (fn) for (const [a, card] of fn()) { if (a && a.href) push(out, seen, a.href, text(a) || text(card).slice(0, 120), text(card)); }
    if (!out.length) for (const a of document.querySelectorAll("a[href]")) { const t = text(a); if (t.length > 15 && !a.href.startsWith(location.origin + "/#")) push(out, seen, a.href, t, text(a.parentElement)); }
    return out.slice(0, 40);
  }
  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg.type !== "search_results") return false;
    (async () => {
      try {
        await new Promise((r) => setTimeout(r, msg.settleMs || 1500));
        if (/facebook\.com|reddit\.com|bringatrailer\.com/.test(location.hostname) && S.autoScroll) await S.autoScroll(3, 900);
        sendResponse({ ok: true, results: extract(), page_url: location.href, page_text: (document.body.innerText || "").slice(0, 3000) });
      } catch (e) { sendResponse({ ok: false, error: String(e) }); }
    })();
    return true;
  });
})();
