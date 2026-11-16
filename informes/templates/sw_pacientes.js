/* Service worker del portal del paciente (versión {{ version }}).
   Guarda solo archivos fijos y la página «sin conexión»; los exámenes, informes e imágenes
   siempre se piden al servidor y nunca quedan guardados en el teléfono. */
const CACHE = "pacientes-{{ version }}";
const FIJOS = {{ (fijos + [sin_conexion])|tojson }};

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(FIJOS)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k.startsWith("pacientes-") && k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const r = e.request, u = new URL(r.url);
  if (r.method !== "GET" || u.origin !== location.origin) return;
  // páginas: siempre del servidor; si no hay internet, la página «sin conexión»
  if (r.mode === "navigate") {
    e.respondWith(fetch(r).catch(() => caches.match({{ sin_conexion|tojson }})));
    return;
  }
  // archivos fijos: del teléfono si ya están, si no del servidor
  if (FIJOS.includes(u.pathname)) e.respondWith(caches.match(r).then(x => x || fetch(r)));
});
