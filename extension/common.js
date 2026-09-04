// Shared helpers for site adapters. Each adapter sets window.__scoutAdapter =
// { site, isSavedPage(), isDetailPage(), collectSaved(), scrapeDetail() } and
// this file wires the message channel to background.js.
(function () {
  const S = (window.__scout = window.__scout || {});

  S.sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  S.text = (el) => (el ? (el.innerText || el.textContent || "") : "").replace(/\s+\n/g, "\n").trim();

  S.priceIn = (txt) => {
    const m = (txt || "").match(/\$\s?[\d,]{3,}/);
    return m ? m[0].replace(/\s/g, "") : "";
  };

  S.soldIn = (txt) => /\b(sold|no longer available|listing (has )?ended)\b/i.test(txt || "");
  S.endedIn = (txt) => /\b(bid to|reserve not met|auction ended|ended)\b/i.test(txt || "");

  // Scroll to the bottom repeatedly until the page stops growing (lazy lists).
  S.autoScroll = async function (maxRounds = 20, settleMs = 1200) {
    let lastH = 0;
    for (let i = 0; i < maxRounds; i++) {
      window.scrollTo(0, document.body.scrollHeight);
      await S.sleep(settleMs);
      const h = document.body.scrollHeight;
      if (h === lastH) break;
      lastH = h;
    }
    window.scrollTo(0, 0);
  };

  // Click every "See more"-style expander so descriptions are complete.
  S.expandAll = async function (labels = ["See more", "Show more", "Read more", "See full description"]) {
    const cands = Array.from(document.querySelectorAll("div[role=button], span[role=button], button, a"));
    let clicked = 0;
    for (const el of cands) {
      const t = (el.innerText || "").trim();
      if (labels.some((l) => t.toLowerCase() === l.toLowerCase()) && clicked < 8) {
        try { el.click(); clicked++; } catch (e) {}
      }
    }
    if (clicked) await S.sleep(600);
  };

  // Photos: large-ish images, deduped, in DOM order.
  S.photos = function (minSize = 250, cap = 40, filter = null) {
    const out = [];
    const seen = new Set();
    for (const img of document.images) {
      const src = img.currentSrc || img.src || "";
      if (!/^https?:/.test(src) || seen.has(src)) continue;
      if (filter && !filter(src)) continue;
      const w = img.naturalWidth || img.width || 0;
      const h = img.naturalHeight || img.height || 0;
      if (Math.max(w, h) < minSize) continue;
      seen.add(src);
      out.push(src);
      if (out.length >= cap) break;
    }
    return out;
  };

  // Walk up from an anchor to the nearest block that looks like a card.
  S.cardOf = function (a, maxDepth = 8) {
    let el = a;
    for (let i = 0; i < maxDepth && el && el.parentElement; i++) {
      el = el.parentElement;
      const t = el.innerText || "";
      const links = el.querySelectorAll("a[href]").length;
      if (t.length > 25 && links <= 6) return el;
    }
    return a.parentElement || a;
  };

  // Generic saved-list collector: anchors matching a URL pattern, one card each.
  S.collectByPattern = function (site, pattern, idFrom) {
    const items = new Map();
    for (const a of document.querySelectorAll("a[href]")) {
      const href = a.href;
      if (!pattern.test(href)) continue;
      const id = idFrom ? idFrom(href) : href;
      if (!id) continue;
      const card = S.cardOf(a);
      const cardText = S.text(card).slice(0, 600);
      const existing = items.get(id);
      if (existing && existing.card_text.length >= cardText.length) continue;
      const img = card.querySelector("img");
      const lines = cardText.split("\n").map((s) => s.trim()).filter(Boolean);
      const title = lines.find((l) => /\b(19|20)\d{2}\b/.test(l) && !/^\$/.test(l)) || lines[0] || "";
      items.set(id, {
        site,
        site_id: id,
        url: href.split("#")[0].split("?")[0] + (href.includes("#listing=") ? "#" + href.split("#")[1] : ""),
        title,
        price_text: S.priceIn(cardText),
        card_text: cardText,
        thumb: img ? img.currentSrc || img.src : null,
        sold: S.soldIn(cardText),
        ended: S.endedIn(cardText) && !S.soldIn(cardText),
      });
    }
    return Array.from(items.values());
  };

  // Generic detail scrape: main text + photos + a few regex facts.
  S.genericDetail = function (extra = {}) {
    const main = document.querySelector("main") || document.body;
    const text = S.text(main).slice(0, 120000);
    const h1 = document.querySelector("h1");
    return Object.assign(
      {
        title: h1 ? S.text(h1) : document.title,
        text,
        status_text: text.slice(0, 3000),
        photos: S.photos(),
        page_url: location.href,
        scraped_at: new Date().toISOString(),
      },
      extra
    );
  };

  if (typeof chrome === "undefined" || !chrome.runtime || !chrome.runtime.onMessage) return; // injected for testing
  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    const A = window.__scoutAdapter;
    if (!A || !["ping", "collect", "detail"].includes(msg.type)) return false;
    (async () => {
      try {
        if (msg.type === "ping") sendResponse({ ok: true, site: A.site, saved: !!A.isSavedPage(), detail: !!A.isDetailPage() });
        else if (msg.type === "collect") sendResponse({ ok: true, items: await A.collectSaved() });
        else if (msg.type === "detail") sendResponse({ ok: true, detail: await A.scrapeDetail() });
      } catch (e) {
        sendResponse({ ok: false, error: String(e && e.stack || e) });
      }
    })();
    return true; // async
  });
})();
