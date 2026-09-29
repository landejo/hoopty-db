// Facebook Marketplace: saved items page + listing detail pages, and car posts
// in groups (e.g. .../groups/<id>/permalink/<post>/), read post-only.
(function () {
  const S = window.__scout;
  const ITEM = /facebook\.com\/marketplace\/item\/(\d+)/;
  const GROUP_POST = /facebook\.com\/groups\/[^/]+\/(?:permalink|posts)\/\d+/;
  // Image types that are never the car: profile pictures, reel/video thumbnails,
  // Instagram-sourced feed images, ad creatives (read off real pages 2026-09-28).
  const JUNK = /\/v\/t(?:39\.30808-1|15\.5256-10|51\.82787-15|45\.1600-4)\//;
  const isCarPhoto = (src) => /fbcdn|scontent/.test(src) && !JUNK.test(src);
  const sleep = S.sleep;

  // The post's own photos: the grid inside the post, then Facebook's photo viewer
  // for the rest of the set ("+9" on the last tile). Never the feed, stories or ads.
  async function groupPostPhotos(scope) {
    const key = (u) => (u.match(/\/(\d+_\d+_\d+)_n\./) || [u.split("?")[0]])[1];
    const out = new Map();
    for (const im of scope.querySelectorAll('a[href*="/photo"] img')) {
      const src = im.currentSrc || im.src;
      if (src && isCarPhoto(src)) out.set(key(src), src);
    }
    const links = [...scope.querySelectorAll('a[href*="/photo"]')];
    if (!links.length || !/^\+\d+$/m.test(scope.innerText || "")) return [...out.values()];
    let steps = 0;   // each photo in the viewer is a history entry; go back that many at the end
    const fbid = () => new URL(location.href).searchParams.get("fbid");
    const mainImg = () => {
      const c = [...document.images].filter((im) => /\/t39\.30808-6\//.test(im.currentSrc || im.src) && (im.naturalWidth || 0) >= 700);
      c.sort((a, b) => b.naturalWidth - a.naturalWidth);
      return c[0] ? c[0].currentSrc || c[0].src : null;
    };
    const waitNewId = async (prev, ms) => { for (let t = 0; t < ms && fbid() === prev; t += 250) await sleep(250); return fbid() !== prev; };
    try {
      links[0].click();
      if (await waitNewId(null, 8000)) steps++;
      const seen = new Set();
      for (let i = 0; i < 40; i++) {
        const id = fbid();
        if (!id || seen.has(id)) break;   // back at the first photo: the set is complete
        seen.add(id);
        let src = null;
        for (let k = 0; k < 15 && !src; k++) { await sleep(300); src = mainImg(); }
        if (src && isCarPhoto(src)) out.set(key(src), src);   // the viewer's larger copy replaces the grid's
        let moved = false;
        for (let tries = 0; tries < 3 && !moved; tries++) {   // Facebook is sometimes slow to advance: retry
          if (tries < 2) {
            // Two elements carry the label (one under the viewer): click the visible one.
            const next = [...document.querySelectorAll('[aria-label="Next photo"]')].filter((e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; }).pop();
            if (next) next.click();
          } else {
            document.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", code: "ArrowRight", keyCode: 39, bubbles: true }));
          }
          moved = await waitNewId(id, 5000);
        }
        if (!moved) break;
        steps++;
      }
    } finally {
      if (steps) history.go(-steps);
    }
    return [...out.values()];
  }

  window.__scoutAdapter = {
    site: "facebook",
    isSavedPage: () => /\/marketplace\/you\/saved|\/saved\/?(\?|$)|\/marketplace\/saved/i.test(location.href),
    isDetailPage: () => ITEM.test(location.href) || GROUP_POST.test(location.href),
    async collectSaved() {
      await S.autoScroll(30, 1500);
      const items = S.collectByPattern("facebook", ITEM, (h) => (h.match(ITEM) || [])[1]);
      for (const it of items) { it.url = `https://www.facebook.com/marketplace/item/${it.site_id}/`; it.pending = /^\s*pending\b/im.test(it.card_text) || /\bpending\b/i.test(it.card_text.split("\n")[0] || ""); }
      return items;
    },
    async scrapeDetail() {
      await sleep(800);
      if (GROUP_POST.test(location.href)) {
        // A group post opens as a dialog over the group feed; read only the post.
        let scope = null;
        for (let k = 0; k < 20 && !scope; k++) { scope = document.querySelector('[role="dialog"]'); if (!scope) await sleep(300); }
        scope = scope || document.querySelector('[role="main"]') || document.body;
        const text = S.text(scope).slice(0, 120000);
        const photos = await groupPostPhotos(scope);
        return { title: document.title.replace(/\s*\|\s*Facebook\s*$/, ""), text, status_text: text.slice(0, 3000), photos,
                 page_url: location.href, scraped_at: new Date().toISOString(), group_post: true };
      }
      await S.expandAll();
      const d = S.genericDetail({
        photos: S.photos(300, 40, isCarPhoto),
      });
      const m = d.text.match(/Listed\s+([^\n]+?)(?:\s+in\s+([^\n]+))?\n/i);
      if (m) { d.listed_text = m[1]; if (m[2]) d.location_text = m[2]; }
      const mi = d.text.match(/Driven\s+([\d,]+)\s+miles/i);
      if (mi) d.mileage_text = mi[1];
      const sold = /^\s*Sold\b/m.test(d.text.slice(0, 1500)) || /This listing is sold/i.test(d.text);
      const pending = !sold && /^\s*Pending\b/m.test(d.text.slice(0, 1500));
      d.status_text = (sold ? "Sold\n" : pending ? "Pending\n" : "") + d.status_text;
      return d;
    },
  };
})();
