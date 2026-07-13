/* Ubuntu Control Deck Service Worker
 * 方針: アプリシェル（HTML/JS/CSS/アイコン）のみをキャッシュしオフライン起動を可能にする。
 * API レスポンス・ログ・ファイル内容など機密になりうるデータは一切キャッシュしない。 */
const CACHE = "control-deck-shell-v3";
const SHELL = ["/", "/manifest.webmanifest", "/favicon.svg", "/icon-192.png", "/icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);

  // 同一オリジンの GET のみ扱う。API/WS/認証は絶対にキャッシュしない
  if (req.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/api/")) {
    return;
  }

  // ビルド済みアセットは cache-first（ハッシュ付きファイル名なので安全）
  if (url.pathname.startsWith("/assets/")) {
    event.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy));
        return res;
      })),
    );
    return;
  }

  // ナビゲーション（SPA）: network-first、オフライン時はキャッシュした index にフォールバック
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req).catch(() => caches.match("/")),
    );
    return;
  }

  // その他の静的ファイル: cache-first
  event.respondWith(caches.match(req).then((hit) => hit || fetch(req)));
});
