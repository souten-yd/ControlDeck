import { useEffect } from "react";
import { wsUrl } from "../api/client";
import { useMetrics } from "../stores";
import type { MetricsSnapshot } from "../types";

/** メトリクス WebSocket。単一接続、切断時は指数バックオフで再接続、
 * タブ非表示時は切断して電池・通信を節約する。 */
export function useMetricsStream(enabled: boolean) {
  const push = useMetrics((s) => s.push);
  const setConnected = useMetrics((s) => s.setConnected);

  useEffect(() => {
    if (!enabled) return;
    let ws: WebSocket | null = null;
    let retry = 0;
    let closed = false;
    let timer: ReturnType<typeof setTimeout>;

    const connect = () => {
      if (closed || document.hidden) return;
      ws = new WebSocket(wsUrl("/system/metrics/stream"));
      ws.onopen = () => {
        retry = 0;
        lastMessageAt = Date.now();
        setConnected(true);
      };
      ws.onmessage = (ev) => {
        lastMessageAt = Date.now();
        try {
          push(JSON.parse(ev.data) as MetricsSnapshot);
        } catch {
          /* 破損メッセージは無視 */
        }
      };
      ws.onclose = () => {
        setConnected(false);
        if (!closed && !document.hidden) {
          timer = setTimeout(connect, Math.min(30_000, 1000 * 2 ** retry++));
        }
      };
      ws.onerror = () => ws?.close();
    };

    const onVisibility = () => {
      if (document.hidden) {
        ws?.close();
      } else if (!ws || ws.readyState >= WebSocket.CLOSING) {
        retry = 0;
        connect();
      }
    };

    // 回線が戻ったのに backoff を待たせない。切れたまま画面を開いていると、
    // 待ちが 30 秒まで伸びて「再接続中」が居座る。復帰の合図が来たら待ちを
    // 捨てて繋ぎ直す。連打にならないよう、直前の試行から 3 秒は空ける。
    let lastRevive = 0;
    const onOnline = () => {
      if (closed || document.hidden) return;
      if (ws && ws.readyState <= WebSocket.OPEN) return;
      const now = Date.now();
      if (now - lastRevive < 3_000) return;
      lastRevive = now;
      clearTimeout(timer);
      retry = 0;
      connect();
    };
    // collector は定期的に snapshot を送る。それが途切れたら経路が死んでいる。
    // 携帯では黙って切れることがあり、TCP の timeout まで誰も気づかない。
    let lastMessageAt = Date.now();
    const watchdog = setInterval(() => {
      if (closed || document.hidden) return;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      if (Date.now() - lastMessageAt < 45_000) return;
      lastMessageAt = Date.now();
      ws.close();          // onclose が backoff つきで繋ぎ直す
    }, 5_000);
    connect();
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("online", onOnline);
    window.addEventListener("pageshow", onOnline);
    return () => {
      closed = true;
      clearTimeout(timer);
      clearInterval(watchdog);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("online", onOnline);
      window.removeEventListener("pageshow", onOnline);
      ws?.close();
      setConnected(false);
    };
  }, [enabled, push, setConnected]);
}
