// Cars & Bids: watch list (/watch-list/, live auctions only) + auction pages.
// Verified against the live DOM 2026-09-04.
(function () {
  const S = window.__scout;
  const AUCTION = /carsandbids\.com\/auctions\/([A-Za-z0-9]+)\/([a-z0-9-]+)/i;
  window.__scoutAdapter = {
    site: "carsandbids",
    isSavedPage: () => /\/watch-list/i.test(location.pathname),
    isDetailPage: () => AUCTION.test(location.href),
    async collectSaved() {
      await S.autoScroll(10, 1000);
      const items = [];
      const seen = new Set();
      for (const li of document.querySelectorAll("li.auction-item")) {
        const a = li.querySelector('a[href*="/auctions/"]');
        const m = a && a.href.match(AUCTION);
        if (!m || seen.has(m[1])) continue;
        seen.add(m[1]);
        const cardText = S.text(li).slice(0, 600);
        // Only your watched cars: the page can also show other auctions (2026-10-11 a sync
        // pulled in 71, incl. a Ford GT and an SLR, against 2 watched + 20 watched results).
        if (!/\bWatched\b/.test(cardText)) continue;
        const img = li.querySelector("img");
        // The first link is the photo, whose text is the "Time Left ... Bid $x" overlay (#513, 2026-10-18).
        const titleEl = li.querySelector(".auction-title a, .auction-title");
        const title = titleEl ? S.text(titleEl).replace(/\s*\bWatched\b\s*$/, "") : "";
        items.push({
          site: "carsandbids", site_id: m[1], url: `https://carsandbids.com/auctions/${m[1]}/${m[2]}`,
          title: title || cardText.split("\n")[0], price_text: S.priceIn(cardText), card_text: cardText,
          thumb: img ? img.currentSrc || img.src : null,
          sold: /\bsold for\b/i.test(cardText), ended: /\b(bid to|ended)\b/i.test(cardText) && !/\bsold for\b/i.test(cardText),
        });
      }
      return items;
    },
    async scrapeDetail() {
      await S.sleep(1500);
      await S.expandAll(["Show more", "See more", "Read more"]);
      const d = S.genericDetail({ photos: S.photos(300, 40, (src) => /carsandbids/.test(src)) });
      const t = d.text;
      const bid = t.match(/(High\s*Bid|Current\s*Bid|Sold\s*for|Bid\s*to|Winning\s*Bid|Reserve\s*Not\s*Met)\s*:?\s*(\$[\d,]+)?/i);
      if (bid) { d.bid_label = bid[1].replace(/\s+/g, " "); d.bid_text = bid[2] || ""; }
      const tl = t.match(/Time Left\s*:?\s*([^\n]+)/i);
      if (tl) d.time_left = tl[1].trim();
      const bids = t.match(/\bBids\s*:?\s*(\d+)/i);
      if (bids) d.bid_count = parseInt(bids[1], 10);
      const end = t.match(/Ending\s+([^\n]+)/i);
      if (end) d.auction_end_text = end[1].trim();
      d.status_text = (bid ? d.bid_label + " " + d.bid_text + "\n" : "") + d.status_text;
      return d;
    },
  };
})();
