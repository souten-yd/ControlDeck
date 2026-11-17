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
