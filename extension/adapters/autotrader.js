// Autotrader: saved listings (/account/cars) + vehicle pages. Saved cards
// link via /cars-for-sale/vehicledetails.xhtml?listingId=<id> while the
// "Cars You May Like" block (ignored, at or below its heading) uses
// /cars-for-sale/vehicle/<id>; both resolve to the /vehicle/<id> page.
// Private Seller Exchange cars use /marketplace/buy/<VIN>.
// Verified against the live DOM 2026-09-11.
(function () {
  const S = window.__scout;
  const VEHICLE = /autotrader\.com\/cars-for-sale\/vehicle\/(\d+)/i;
  const XHTML = /autotrader\.com\/cars-for-sale\/vehicledetails\.xhtml\?[^#]*\blistingId=(\d+)/i;
  const MARKET = /autotrader\.com\/marketplace\/buy\/([A-HJ-NPR-Z0-9]{17})/i;
  const idFrom = (h) => (h.match(VEHICLE) || h.match(XHTML) || h.match(MARKET) || [])[1];
  const urlFor = (id) => /^\d+$/.test(id)
    ? `https://www.autotrader.com/cars-for-sale/vehicle/${id}`
    : `https://www.autotrader.com/marketplace/buy/${id}`;
  const AT_IMG = /autotrader|images\.autotrader|kbb|coxautoinc/;
  function galleryPhotos(cap = 40) {
    const big = (u) => u.replace(/([?&])width=\d+/, "$1width=1024").replace(/([?&])height=\d+/, "$1height=768");
    const slider = document.querySelector('[data-cmp="mediaSlider"]');
    const fromSlider = slider ? [...slider.querySelectorAll("img")].map((i) => i.currentSrc || i.src || i.getAttribute("data-src") || "") : [];
    const pick = fromSlider.length ? fromSlider
      : [...document.images].filter((i) => !i.closest('[data-cmp="inventoryImage"], [data-cmp="itemCard"]')).map((i) => i.currentSrc || i.src || "");
    const seen = new Set(), out = [];
    for (const u of pick) {
      if (!/^https?:/.test(u) || !AT_IMG.test(u)) continue;
      const k = u.split("?")[0];
      if (seen.has(k)) continue;
      seen.add(k); out.push(big(u));
      if (out.length >= cap) break;
    }
    return out;
  }
  // A free history report the dealer paid for: "View the Free Vehicle History Report"
  // links to /cars-for-sale/vehicle/<id>/experian?... (AutoCheck). A buy-only listing
  // links autocheck.com instead, which this skips. Verified 2026-10-23 (#648 free, #645 buy-only).
  function freeHistoryReport() {
    const a = document.querySelector('a[href*="/experian?"]');
    return a ? { kind: "autocheck", url: a.href } : null;
  }

  window.__scoutAdapter = {
    site: "autotrader",
    isSavedPage: () => /\/account\/cars/i.test(location.pathname),
    isDetailPage: () => VEHICLE.test(location.href) || XHTML.test(location.href) || MARKET.test(location.href),
    async collectSaved() {
      await S.sleep(2500); // the list renders client-side after load
      await S.autoScroll(10, 1000);
      const rec = Array.from(document.querySelectorAll("h1,h2,h3,h4")).find((h) => /cars you may like|similar|recommended/i.test(h.textContent));
      const pattern = { test: (h) => !!idFrom(h) };
      const items = S.collectByPattern("autotrader", pattern, idFrom);
      const out = [];
      for (const it of items) {
        const a = document.querySelector(`a[href*="${it.site_id}"]`);
        if (rec && a && (rec.compareDocumentPosition(a) & Node.DOCUMENT_POSITION_FOLLOWING)) continue;
        it.url = urlFor(it.site_id);
        it.sold = /\bsold\b|no longer available/i.test(it.card_text);
        out.push(it);
      }
      return out;
    },
    // Only this car's gallery (data-cmp="mediaSlider"), at 1024x768. The page also renders
    // the dealer's other cars (data-cmp="inventoryImage" in "itemCard"s, 408x306): 20 of
    // #487's 22 photos were those, 2026-10-18.
    async scrapeDetail() {
      await S.sleep(1200);
      await S.expandAll(["See more", "Show more", "Read more", "View full description", "Show full description", "See all features"]);
      const d = S.genericDetail({ photos: galleryPhotos() });
      d.history_report = freeHistoryReport();
      if (/no longer available|this vehicle (is|has been) sold|listing (is )?unavailable/i.test(d.text.slice(0, 2500))) d.status_text = "No longer available\n" + d.status_text;
      return d;
    },
  };
})();
