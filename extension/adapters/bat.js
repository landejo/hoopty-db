// Bring a Trailer: account watch list + listing pages.
(function () {
  const S = window.__scout;
  const LISTING = /bringatrailer\.com\/listing\/([a-z0-9-]+)\/?/i;
  window.__scoutAdapter = {
    site: "bat",
    isSavedPage: () => /watch|saved/i.test(location.pathname),
    isDetailPage: () => LISTING.test(location.href),
    async collectSaved() {
      await S.autoScroll(10, 1000);
      const items = S.collectByPattern("bat", LISTING, (h) => (h.match(LISTING) || [])[1]);
      for (const it of items) {
        it.url = `https://bringatrailer.com/listing/${it.site_id}/`;
        it.sold = /\bsold for\b/i.test(it.card_text);
        it.ended = !it.sold && /\b(bid to|ended)\b/i.test(it.card_text);
      }
      return items;
    },
    async scrapeDetail() {
      await S.sleep(1200);
      await S.expandAll(["Show more", "See more", "Read more"]);
      const d = S.genericDetail({ photos: S.photos(300, 40, (src) => /bringatrailer/.test(src)) });
      const t = d.text;
      const bid = t.match(/(Current Bid|Sold for|Bid to|Winning Bid)\s*:?\s*(\$[\d,]+)/i);
      if (bid) { d.bid_label = bid[1]; d.bid_text = bid[2]; }
      const tl = t.match(/(Time Left|Ends)\s*:?\s*([^\n]+)/i);
      if (tl) d.time_left = tl[2].trim();
      const bids = t.match(/(\d+)\s+bids?/i);
      if (bids) d.bid_count = parseInt(bids[1], 10);
      const ended = t.match(/(?:sold|ended)\s+on\s+([^\n]+)/i);
      if (ended) d.auction_end_text = ended[1].trim();
      // BaT essentials block (chassis, mileage, etc.) is a bullet list near the top.
      const ess = t.match(/Listing Details[\s\S]{0,2000}/i);
      if (ess) d.essentials = ess[0].slice(0, 2000);
      d.status_text = (bid ? bid[1] + " " + bid[2] + "\n" : "") + d.status_text;
      return d;
    },
  };
})();
