import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import "./styles/index.css";

// iOS standalone(ホーム画面アプリ)のリロード直後に 100dvh が一段短く評価され
// 下部ナビが浮く問題への対策。standalone で正確な window.innerHeight を高さに反映する。
// （visualViewport.height は standalone で短い値を返すため使わない。#root の高さ
//  のみ更新し、スクロールは各画面内で行うため操作は妨げない）
function setAppHeight() {
  document.documentElement.style.setProperty("--app-vh", `${window.innerHeight}px`);
}
setAppHeight();
// 起動直後の取りこぼし対策で数フレーム後にも再設定
requestAnimationFrame(setAppHeight);
setTimeout(setAppHeight, 200);
window.addEventListener("resize", setAppHeight);
window.addEventListener("orientationchange", setAppHeight);
window.addEventListener("pageshow", setAppHeight);

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
