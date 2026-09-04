// CarGurus: saved-cars page + listing detail pages.
(function () {
  const S = window.__scout;
  const LISTING = /cargurus\.com\/Cars\/(?:inventorylisting\/[^#]*#listing=(\d+)|l-[^/]*-(\d+)|.*?listing=(\d+))/i;
  const idOf = (h) => { const m = h.match(LISTING); return m ? (m[1] || m[2] || m[3]) : null; };
  window.__scoutAdapter = {
    site: "cargurus",
    isSavedPage: () => /saved/i.test(location.pathname + location.search),
    isDetailPage: () => !!idOf(location.href),
    async collectSaved() {
      await S.autoScroll(15, 1200);
      return S.collectByPattern("cargurus", LISTING, idOf);
    },
    async scrapeDetail() {
      await S.sleep(800);
      await S.expandAll(["See more", "Show more", "Read more", "View full description", "Show full description"]);
      const d = S.genericDetail({ photos: S.photos(300, 40, (src) => /cargurus|static\.cargurus/.test(src)) });
      const gone = /no longer available|sold/i.test(d.text.slice(0, 2500));
      if (gone) d.status_text = "No longer available\n" + d.status_text;
      return d;
    },
  };
})();
