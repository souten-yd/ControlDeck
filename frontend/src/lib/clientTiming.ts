/** 画面側の時間を診断へ送る（決まり: 異常を検知できる診断を足す。2026-11-17「会話の読み込みが遅い」）。
 *
 * サーバー側の記録では会話の API は 2 秒未満で返っていて、詰まりも無かった。携帯の「遅い」が
 * 通信（Tailscale 経由など）か、API の順番待ちか、描画かを分けるため、画面を開いてからの各段階の
 * 時間と、その間の API の時間（画面から見た往復・待ち・大きさ）を送る。1 画面を開くたびに 1 回。 */
const SKIP = /\/(diagnostics\/client|jobs\/stream|stream)(\?|$)/;

export function reportClientTiming(page: string, startedAt: number, marks: Record<string, number>): void {
  try {
    const api = (performance.getEntriesByType("resource") as PerformanceResourceTiming[])
      .filter((entry) => entry.startTime >= startedAt - 50 && entry.name.includes("/api/v1/") && !SKIP.test(entry.name))
      .map((entry) => ({
        // 会話 ID などは伏せて、どの API かだけ残す
        path: new URL(entry.name).pathname.replace(/\/[0-9a-f]{12,}(?=\/|$)/g, "/{id}").slice(0, 120),
        ms: entry.duration,
        wait_ms: entry.responseStart > 0 ? entry.responseStart - entry.requestStart : 0,
        kb: (entry.transferSize || entry.encodedBodySize || 0) / 1024,
      }))
      .sort((a, b) => b.ms - a.ms)
      .slice(0, 12);
    const nav = performance.getEntriesByType("navigation")[0] as PerformanceNavigationTiming | undefined;
    const firstLoad = nav && startedAt - nav.startTime < 5000
      ? { page_ttfb: nav.responseStart, page_dom_ready: nav.domContentLoadedEventEnd } : {};
    const connection = (navigator as unknown as { connection?: { effectiveType?: string } }).connection?.effectiveType ?? "";
    const standalone = (navigator as unknown as { standalone?: boolean }).standalone === true
      || window.matchMedia("(display-mode: standalone)").matches;
    void fetch("/api/v1/diagnostics/client", {
      method: "POST", credentials: "same-origin", keepalive: true,
      headers: { "Content-Type": "application/json", "X-Requested-With": "ControlDeck" },
      body: JSON.stringify({ page, marks: { ...firstLoad, ...marks }, api, standalone, connection }),
    }).catch(() => undefined);
  } catch {
    // 計測の失敗で画面を止めない
  }
}


/** 画面の寸法を診断へ送る（2026-11-17: iPhone のホーム画面アプリで下が切れる。100vh が縮む前提で直したが
 * 直らなかったので、推測をやめて実機の値を見る）。開いたときと、寸法が変わって 1 秒落ち着いたとき。同じ値は送らない。 */
let lastViewport = "";
let viewportTimer = 0;

function px(value: string): number {
  const parsed = parseFloat(value);
  return Number.isFinite(parsed) ? parsed : -1;
}

function measureViewport(): Record<string, number> {
  const probe = document.createElement("div");
  probe.style.cssText = "position:fixed;visibility:hidden;pointer-events:none;"
    + "padding-top:env(safe-area-inset-top);padding-bottom:env(safe-area-inset-bottom);";
  document.body.appendChild(probe);
  const safe = getComputedStyle(probe);
  const safeTop = px(safe.paddingTop);
  const safeBottom = px(safe.paddingBottom);
  probe.remove();
  const root = document.getElementById("root")?.getBoundingClientRect();
  const composer = document.querySelector("[data-assistant-input-card]")?.getBoundingClientRect();
  const vv = window.visualViewport;
  return {
    inner_w: window.innerWidth, inner_h: window.innerHeight, outer_h: window.outerHeight,
    client_h: document.documentElement.clientHeight,
    screen_w: window.screen.width, screen_h: window.screen.height,
    vv_h: vv ? Math.round(vv.height * 10) / 10 : -1, vv_top: vv ? Math.round(vv.offsetTop * 10) / 10 : -1,
    dpr: window.devicePixelRatio, safe_top: safeTop, safe_bottom: safeBottom,
    root_top: root ? Math.round(root.top) : -1, root_h: root ? Math.round(root.height) : -1,
    root_bottom: root ? Math.round(root.bottom) : -1,
    composer_bottom: composer ? Math.round(composer.bottom) : -1,
    shrunk: Number(document.documentElement.dataset.viewportShrunk || 0),
    // このページが起動（0）・再読み込み（1）・戻る（2）のどれで開かれたか。iPhone は再読み込みの後に
    // WebView が短くなると見ている（2026-11-17）ので、その裏付けに
    nav_type: ({ navigate: 0, reload: 1, back_forward: 2, prerender: 3 } as Record<string, number>)[
      (performance.getEntriesByType("navigation")[0] as PerformanceNavigationTiming | undefined)?.type ?? ""] ?? -1,
    since_load_s: Math.round(performance.now() / 1000),
    // iOS の版（例 26.1 → 26.1）。描画範囲が短くなるのが iOS の更新からかを見るため
    ios: Number((/OS (\d+)_(\d+)/.exec(navigator.userAgent) ?? []).slice(1, 3).join(".") || -1),
    keyboard_fit: document.documentElement.classList.contains("cd-keyboard-fit") ? 1 : 0,
  };
}

export function startViewportDiagnostics(): void {
  const send = () => {
    try {
      const marks = measureViewport();
      const key = JSON.stringify(marks);
      if (key === lastViewport) return;
      lastViewport = key;
      const standalone = (navigator as unknown as { standalone?: boolean }).standalone === true
        || window.matchMedia("(display-mode: standalone)").matches;
      void fetch("/api/v1/diagnostics/client", {
        method: "POST", credentials: "same-origin", keepalive: true,
        headers: { "Content-Type": "application/json", "X-Requested-With": "ControlDeck" },
        body: JSON.stringify({ page: "viewport", marks, api: [], standalone,
          connection: location.pathname.split("/")[1]?.slice(0, 20) || "home" }),
      }).catch(() => undefined);
    } catch {
      // 計測の失敗で画面を止めない
    }
  };
  const schedule = () => {
    window.clearTimeout(viewportTimer);
    viewportTimer = window.setTimeout(send, 1000);
  };
  window.addEventListener("resize", schedule);
  window.visualViewport?.addEventListener("resize", schedule);
  window.addEventListener("orientationchange", schedule);
  // 画面を切り替えたとき（入力欄の位置が変わる）にも送る
  window.addEventListener("popstate", schedule);
  window.setTimeout(send, 2500);
  window.setInterval(() => { if (document.visibilityState === "visible") send(); }, 30_000);
}
