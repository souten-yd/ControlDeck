import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { ErrorBoundary } from "./components/ErrorBoundary";
import "./styles/index.css";

// iOS standalone(ホーム画面アプリ)ではリロード後に動的ビューポート高(dvh/innerHeight)が
// 縮んだ値で固定され下部ナビが浮く。standalone では縮まない large viewport(100vh)を
// 使うため html にクラスを付ける（CSS 側で #root の高さを切り替える）。
const isStandalone =
  (window.navigator as unknown as { standalone?: boolean }).standalone === true ||
  window.matchMedia("(display-mode: standalone)").matches;
if (isStandalone) document.documentElement.classList.add("pwa-standalone");

// 根本の対処は index.css の html の min-height（WebKit 301108）。ここはそれが効かなかったときの保険。
// iPhone のホーム画面アプリは、再読み込みの後に WebView 自体が上の Safe Area（約 59pt）の分だけ短くなり、
// 画面の下端より上で描画が終わる（実機の診断 2026-11-17: screen 932 / innerHeight 873 / safe 59・34。
// 画面写真でも 873 で切れ、その下は描画されない黒い帯）。#428 は「描画は満寸」と見て殻を 932 に
// 広げたが逆効果で、入力欄の下が描けない領域へはみ出した。殻は報告された高さに合わせ、この状態では
// ホームバーが描画領域の外にあるので、下の Safe Area の余白も付けない。
const isIPhoneStandalone = (window.navigator as unknown as { standalone?: boolean }).standalone === true
  && Math.min(window.screen.width, window.screen.height) <= 500;
if (isIPhoneStandalone) {
  const syncStandaloneHeight = () => {
    const root = document.documentElement;
    const portrait = window.innerHeight >= window.innerWidth;
    const full = portrait ? Math.max(window.screen.width, window.screen.height) : Math.min(window.screen.width, window.screen.height);
    const reported = window.innerHeight;
    // 短くなるのは Safe Area 程度（20〜100px）。それより大きい差（キーボードなど）は別の処理に任せる
    // 上の帯に画面を広げているとき（上の Safe Area がある）だけ「短い」と見る。帯の下から置かれているとき
    // （status-bar-style: black、上の Safe Area 0）は、描画範囲が下端まで届くので下の Safe Area をそのまま使う
    const probe = document.createElement("div");
    probe.style.cssText = "position:fixed;visibility:hidden;padding-top:var(--cd-safe-area-top, env(safe-area-inset-top))";
    document.body.appendChild(probe);
    const safeTop = parseFloat(getComputedStyle(probe).paddingTop) || 0;
    probe.remove();
    const shrunk = safeTop >= 20 && full - reported >= 20 && full - reported <= 100;
    root.style.setProperty("--cd-standalone-height", `${reported}px`);
    if (shrunk) root.style.setProperty("--cd-safe-area-bottom", "0px");
    else root.style.removeProperty("--cd-safe-area-bottom");
    root.dataset.viewportShrunk = shrunk ? String(full - reported) : "";
  };
  syncStandaloneHeight();
  window.addEventListener("resize", syncStandaloneHeight);
  window.addEventListener("orientationchange", () => window.setTimeout(syncStandaloneHeight, 300));
  // 起動の直後は 873 で、文書の高さ（index.css の min-height）を見た iOS が少し後に 932 へ広げる。
  // このとき window の resize が来ないことがある（実機 2026-11-17: innerHeight 932 なのに殻 873 のまま）ので、
  // 読み込み完了・表示の切り替え・少し後にも合わせ直す。
  window.visualViewport?.addEventListener("resize", syncStandaloneHeight);
  window.addEventListener("load", syncStandaloneHeight);
  document.addEventListener("visibilitychange", syncStandaloneHeight);
  for (const delay of [300, 1000, 2500]) window.setTimeout(syncStandaloneHeight, delay);
}

// デプロイ（再ビルド）後、開きっぱなしの旧画面が存在しない旧チャンクを遅延ロードして
// 404 になる（ターミナル/リモート等が開けない）。失敗時は自動で 1 回だけ再読み込みする。
// 診断: 画面の寸法（ログインしていないときは送っても 401 で捨てられる）
void import("./lib/clientTiming").then(({ startViewportDiagnostics }) => startViewportDiagnostics());

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
      {/* 画面より上（layout や認証）で落ちても白くしないための最後の受け皿 */}
      <ErrorBoundary>
        <App />
      </ErrorBoundary>
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
