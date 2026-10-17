// CarGurus: saved-cars page (/Cars/myAccount/saved-listings, with a "Sold" tab at
// /saved-listings/sold) + listing detail pages (/details/<id>). Verified 2026-09-04.
// 2026-10-17: saved cars moved to a /hub/saved overview (a carousel preview); its
// "View all" opens the full list in place, and the tabs now read "Saved" / "Sold".
(function () {
  const S = window.__scout;
  const LISTING = /cargurus\.com\/details\/(\d+)/i;
  const idOf = (h) => (h.match(LISTING) || [])[1] || null;

  function collectTiles(markSold) {
    const out = [];
    for (const tile of document.querySelectorAll("[data-testid=srp-listing-tile]")) {
      const a = tile.querySelector('a[href*="/details/"]');
      const id = a && idOf(a.href);
      if (!id) continue;
      const cardText = S.text(tile).slice(0, 600);
      const NOISE = /remove saved listing|^sold$|price drop|great deal|good deal|fair deal|high price|overpriced|no rating|check availability|includes dealer fees|\/mo est|mi away|^\$/i;
      const raw = cardText.split("\n").map((s) => s.trim()).filter(Boolean);
      const yi = raw.findIndex((l) => /\b(19|20)\d{2}\b/.test(l) && !NOISE.test(l));
      const lines = yi >= 0 ? [raw[yi]].concat(raw[yi + 1] && !NOISE.test(raw[yi + 1]) && !/\bmi\b/.test(raw[yi + 1]) ? [raw[yi + 1]] : []) : raw.filter((l) => !NOISE.test(l));
      const img = tile.querySelector("img");
      const priceEl = tile.querySelector("[data-testid=srp-tile-price]");
      out.push({
        site: "cargurus", site_id: id, url: `https://www.cargurus.com/details/${id}`,
        title: lines.slice(0, 2).join(" "), price_text: priceEl ? S.text(priceEl) : S.priceIn(cardText),
        card_text: cardText, thumb: img ? img.currentSrc || img.src : null,
        sold: markSold || /^\s*Sold\b/i.test(cardText), ended: false,
        price_drop_text: (cardText.match(/price drop[^\n]*/i) || [""])[0],
      });
    }
    return out;
  }

  window.__scoutAdapter = {
    site: "cargurus",
    isSavedPage: () => /myAccount\/saved-listings|mySaved\.action|\/hub\/saved\b/i.test(location.href),
    isDetailPage: () => !!idOf(location.href),
    async collectSaved() {
      // The hub only previews a few saved cars: open the full list first.
      const viewAll = document.querySelector('a[data-testid=view-all-link][href*="saved-listings"]');
      if (/\/hub\/saved\b/.test(location.pathname) && viewAll) { viewAll.click(); await S.sleep(4000); }
      const onSold = /\/sold\b/.test(location.pathname);
      // Tab labels were "Saved cars" / "Sold cars"; now "Saved" / "Sold".
      const tabBtn = (label) => Array.from(document.querySelectorAll("button, [role=tab]")).find((b) => [label, label + " cars"].includes(S.text(b).trim().toLowerCase()));
      let items = [];
      // Saved tab first, then the Sold tab (those become market comps).
      if (onSold && tabBtn("saved")) { tabBtn("saved").click(); await S.sleep(2500); }
      await S.autoScroll(15, 1200);
      items = items.concat(collectTiles(false));
      const sold = tabBtn("sold");
      if (sold) {
        sold.click(); await S.sleep(3000); await S.autoScroll(15, 1200);
        items = items.concat(collectTiles(true));
        const back = tabBtn("saved"); if (back) back.click();
      }
      const seen = new Set();
      return items.filter((i) => !seen.has(i.site_id) && seen.add(i.site_id));
    },
    async scrapeDetail() {
      await S.sleep(800);
      await S.expandAll(["Show full description", "See more", "Show more", "Read more"]);
      const notVehicle = (u) => /logo|badge|sprite|icon|placeholder|avatar|dealer_logo/i.test(u);
      const d = S.genericDetail({
        photos: S.allPhotos({
          minSize: 300, cap: 40,
          filter: (src) => /cargurus\.com\/images\/forsale/.test(src) && !notVehicle(src),
          sourcePattern: /static\.cargurus\.com\/images\/forsale\//,
          reject: notVehicle,
        }),
      });
      // Not a bare "sold": CarGurus pages carry "similar cars sold" and dealer copy.
      if (/no longer available|this listing has been sold|looks like that one got away|^\s*sold\s*$/im.test(d.text.slice(0, 1500))) d.status_text = "Sold\n" + d.status_text;
      return d;
    },
  };
})();
