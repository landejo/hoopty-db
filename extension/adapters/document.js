// Capture a history report or record page (Carfax, AutoCheck, a shop's portal)
// as a document to attach to a tracked listing. Text only — no screenshots.
(function () {
  const S = window.__scout;
  function guessKind() {
    const h = location.hostname, t = (document.title + " " + document.body.innerText.slice(0, 400)).toLowerCase();
    if (/carfax/.test(h) || /carfax/.test(t)) return "carfax";
    if (/autocheck/.test(h) || /autocheck/.test(t)) return "autocheck";
    if (/invoice|repair order/.test(t)) return "invoice";
    if (/inspection/.test(t)) return "inspection";
    return "service_records";
  }
  function vinOnPage() {
    const m = (document.body.innerText || "").match(/\b([A-HJ-NPR-Z0-9]{17})\b/i);
    return m ? m[1].toUpperCase() : null;
  }
  chrome.runtime.onMessage.addListener((msg, _s, sendResponse) => {
    if (msg.type !== "capture_document") return false;
    (async () => {
      try {
        await S.waitForChallenge(15000);
        await S.expandAll(["Show more", "See more", "View all", "Expand all", "Show all records"]);
        const main = document.querySelector("main") || document.body;
        sendResponse({ ok: true, kind: guessKind(), vin: vinOnPage(),
                       title: document.title.slice(0, 200), url: location.href,
                       text: S.text(main).slice(0, 200000) });
      } catch (e) { sendResponse({ ok: false, error: String(e) }); }
    })();
    return true;
  });
})();
