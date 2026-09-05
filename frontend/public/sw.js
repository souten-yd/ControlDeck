/* Ubuntu Control Deck Service Worker
 * 方針: アプリシェル（HTML/JS/CSS/アイコン）のみをキャッシュしオフライン起動を可能にする。
 * API レスポンス・ログ・ファイル内容など機密になりうるデータは一切キャッシュしない。 */
const CACHE = "control-deck-shell-v17";
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
        // 再デプロイで消えた旧chunkの404を保存しない（保存すると復帰できなくなる）
        if (res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));
        } else if (res.status === 404) {
          // 消えた chunk を指しているのは、掴んでいる index が古いということ。
          // 控えを捨てれば、次の読み込みは必ず network から取り直す。
          caches.delete(CACHE);
        }
        return res;
      })),
    );
    return;
  }

  // ナビゲーション（SPA）: network-first。取れたら控えを最新へ入れ替える。
  //
  // 入れ替えないと、再デプロイ後も install 時の古い index が残る。回線が一瞬
  // 切れてフォールバックが使われると、その古い index が既に消えた chunk を指し、
  // 読み込めずに白い画面になる。携帯では経路が途切れるので現実に起きる。
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req).then((res) => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put("/", copy));
        }
        return res;
      }).catch(() => caches.match("/")),
    );
    return;
  }

  // その他の静的ファイル: cache-first
  event.respondWith(caches.match(req).then((hit) => hit || fetch(req)));
});
