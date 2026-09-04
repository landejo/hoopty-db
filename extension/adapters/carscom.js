// Cars.com: saved cars page + vehicle detail pages.
(function () {
  const S = window.__scout;
  const VDP = /cars\.com\/vehicledetail\/([a-z0-9-]+)/i;
  window.__scoutAdapter = {
    site: "carscom",
    isSavedPage: () => /\/saved/i.test(location.pathname),
    isDetailPage: () => VDP.test(location.href),
    async collectSaved() {
      await S.autoScroll(15, 1200);
      const items = S.collectByPattern("carscom", VDP, (h) => (h.match(VDP) || [])[1]);
      for (const it of items) it.url = `https://www.cars.com/vehicledetail/${it.site_id}/`;
      return items;
    },
    async scrapeDetail() {
      await S.sleep(800);
      await S.expandAll(["Show full description", "See more", "Show more", "Read more"]);
      const d = S.genericDetail({ photos: S.photos(300, 40, (src) => /cars\.com|platform\.cstatic/.test(src)) });
      if (/this vehicle (is )?(no longer|sold)/i.test(d.text.slice(0, 2500))) d.status_text = "Sold\n" + d.status_text;
      return d;
    },
  };
})();
