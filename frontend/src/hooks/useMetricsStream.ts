import { useEffect } from "react";
import { wsUrl } from "../api/client";
import {
  UI_GRACE_MS,
  discardSocket,
  looksAlive,
  looksDead,
  retryDelayMs,
  watchForReturn,
} from "../lib/liveConnection";
import { useMetrics } from "../stores";
import type { MetricsSnapshot } from "../types";

/** メトリクス WebSocket。
 *
 * 生死の判断・再接続・復帰の扱いは liveConnection の方針に従う。ここ独自の
 * 事情は「画面を消している間は繋がない（電池と通信を使わない）」ことだけ。
 */
export function useMetricsStream(enabled: boolean) {
  const push = useMetrics((s) => s.push);
  const setConnected = useMetrics((s) => s.setConnected);

  useEffect(() => {
    if (!enabled) return;
    let ws: WebSocket | null = null;
    let attempt = 0;
    let closed = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let lastMessageAt = Date.now();

    // 一瞬の途切れは表示に出さない。携帯では珍しくなく、すぐ戻るものまで
    // 「再接続中」と出すと、実際は繋がっているのに壊れて見える。
    let graceTimer: ReturnType<typeof setTimeout> | undefined;
    const showDisconnected = () => {
      if (graceTimer !== undefined) return;
      graceTimer = setTimeout(() => {
        graceTimer = undefined;
        if (!closed) setConnected(false);
      }, UI_GRACE_MS);
    };
    const showConnected = () => {
      clearTimeout(graceTimer);
      graceTimer = undefined;
      setConnected(true);
    };

    const connect = () => {
      if (closed || document.hidden) return;
      ws = new WebSocket(wsUrl("/system/metrics/stream"));
      ws.onopen = () => {
        attempt = 0;
        lastMessageAt = Date.now();
        showConnected();
      };
      ws.onmessage = (event) => {
        lastMessageAt = Date.now();
        try {
          push(JSON.parse(event.data) as MetricsSnapshot);
        } catch {
          /* 壊れた message は捨てる */
        }
      };
      ws.onclose = () => {
        showDisconnected();
        if (closed || document.hidden) return;
        timer = setTimeout(connect, retryDelayMs(attempt++));
      };
      ws.onerror = () => ws?.close();
    };

    /** 疑わしければ捨てて繋ぎ直す。readyState は信用しない。 */
    const revive = () => {
      if (closed) return;
      if (ws?.readyState === WebSocket.CONNECTING) return;
      if (ws?.readyState === WebSocket.OPEN && looksAlive(lastMessageAt)) return;
      discardSocket(ws);
      ws = null;
      clearTimeout(timer);
      attempt = 0;
      connect();
    };

    const onVisibility = () => {
      if (document.hidden) {
        discardSocket(ws);
        ws = null;
        clearTimeout(timer);
        showDisconnected();
        return;
      }
      revive();
    };

    // 黙ったまま切れることがある。TCP の timeout を待つより早く見切る。
    const watchdog = setInterval(() => {
      if (closed || document.hidden) return;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      if (!looksDead(lastMessageAt)) return;
      lastMessageAt = Date.now();
      ws.close();          // onclose が再接続を予約する
    }, 2_000);

    connect();
    document.addEventListener("visibilitychange", onVisibility);
    const stopWatching = watchForReturn(revive);
    return () => {
      closed = true;
      clearTimeout(timer);
      clearTimeout(graceTimer);
      clearInterval(watchdog);
      document.removeEventListener("visibilitychange", onVisibility);
      stopWatching();
      discardSocket(ws);
      setConnected(false);
    };
  }, [enabled, push, setConnected]);
}
