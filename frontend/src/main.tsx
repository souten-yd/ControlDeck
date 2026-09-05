import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import "./styles/index.css";

// iOS standalone(ホーム画面アプリ)ではリロード後に動的ビューポート高(dvh/innerHeight)が
// 縮んだ値で固定され下部ナビが浮く。standalone では縮まない large viewport(100vh)を
// 使うため html にクラスを付ける（CSS 側で #root の高さを切り替える）。
const isStandalone =
  (window.navigator as unknown as { standalone?: boolean }).standalone === true ||
  window.matchMedia("(display-mode: standalone)").matches;
if (isStandalone) document.documentElement.classList.add("pwa-standalone");

// デプロイ（再ビルド）後、開きっぱなしの旧画面が存在しない旧チャンクを遅延ロードして
// 404 になる（ターミナル/リモート等が開けない）。失敗時は自動で 1 回だけ再読み込みする。
window.addEventListener("vite:preloadError", (e) => {
  const last = Number(sessionStorage.getItem("cd-chunk-reload") || 0);
  if (Date.now() - last > 10_000) {
    e.preventDefault();
    sessionStorage.setItem("cd-chunk-reload", String(Date.now()));
    location.reload();
  }
});

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </React.StrictMode>,
);

// PWA: Service Worker 登録（本番ビルドのみ。開発時は登録しない）
if ("serviceWorker" in navigator && !import.meta.env.DEV) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      /* 登録失敗は致命的でないため無視 */
    });
  });
}

// 遅延読み込みの chunk が取れないときは、掴んでいる index が古い。放っておくと
// 画面が白いまま戻らないので、控えを捨てて一度だけ読み直す。何度も繰り返さない
// よう、直前の試みから 30 秒は空ける（本当に取れない状況で loop にしない）。
const RELOAD_MARK = "control-deck:shell-reloaded-at";
const healStaleShell = () => {
  try {
    const last = Number(sessionStorage.getItem(RELOAD_MARK) || 0);
    if (Date.now() - last < 30_000) return;
    sessionStorage.setItem(RELOAD_MARK, String(Date.now()));
  } catch {
    return;   // storage が使えないなら諦める。無限 reload よりましである
  }
  void (async () => {
    try {
      const keys = await caches.keys();
      await Promise.all(keys.map((key) => caches.delete(key)));
    } catch {
      /* 消せなくても reload はしてみる */
    }
    location.reload();
  })();
};
window.addEventListener("vite:preloadError", healStaleShell);
window.addEventListener("unhandledrejection", (event) => {
  const message = String((event.reason as { message?: unknown })?.message ?? event.reason ?? "");
  if (/Failed to fetch dynamically imported module|Importing a module script failed/i.test(message)) {
    healStaleShell();
  }
});
