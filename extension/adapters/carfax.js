// CARFAX Cars for Sale: followed cars (/account/followed-cars/) + listing pages
// (/vehicle/<VIN>). Verified against the signed-in page 2026-10-18. History
// reports on carfax.com are captured separately, as documents (document.js).
(function () {
  const S = window.__scout;
  const LISTING = /carfax\.com\/vehicle\/([A-HJ-NPR-Z0-9]{17})/i;
  const vinOf = (h) => ((h || "").match(LISTING) || [])[1] || null;
  // Photos are carfax-img.vast.com/carfax/v2/<listing id>/<n>/<WxH>; the page renders
  // one 640x480 slide plus 120x90 strip thumbnails, so rebuild every <n> at 1024x768
  // (the largest size the CDN serves; 1280x960 fails). The listing id seen most is
  // this car's, not a "similar cars" tile.
  function listingPhotos(cap = 40) {
    const re = /carfax-img\.vast\.com\/carfax\/v2\/(\d+)\/(\d+)\//g;
    const byId = new Map();
    const srcs = [...document.images].map((i) => i.currentSrc || i.src).join(" ") + " " + document.documentElement.innerHTML;
    let m;
    while ((m = re.exec(srcs)) !== null) {
      if (!byId.has(m[1])) byId.set(m[1], new Set());
      byId.get(m[1]).add(Number(m[2]));
    }
    const best = [...byId.entries()].sort((a, b) => b[1].size - a[1].size)[0];
    if (!best) return [];
    return [...best[1]].sort((a, b) => a - b).slice(0, cap)
      .map((n) => `https://carfax-img.vast.com/carfax/v2/${best[0]}/${n}/1024x768`);
  }
  const GONE_RE = /no longer available|this vehicle has (been )?sold|vehicle is no longer listed|listing (has )?expired/i;

  window.__scoutAdapter = {
    site: "carfax",
    isSavedPage: () => /\/account\/followed-cars/i.test(location.pathname),
    isDetailPage: () => !!vinOf(location.href),
    async collectSaved() {
      await S.autoScroll(10, 1000);
      const items = [];
      const seen = new Set();
      for (const li of document.querySelectorAll('li[class*="followedCar"]')) {
        const a = li.querySelector('a[href*="/vehicle/"]');
        const vin = a && vinOf(a.href);
        if (!vin || seen.has(vin)) continue;
        seen.add(vin);
        const cardText = S.text(li).slice(0, 600);
        // "Used 2017 Mazda Miata·38972 miles"
        const ymm = S.text(li.querySelector('[class*="conditionYearMakeModel"]')) || (cardText.match(/(?:Used|New|Certified)[^\n]*/) || [""])[0];
        const priceEl = li.querySelector('[class*="vehiclePrice"]');
        const img = li.querySelector("img");
        items.push({
          site: "carfax", site_id: vin, url: `https://www.carfax.com/vehicle/${vin}`,
          title: ymm.split("·")[0].replace(/^(Used|New|Certified Pre-Owned|Certified)\s+/i, "").trim(),
          price_text: priceEl ? S.priceIn(S.text(priceEl)) : S.priceIn(cardText), card_text: cardText,
          thumb: img ? img.currentSrc || img.src : null,
          sold: GONE_RE.test(cardText) || /^\s*sold\b/im.test(cardText), ended: false,
        });
      }
      return items;
    },
    async scrapeDetail() {
      await S.sleep(1000);
      await S.expandAll(["Show more", "See more", "Read more", "View all features", "Show all features"]);
      const d = S.genericDetail({ photos: listingPhotos() });
      const mi = d.text.match(/([\d,]+)\s*mi\b/);
      if (mi) d.mileage_text = mi[1];
      if (GONE_RE.test(d.text.slice(0, 2000))) d.status_text = "Sold\n" + d.status_text;
      return d;
    },
  };
})();
