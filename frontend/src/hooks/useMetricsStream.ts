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
        setConnected(true);
      };
      ws.onmessage = (ev) => {
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
        connect();
      }
    };

    connect();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      closed = true;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
      ws?.close();
      setConnected(false);
    };
  }, [enabled, push, setConnected]);
}
