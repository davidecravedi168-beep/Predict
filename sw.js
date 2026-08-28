const CACHE = "alpha-engine-v8-5-edge-core-r2";
const APP_SHELL = [
  "./",
  "./index.html",
  "./manifest.webmanifest",
  "./icon-180.png",
  "./icon-192.png",
  "./icon-512.png"
];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(APP_SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", e => {
  e.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener("fetch", e => {
  if (e.request.method !== "GET") return;
  const url = new URL(e.request.url);
  const isLiveData = /\/data\/(latest|automation-health|backtest-v8)\.json$/.test(url.pathname);
  const isNavigation = e.request.mode === "navigate";

  // Financial decisions and the app shell are always network-first.
  // no-store prevents an intermediary/browser HTTP cache from hiding a fresh Pages deploy.
  if (isLiveData || isNavigation) {
    e.respondWith(
      fetch(e.request, { cache: "no-store" }).then(r => {
        if (r.ok) {
          const copy = r.clone();
          caches.open(CACHE).then(c => c.put(e.request, copy));
        }
        return r;
      }).catch(() => caches.match(e.request).then(r => r || caches.match("./index.html")))
    );
    return;
  }

  e.respondWith(
    fetch(e.request, { cache: "no-cache" }).then(r => {
      if (r.ok) {
        const copy = r.clone();
        caches.open(CACHE).then(c => c.put(e.request, copy));
      }
      return r;
    }).catch(() => caches.match(e.request).then(r => r || caches.match("./index.html")))
  );
});
