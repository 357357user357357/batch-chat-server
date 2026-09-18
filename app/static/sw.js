/* Batch Chat service worker — deliberately minimal.
 * NETWORK-FIRST for everything: the live UI always comes straight from the
 * server, so deploying a new app.js/style.css is picked up on the next reload
 * with zero staleness. The cache below exists ONLY as an offline fallback for
 * navigations (open the app with no connection -> cached shell). Bump the
 * cache name when the fallback shell file list changes. */
const SHELL_CACHE = "bc-shell-v1";
const SHELL_FILES = ["/", "/style.css?v=3", "/app.js?v=3", "/icon-192.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      .then((cache) => cache.addAll(SHELL_FILES))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => k !== SHELL_CACHE).map((k) => caches.delete(k)))
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;

  // Navigations: network first, cached shell when offline.
  if (event.request.mode === "navigate") {
    event.respondWith(
      fetch(event.request).catch(() =>
        caches.match("/").then((shell) => shell || Response.error())
      )
    );
    return;
  }

  // Everything else: straight to the network; fill the offline cache opportunistically.
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (SHELL_FILES.includes(url.pathname + (url.search || "")) && response.ok) {
          const copy = response.clone();
          caches.open(SHELL_CACHE).then((cache) => cache.put(event.request, copy));
        }
        return response;
      })
      .catch(() => caches.match(event.request).then((hit) => hit || Response.error()))
  );
});
