/* Ubuntu Control Deck Service Worker
 * 方針: アプリシェル（HTML/JS/CSS/アイコン）のみをキャッシュしオフライン起動を可能にする。
 * API レスポンス・ログ・ファイル内容など機密になりうるデータは一切キャッシュしない。 */
const CACHE = "control-deck-shell-v23";
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


/* Web Push（設定 → 通知 → この端末で受け取る）。
 * サーバーは RFC 8291 で暗号化した JSON（title・body・url・tag）を送る。iOS は届いたら必ず
 * 表示しないと購読を外すので、中身が読めなくても何かを出す。通知への返信はできない（iOS）ので、
 * タップしたらその画面（会話・実行結果）を開く、までを 1 本の導線にする。 */
self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch { data = { body: event.data ? event.data.text() : "" }; }
  const title = typeof data.title === "string" && data.title ? data.title : "ControlDeck";
  const url = typeof data.url === "string" && data.url.startsWith("/") && !data.url.startsWith("//") ? data.url : "/";
  const shown = self.registration.showNotification(title, {
    body: typeof data.body === "string" ? data.body : "",
    tag: typeof data.tag === "string" ? data.tag : undefined,
    icon: "/icon-192.png",
    badge: "/icon-192.png",
    data: { url },
  });
  // 受け取ったことをサーバーへ返す（201 でも端末に届いたとは限らないので、画面で確かめられるように）。
  // 返事が失敗しても通知は出す。
  const receipt = (ok, error) => typeof data.rid === "string"
    ? fetch("/api/v1/push/receipts", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Requested-With": "ControlDeck" },
      body: JSON.stringify({ rid: data.rid, shown: ok, error: String(error || "").slice(0, 200) }),
    }).catch(() => undefined)
    : Promise.resolve();
  event.waitUntil(shown.then(() => receipt(true), (error) => receipt(false, error && error.message)));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = new URL(event.notification.data?.url || "/", self.location.origin);
  if (target.origin !== self.location.origin) return;
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    // 開いている ControlDeck があれば、それをその画面へ移して前に出す
    for (const client of windows) {
      if (new URL(client.url).origin !== self.location.origin) continue;
      try {
        await client.focus();
        if ("navigate" in client) await client.navigate(target.href);
        return;
      } catch { /* 移せなければ新しく開く */ }
    }
    await self.clients.openWindow(target.href);
  })());
});
