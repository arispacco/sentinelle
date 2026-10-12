// Sentinelle — Service Worker
// Stratégie :
//  - Assets statiques (/, /static/*, manifest) : network-first, cache SEULEMENT si r.ok && type basic/cors.
//  - Fallback hors-ligne : caches.match(..., { ignoreSearch: true }) → tolère le mismatch "?v=N".
//  - API : réseau uniquement, réponse JSON offline en cas d'échec (jamais mise en cache).
const CACHE = "sentinelle-hub-v7";

// Pré-cache des URL versionnées EXACTES servies par index.html (+ fallbacks sans query).
const SHELL = [
  "/",
  "/static/app.css?v=7",
  "/static/app.js?v=10",
  "/static/app.css",
  "/static/app.js",
  "/static/icons/icon.svg",
  "/manifest.webmanifest",
];

function isCacheable(response) {
  try {
    return !!response && response.ok && (response.type === "basic" || response.type === "cors");
  } catch (_) {
    return false;
  }
}

async function precache() {
  const cache = await caches.open(CACHE);
  await Promise.allSettled(
    SHELL.map(async (url) => {
      try {
        const res = await fetch(new Request(url, { cache: "reload" }));
        if (isCacheable(res)) await cache.put(url, res);
      } catch (_) {
        /* asset indisponible au moment de l'install : le runtime le remplira */
      }
    })
  );
}

self.addEventListener("install", (e) => {
  e.waitUntil(precache());
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// Network-first avec garde-fou : on ne met en cache QUE les réponses valides,
// et on retombe sur le cache (ignoreSearch) si le réseau renvoie une erreur serveur.
async function networkFirstAsset(req) {
  try {
    const fresh = await fetch(req);
    if (isCacheable(fresh)) {
      const cache = await caches.open(CACHE);
      cache.put(req, fresh.clone()).catch(() => {});
      return fresh;
    }
    // 5xx / opaque / erreur : ne JAMAIS empoisonner le cache avec ça.
    const cached = await caches.match(req, { ignoreSearch: true });
    return cached || fresh;
  } catch (_) {
    const cached = await caches.match(req, { ignoreSearch: true });
    if (cached) return cached;
    return new Response("Offline", {
      status: 503,
      headers: { "Content-Type": "text/plain; charset=utf-8" },
    });
  }
}

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;

  let url;
  try {
    url = new URL(req.url);
  } catch (_) {
    return;
  }
  // Laisser passer le cross-origin (CDN charts, fonts Google…) : pas notre responsabilité.
  if (url.origin !== self.location.origin) return;

  const isStaticAsset =
    url.pathname === "/" ||
    url.pathname.startsWith("/static/") ||
    url.pathname === "/manifest.webmanifest";

  if (isStaticAsset) {
    e.respondWith(networkFirstAsset(req));
    return;
  }

  // API & autres : réseau d'abord, jamais de cache, fallback JSON offline propre.
  e.respondWith(
    fetch(req).catch(
      () =>
        new Response(JSON.stringify({ error: "offline" }), {
          status: 503,
          headers: { "Content-Type": "application/json" },
        })
    )
  );
});

/* ---- Web Push Notifications ---- */
self.addEventListener("push", (event) => {
  let data = { title: "Sentinelle Alerte", body: "Nouvelle opportunité ou mise à jour de veille détectée.", url: "/" };
  if (event.data) {
    try {
      data = event.data.json();
    } catch (e) {
      data.body = event.data.text();
    }
  }
  event.waitUntil(
    self.registration.showNotification(data.title || "Sentinelle", {
      body: data.body,
      icon: "/static/icons/icon.svg",
      badge: "/static/icons/icon.svg",
      data: { url: data.url || "/" },
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const urlToOpen = event.notification.data?.url || "/";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((windowClients) => {
      for (let client of windowClients) {
        if (client.url.includes(self.origin) && "focus" in client) {
          return client.focus();
        }
      }
      if (self.clients.openWindow) {
        return self.clients.openWindow(urlToOpen);
      }
    })
  );
});
