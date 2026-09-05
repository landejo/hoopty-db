// Facebook Marketplace: saved items page + listing detail pages.
(function () {
  const S = window.__scout;
  const ITEM = /facebook\.com\/marketplace\/item\/(\d+)/;
  window.__scoutAdapter = {
    site: "facebook",
    isSavedPage: () => /\/marketplace\/you\/saved|\/saved\/?(\?|$)|\/marketplace\/saved/i.test(location.href),
    isDetailPage: () => ITEM.test(location.href),
    async collectSaved() {
      await S.autoScroll(30, 1500);
      const items = S.collectByPattern("facebook", ITEM, (h) => (h.match(ITEM) || [])[1]);
      for (const it of items) { it.url = `https://www.facebook.com/marketplace/item/${it.site_id}/`; it.pending = /^\s*pending\b/im.test(it.card_text) || /\bpending\b/i.test(it.card_text.split("\n")[0] || ""); }
      return items;
    },
    async scrapeDetail() {
      await S.sleep(800);
      await S.expandAll();
      const d = S.genericDetail({
        photos: S.photos(300, 40, (src) => /fbcdn|scontent/.test(src)),
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
