// Cars & Bids: watch list + auction pages.
(function () {
  const S = window.__scout;
  const AUCTION = /carsandbids\.com\/auctions\/([A-Za-z0-9]+)\/([a-z0-9-]+)/i;
  window.__scoutAdapter = {
    site: "carsandbids",
    isSavedPage: () => /watch|saved/i.test(location.pathname),
    isDetailPage: () => AUCTION.test(location.href),
    async collectSaved() {
      await S.autoScroll(10, 1000);
      const items = S.collectByPattern("carsandbids", AUCTION, (h) => (h.match(AUCTION) || [])[1]);
      for (const it of items) {
        const m = it.url.match(AUCTION);
        if (m) it.url = `https://carsandbids.com/auctions/${m[1]}/${m[2]}`;
        it.ended = /\b(sold for|bid to|ended)\b/i.test(it.card_text) && !/\bsold for\b/i.test(it.card_text);
        it.sold = /\bsold for\b/i.test(it.card_text);
      }
      return items;
    },
    async scrapeDetail() {
      await S.sleep(1200);
      await S.expandAll(["Show more", "See more", "Read more"]);
      const d = S.genericDetail({ photos: S.photos(300, 40, (src) => /carsandbids/.test(src)) });
      const t = d.text;
      const bid = t.match(/(Current Bid|Sold for|Bid to)\s*:?\s*(\$[\d,]+)/i);
      if (bid) { d.bid_label = bid[1]; d.bid_text = bid[2]; }
      const tl = t.match(/Time Left\s*:?\s*([^\n]+)/i);
      if (tl) d.time_left = tl[1].trim();
      const bids = t.match(/Bids?\s*:?\s*(\d+)/i);
      if (bids) d.bid_count = parseInt(bids[1], 10);
      const end = t.match(/Ending\s*:?\s*([^\n]+)/i);
      if (end) d.auction_end_text = end[1].trim();
      d.status_text = (bid ? bid[1] + " " + bid[2] + "\n" : "") + d.status_text;
      return d;
    },
  };
})();
