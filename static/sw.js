// Everything in this app needs the server, so nothing is served from cache while it answers.
// The one cached page explains what to check when it does not (ADR 0007).
// Bump the cache name whenever offline.html changes, or installed phones keep the old copy.
const CACHE = "lecture-offline-v1";
const OFFLINE_URL = "/offline.html";

self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.add(OFFLINE_URL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", event => {
  event.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key))))
    .then(() => self.clients.claim()));
});

// Only page loads are touched; every other request goes to the network as if this worker did
// not exist. A page load fails two ways: fetch() rejects when nothing answers at all, and the
// persistent `tailscale serve` proxy answers a gateway error while the server behind it is
// down. The app never serves a page with those codes, so both mean the server is unreachable.
// A 404 or 500 still reaches the page as is.
// no-cache makes every page load ask the server: StaticFiles sends no Cache-Control, so the
// HTTP cache would otherwise hand back a stale index.html while the server is down.
const GATEWAY_ERRORS = [502, 503, 504];

self.addEventListener("fetch", event => {
  if (event.request.mode !== "navigate") return;
  event.respondWith(fetch(event.request, {cache: "no-cache"})
    .then(response => GATEWAY_ERRORS.includes(response.status) ? caches.match(OFFLINE_URL) : response)
    .catch(() => caches.match(OFFLINE_URL)));
});
