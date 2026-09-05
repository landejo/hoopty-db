// Cars.com: saved cars (/profile/saved-cars/) + vehicle detail pages.
// The saved page also lists recommended cars; only cards carrying a
// "Saved on:" line are the user's. Verified against the live DOM 2026-09-04.
(function () {
  const S = window.__scout;
  const VDP = /cars\.com\/vehicledetail\/([a-z0-9-]+)/i;
  window.__scoutAdapter = {
    site: "carscom",
    isSavedPage: () => /\/profile\/saved-cars/i.test(location.pathname),
    isDetailPage: () => VDP.test(location.href),
    async collectSaved() {
      await S.autoScroll(15, 1200);
      const items = S.collectByPattern("carscom", VDP, (h) => (h.match(VDP) || [])[1]).filter((it) => /saved on:/i.test(it.card_text));
      for (const it of items) {
        it.url = `https://www.cars.com/vehicledetail/${it.site_id}/`;
        const lines = it.card_text.split("\n").map((s) => s.trim()).filter(Boolean);
        it.title = lines.find((l) => /\b(19|20)\d{2}\b/.test(l) && !/saved on|\$/i.test(l)) || it.title;
        it.sold = /\bsold\b|no longer available/i.test(it.card_text);
        it.pending = !it.sold && /sale pending|\bpending\b/i.test(it.card_text);
        it.price_drop_text = (it.card_text.match(/\$[\d,]+\s*price drop|price drop[^\n]*/i) || [""])[0];
      }
      return items;
    },
    async scrapeDetail() {
      await S.sleep(800);
      await S.expandAll(["Show full description", "See more", "Show more", "Read more", "View all features"]);
      const d = S.genericDetail({ photos: S.photos(300, 40, (src) => /cars\.com|cstatic/.test(src)) });
      if (/this vehicle (is )?(no longer|sold)|listing (is )?no longer available/i.test(d.text.slice(0, 2500))) d.status_text = "Sold\n" + d.status_text;
      return d;
    },
  };
})();
