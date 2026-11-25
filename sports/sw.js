/* 오늘의 경기장 서비스 워커
   - 앱 껍데기(index.html, 아이콘, 매니페스트): 캐시 먼저 (빠른 실행)
   - data/*.json: 네트워크 먼저 (늘 새 데이터), 실패할 때만 캐시
*/
const VERSION = "sports-v26";
const SHELL = [
  "./",
  "./index.html",
  "./manifest.webmanifest",
  "./app-icon-192.png",
  "./app-icon-512.png",
  "./app-icon-180.png",
  "./app-icon-maskable-192.png",
  "./assets/fonts/PretendardVariable.woff2",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(VERSION).then((cache) =>
      /* 한 항목(예: 2MB 글꼴)이 느리거나 실패해도 나머지 껍데기는 그대로 캐시한다.
         cache.addAll 은 하나만 실패해도 전부 버리기 때문이다. */
      Promise.allSettled(SHELL.map((u) => cache.add(u)))
    ).catch(() => undefined)
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  const isData = url.pathname.includes("/data/");
  if (isData) {
    event.respondWith(
      fetch(req)
        .then((res) => {
          const copy = res.clone();
          caches.open(VERSION).then((c) => c.put(req, copy)).catch(() => undefined);
          return res;
        })
        .catch(() => caches.match(req))
    );
    return;
  }

  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req).catch(() => caches.match("./index.html").then((r) => r || caches.match("./")))
    );
    return;
  }

  event.respondWith(
    caches.match(req).then(
      (hit) =>
        hit ||
        fetch(req).then((res) => {
          const copy = res.clone();
          caches.open(VERSION).then((c) => c.put(req, copy)).catch(() => undefined);
          return res;
        })
    )
  );
});
