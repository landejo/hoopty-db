// Bridge between the local workbench page and this extension. The page cannot
// call chrome.runtime itself, so it posts a window message; we forward it to
// the service worker and post the answer back. Only runs on the local server.
(function () {
  document.documentElement.dataset.scoutExtension = chrome.runtime.getManifest().version;
  const ALLOWED = new Set(["check_availability", "investigate"]);
  window.addEventListener("message", (e) => {
    if (e.source !== window || !e.data || e.data.source !== "hoopty-page" || !ALLOWED.has(e.data.type)) return;
    chrome.runtime.sendMessage({ type: e.data.type }, (resp) => {
      window.postMessage({ source: "hoopty-extension", type: e.data.type + ":done",
                           resp: chrome.runtime.lastError ? { ok: false, error: chrome.runtime.lastError.message } : resp }, window.location.origin);
    });
  });
})();
