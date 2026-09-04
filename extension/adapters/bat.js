// Bring a Trailer: watchlist (/watchlist/) + listing pages.
// The watchlist page shows live watched auctions, plus a "Recently Viewed"
// carousel; ended items from that carousel are collected as comps only.
// Verified against the live DOM 2026-09-04.
(function () {
  const S = window.__scout;
  const LISTING = /bringatrailer\.com\/listing\/([a-z0-9-]+)\/?/i;
  window.__scoutAdapter = {
    site: "bat",
    isSavedPage: () => /\/watchlist/i.test(location.pathname),
    isDetailPage: () => LISTING.test(location.href),
    async collectSaved() {
      await S.autoScroll(10, 1000);
      const recent = document.querySelector("section.items-recent");
      const items = S.collectByPattern("bat", LISTING, (h) => (h.match(LISTING) || [])[1]);
      const out = [];
      for (const it of items) {
        it.url = `https://bringatrailer.com/listing/${it.site_id}/`;
        it.sold = /\bsold for\b/i.test(it.card_text);
        it.ended = !it.sold && /\b(bid to|ended)\b/i.test(it.card_text);
        const a = document.querySelector(`a[href*="/listing/${it.site_id}"]`);
        const inRecent = !!(recent && a && recent.contains(a));
        if (inRecent) { if (!(it.sold || it.ended)) continue; it.section = "recently_viewed"; }
        out.push(it);
      }
      return out;
    },
    async scrapeDetail() {
      await S.sleep(1500);
      await S.expandAll(["Show more", "See more", "Read more"]);
      const d = S.genericDetail({ photos: S.photos(300, 40, (src) => /bringatrailer/.test(src)) });
      const t = d.text;
      const bid = t.match(/(Current Bid|Sold for|Bid to|Winning Bid)\s*:?\s*(?:USD\s*)?(\$[\d,]+)/i);
      if (bid) { d.bid_label = bid[1]; d.bid_text = bid[2]; }
      const tl = t.match(/Ends in\s*([^\n]+)/i);
      if (tl) d.time_left = tl[1].trim();
      const bids = t.match(/(\d+)\s*Bids?\b/i);
      if (bids) d.bid_count = parseInt(bids[1], 10);
      const ended = t.match(/(?:sold|bid to [^\n]*?)\s+on\s+(\d{1,2}\/\d{1,2}\/\d{2,4})/i);
      if (ended) d.auction_end_text = ended[1];
      const ess = t.match(/Listing Details[\s\S]{0,2500}?(?=Private Party or Dealer|Lot #)/i);
      if (ess) d.essentials = ess[0];
      d.status_text = (bid ? bid[1] + " " + bid[2] + "\n" : "") + d.status_text;
      return d;
    },
  };
})();
