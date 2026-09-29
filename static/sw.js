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
// not exist. fetch() rejects only when the server cannot be reached at all, so a 404 or 500
// still reaches the page as is.
self.addEventListener("fetch", event => {
  if (event.request.mode !== "navigate") return;
  event.respondWith(fetch(event.request).catch(() => caches.match(OFFLINE_URL)));
});
