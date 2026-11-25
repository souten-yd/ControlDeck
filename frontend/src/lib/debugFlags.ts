/** 実機（iOS Safari の PWA など）でテロップのカクつきの原因を切り分けるための、URL の目印。既定の動きは変えない。
 *  ?marquee=css  テロップを WAAPI ではなく CSS の @keyframes（transform だけ）で動かす
 *  ?live=off     ホームの定期の取り直し（LlmHomeCard・アプリ・通知ほか）とメトリクスの描き直しを止める
 *  目印は端末（localStorage）に覚える。?marquee=default / ?live=on か、設定 → 表示 のボタンで解除。 */
const KEYS = { marquee: "cd.debug.marquee", live: "cd.debug.live" } as const;
type Flag = keyof typeof KEYS;
const ACTIVE: Record<Flag, string> = { marquee: "css", live: "off" };

const read = (k: string) => { try { return localStorage.getItem(k); } catch { return null; } };
const write = (k: string, v: string | null) => { try { if (v) localStorage.setItem(k, v); else localStorage.removeItem(k); } catch { /* 保存できなくても動く */ } };

/** 起動時に URL の目印を読み、覚える（有効な値なら保存・解除の値なら消す）。 */
export function applyUrlFlags(search = typeof location === "undefined" ? "" : location.search): void {
  const q = new URLSearchParams(search);
  for (const f of Object.keys(KEYS) as Flag[]) {
    const v = q.get(f);
    if (v == null) continue;
    write(KEYS[f], v === ACTIVE[f] ? v : null);
  }
}
applyUrlFlags();

export const marqueeCss = (): boolean => read(KEYS.marquee) === ACTIVE.marquee;
export const liveOff = (): boolean => read(KEYS.live) === ACTIVE.live;
/** refetchInterval に使う。live=off なら止める（false）。 */
export const liveInterval = <T,>(ms: T): T | false => (liveOff() ? false : ms);
export function clearDebugFlags(): void { for (const f of Object.keys(KEYS) as Flag[]) write(KEYS[f], null); }
