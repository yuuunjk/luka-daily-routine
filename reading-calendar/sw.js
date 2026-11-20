/* LUKA 독서 캘린더 — Service Worker
   삼성 인터넷·크롬이 웹앱(WebAPK)으로 설치할 수 있도록 추가한 최소 SW.
   - install:  대기 없이 바로 새 버전으로 넘어감
   - activate: 열려 있는 화면을 새 SW가 곧바로 제어 + 옛 캐시 정리
   - fetch:    네트워크 우선. 온라인일 때는 늘 서버의 최신 파일을 쓰고,
               오프라인일 때만 캐시로 대신한다(구버전 고착 방지).
*/

const CACHE = 'luka-reading-calendar-v1';

self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    try {
      const keys = await caches.keys();
      await Promise.all(
        keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))
      );
    } catch (e) {
      /* 캐시 정리에 실패해도 제어권은 넘긴다 */
    }
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (event) => {
  const req = event.request;

  // GET·같은 출처 요청만 다룬다. 나머지는 브라우저 기본 동작에 맡긴다.
  if (req.method !== 'GET') return;
  let url;
  try {
    url = new URL(req.url);
  } catch (e) {
    return;
  }
  if (url.origin !== self.location.origin) return;

  event.respondWith((async () => {
    try {
      const res = await fetch(req, { cache: 'no-cache' });
      if (res && res.ok && res.type === 'basic') {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
      }
      return res;
    } catch (err) {
      // 오프라인: 마지막으로 받아 둔 응답, 없으면 앱 화면으로
      const cached = await caches.match(req, { ignoreSearch: true });
      if (cached) return cached;
      if (req.mode === 'navigate') {
        const shell = await caches.match('./index.html', { ignoreSearch: true });
        if (shell) return shell;
      }
      throw err;
    }
  })());
});
