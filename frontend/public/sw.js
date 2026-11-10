/* Service worker: the app shell and hashed build assets, for a phone that drops its connection.
 *
 * - Caches only the app shell ("/") and same-origin /assets/* (hashed, so cache-first is safe).
 * - Never caches or intercepts /api/ or any non-GET request. The one exception is the
 *   WhatsApp / Android share target: POST /share-target stores the shared files in the
 *   "shared-files" cache and redirects to the receiving page.
 * - Navigations are network-first, falling back to the cached shell when offline.
 *
 * Bump SHELL_VERSION when this file's caching rules change. Old "erp-" caches are deleted
 * on activate. SHARED_CACHE is kept: it holds files the person has not yet processed.
 */
const SHELL_VERSION = "v1";
const SHELL_CACHE = `erp-shell-${SHELL_VERSION}`;
const ASSET_CACHE = `erp-assets-${SHELL_VERSION}`;
const SHARED_CACHE = "shared-files";
const KEEP_CACHES = [SHELL_CACHE, ASSET_CACHE, SHARED_CACHE];
const SHARE_PATH = "/share-target";

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      .then((cache) => cache.add(new Request("/", { cache: "reload" })))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((names) =>
        Promise.all(names.filter((name) => !KEEP_CACHES.includes(name)).map((name) => caches.delete(name)))
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (request.method === "POST" && url.pathname === SHARE_PATH) {
    event.respondWith(receiveShare(request));
    return;
  }

  // Everything else that is not a GET goes straight to the network, untouched.
  if (request.method !== "GET") return;
  // Business data is never cached and never served stale.
  if (url.pathname.startsWith("/api/")) return;

  if (request.mode === "navigate") {
    event.respondWith(networkFirstShell(request));
    return;
  }
  if (url.pathname.startsWith("/assets/")) {
    event.respondWith(cacheFirstAsset(request));
  }
});

/** Navigations: the live page when online; the cached shell when not. */
async function networkFirstShell(request) {
  try {
    const response = await fetch(request);
    if (response.ok && (response.headers.get("content-type") || "").includes("text/html")) {
      const cache = await caches.open(SHELL_CACHE);
      cache.put(new Request("/"), response.clone());
    }
    return response;
  } catch (error) {
    const cached = await caches.match(new Request("/"), { cacheName: SHELL_CACHE });
    if (cached) return cached;
    throw error;
  }
}

/** Hashed build assets: cache-first, filled on first use. */
async function cacheFirstAsset(request) {
  const cache = await caches.open(ASSET_CACHE);
  const cached = await cache.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response.ok && response.type === "basic") {
    cache.put(request, response.clone());
  }
  return response;
}

/** The share target: keep the shared files for the receiving page, then redirect to it. */
async function receiveShare(request) {
  try {
    const form = await request.formData();
    const files = form.getAll("files").filter((item) => item instanceof File);
    const cache = await caches.open(SHARED_CACHE);
    // One share at a time: anything still waiting from an earlier share is replaced.
    for (const key of await cache.keys()) await cache.delete(key);
    for (let index = 0; index < files.length; index += 1) {
      const file = files[index];
      const body = new Response(file, {
        headers: {
          "Content-Type": file.type || "application/octet-stream",
          "X-File-Name": encodeURIComponent(file.name || `shared-${index + 1}`),
        },
      });
      await cache.put(new Request(`/shared/${index}`), body);
    }
    return Response.redirect(`${SHARE_PATH}?received=${files.length}`, 303);
  } catch (error) {
    return Response.redirect(`${SHARE_PATH}?received=0`, 303);
  }
}
