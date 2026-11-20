/** active の間だけ画面を消さない（Screen Wake Lock）。対応していない・断られたときは何もしない。
 *
 * 2026-11-20 利用者の依頼（便利機能）から。iPhone は画面が消えると読み上げ（Web Audio）とマイクが止まるので、
 * 読み上げ・音声入力・会話モードの間は消さない。オンオフは 設定 → 操作性（stores/uxPrefs の keepAwake）。
 * 画面を裏へ回すと OS が外すので、表へ戻ったら取り直す。 */
import { useEffect } from "react";

interface WakeLockSentinelLike { released: boolean; release: () => Promise<void> }

export function useWakeLock(active: boolean) {
  useEffect(() => {
    const wakeLock = (navigator as Navigator & { wakeLock?: { request: (type: "screen") => Promise<WakeLockSentinelLike> } }).wakeLock;
    if (!active || !wakeLock) return;
    let sentinel: WakeLockSentinelLike | null = null;
    let done = false;
    const acquire = () => {
      if (done || document.hidden || (sentinel && !sentinel.released)) return;
      wakeLock.request("screen").then((value) => {
        if (done) void value.release().catch(() => undefined);
        else sentinel = value;
      }).catch(() => undefined);
    };
    acquire();
    document.addEventListener("visibilitychange", acquire);
    return () => {
      done = true;
      document.removeEventListener("visibilitychange", acquire);
      if (sentinel && !sentinel.released) void sentinel.release().catch(() => undefined);
    };
  }, [active]);
}
