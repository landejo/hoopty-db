// CarGurus: saved-cars page (/Cars/myAccount/saved-listings, with a "Sold cars"
// tab at /saved-listings/sold) + listing detail pages (/details/<id>).
// Verified against the live DOM 2026-09-04.
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
      const lines = cardText.split("\n").map((s) => s.trim()).filter(Boolean).filter((l) => !/remove saved listing|^sold$/i.test(l));
      const img = tile.querySelector("img");
      const priceEl = tile.querySelector("[data-testid=srp-tile-price]");
      out.push({
        site: "cargurus", site_id: id, url: `https://www.cargurus.com/details/${id}`,
        title: lines.slice(0, 2).join(" "), price_text: priceEl ? S.text(priceEl) : S.priceIn(cardText),
        card_text: cardText, thumb: img ? img.currentSrc || img.src : null,
        sold: markSold || /^\s*Sold\b/i.test(cardText), ended: false,
      });
    }
    return out;
  }

  window.__scoutAdapter = {
    site: "cargurus",
    isSavedPage: () => /myAccount\/saved-listings|mySaved\.action/i.test(location.href),
    isDetailPage: () => !!idOf(location.href),
    async collectSaved() {
      const onSold = /\/sold\b/.test(location.pathname);
      const tabBtn = (label) => Array.from(document.querySelectorAll("button")).find((b) => S.text(b).toLowerCase() === label);
      let items = [];
      // Saved tab first, then the Sold tab (those become market comps).
      if (onSold && tabBtn("saved cars")) { tabBtn("saved cars").click(); await S.sleep(2500); }
      await S.autoScroll(15, 1200);
      items = items.concat(collectTiles(false));
      const sold = tabBtn("sold cars");
      if (sold) {
        sold.click(); await S.sleep(3000); await S.autoScroll(15, 1200);
        items = items.concat(collectTiles(true));
        const back = tabBtn("saved cars"); if (back) back.click();
      }
      const seen = new Set();
      return items.filter((i) => !seen.has(i.site_id) && seen.add(i.site_id));
    },
    async scrapeDetail() {
      await S.sleep(800);
      await S.expandAll(["Show full description", "See more", "Show more", "Read more"]);
      const d = S.genericDetail({ photos: S.photos(300, 40, (src) => /static\.cargurus\.com\/images\/forsale/.test(src)) });
      if (/no longer available|this listing has been sold|\bsold\b/i.test(d.text.slice(0, 1500))) d.status_text = "Sold\n" + d.status_text;
      return d;
    },
  };
})();
