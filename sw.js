const CACHE = "alpha-engine-v9-finance-cockpit-r6-quant-governance";
const APP_SHELL = [
  "./",
  "./index.html",
  "./finance-cockpit.css",
  "./freeze-ui.css",
  "./quant-governance.css",
  "./finance-cockpit.js",
  "./quant-governance.js",
  "./manifest.webmanifest",
  "./icon-180.png",
  "./alpha-home-180.png",
  "./alpha-home-192.png",
  "./alpha-home-512.png"
];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(APP_SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))));
  self.clients.claim();
});

self.addEventListener("fetch", e => {
  if (e.request.method !== "GET") return;
  const url = new URL(e.request.url);
  const isLiveData = /\/data\/(latest|automation-health|backtest-v8|market-series|model-health|quant-governance)\.json$/.test(url.pathname);
  const isNavigation = e.request.mode === "navigate";
  if (isLiveData || isNavigation) {
    e.respondWith(fetch(e.request,{cache:"no-store"}).then(r=>{if(r.ok)caches.open(CACHE).then(c=>c.put(e.request,r.clone()));return r}).catch(()=>caches.match(e.request).then(r=>r||caches.match("./index.html"))));
    return;
  }
  e.respondWith(fetch(e.request,{cache:"no-cache"}).then(r=>{if(r.ok)caches.open(CACHE).then(c=>c.put(e.request,r.clone()));return r}).catch(()=>caches.match(e.request).then(r=>r||caches.match("./index.html"))));
});
