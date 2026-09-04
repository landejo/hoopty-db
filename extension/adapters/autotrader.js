// Autotrader: saved listings (/account/cars) + vehicle pages
// (/cars-for-sale/vehicle/<id>). The saved page also shows a "Cars You May
// Like" block; anything at or below that heading is ignored.
(function () {
  const S = window.__scout;
  const VEHICLE = /autotrader\.com\/cars-for-sale\/vehicle\/(\d+)/i;
  window.__scoutAdapter = {
    site: "autotrader",
    isSavedPage: () => /\/account\/cars/i.test(location.pathname),
    isDetailPage: () => VEHICLE.test(location.href),
    async collectSaved() {
      await S.sleep(2500); // the list renders client-side after load
      await S.autoScroll(10, 1000);
      const rec = Array.from(document.querySelectorAll("h1,h2,h3,h4")).find((h) => /cars you may like|similar|recommended/i.test(h.textContent));
      const items = S.collectByPattern("autotrader", VEHICLE, (h) => (h.match(VEHICLE) || [])[1]);
      const out = [];
      for (const it of items) {
        const a = document.querySelector(`a[href*="/cars-for-sale/vehicle/${it.site_id}"]`);
        if (rec && a && (rec.compareDocumentPosition(a) & Node.DOCUMENT_POSITION_FOLLOWING)) continue;
        it.url = `https://www.autotrader.com/cars-for-sale/vehicle/${it.site_id}`;
        it.sold = /\bsold\b|no longer available|unavailable/i.test(it.card_text);
        out.push(it);
      }
      return out;
    },
    async scrapeDetail() {
      await S.sleep(1200);
      await S.expandAll(["See more", "Show more", "Read more", "View full description", "Show full description", "See all features"]);
      const d = S.genericDetail({ photos: S.photos(300, 40, (src) => /autotrader|images\.autotrader|kbb|coxautoinc/.test(src)) });
      if (/no longer available|this vehicle (is|has been) sold|listing (is )?unavailable/i.test(d.text.slice(0, 2500))) d.status_text = "No longer available\n" + d.status_text;
      return d;
    },
  };
})();
