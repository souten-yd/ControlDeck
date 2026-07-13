import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import "./styles/index.css";

// iOS PWA 対策: 実際の表示高さを CSS 変数へ反映する。
// dvh だけだとソフトキーボード開閉後にビューポート高が縮んだまま戻らず
// 下部ナビが黒い余白の上に浮くことがあるため、innerHeight を実測して復帰させる。
function syncAppHeight() {
  const h = window.visualViewport?.height ?? window.innerHeight;
  document.documentElement.style.setProperty("--app-h", `${Math.round(h)}px`);
}
syncAppHeight();
window.addEventListener("resize", syncAppHeight);
window.addEventListener("orientationchange", syncAppHeight);
window.visualViewport?.addEventListener("resize", syncAppHeight);
// 復帰・フォーカス変化後にも再計測（キーボード閉じの取りこぼし対策）
window.addEventListener("pageshow", syncAppHeight);
window.addEventListener("focusout", () => setTimeout(syncAppHeight, 100));

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
