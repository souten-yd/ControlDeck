/** 左右に指でずらして「前・次」へ切り替える手の操作（チャンネルの切り替え・まとめのカード）。
 *
 * 2026-11-24 利用者「CH の上で左右にフリックすると CH が変わるように」「今週のまとめの上で左右にフリックすると今朝のまとめ」。
 * 手本は Claude アプリ: 閾値を越えたら発火するだけのフリックは不評（v0.98）なので、指に付いて動き、離した位置と速さで決める。
 * - 最初の LOCK px で横か縦かを決める。縦ならこの部品は何もしない（ページのスクロールに任せる。touch-action: pan-y は CSS の .nw-swipe）。
 * - 画面の左右の端 EDGE px（アプリのメニューを引く操作 useNavSwipe と同じ 20px） は始点にしない（iPhone の戻る/進むと取り合うため）。マウスは対象外（リンクの選択・ドラッグと取り合う）。
 * - 離したとき、幅の 25% 以上ずれていた、または速さが FLICK px/ms 以上でずらした向きなら次へ。そうでなければ元へ戻す。端では 0.25 倍に重くして止める。
 * - 動かすのは transform と opacity だけ。prefers-reduced-motion では動きを 80ms に縮める。
 * mode:
 *   "swap"  要素そのものが指に付いて動き、切り替えるとき出て行って新しい中身が反対側から入る（チャンネルの切り替え）。
 *   "track" 要素は幅 N 枚分の帯で、親を窓にして -index × 幅 だけずらして置く（まとめのカード）。 */
import { useCallback, useEffect, useLayoutEffect, useRef, type MouseEvent, type PointerEvent } from "react";

const EDGE = 20, LOCK = 8, COMMIT = 0.25, FLICK = 0.45;

interface Drag { id: number; x0: number; y0: number; lock: "none" | "x"; dx: number; samples: { t: number; x: number }[] }

export function useSwipe<T extends HTMLElement>({ index, count, onIndex, mode, disabled = false }: {
  index: number; count: number; onIndex: (next: number) => void; mode: "swap" | "track"; disabled?: boolean;
}) {
  const ref = useRef<T>(null);
  const drag = useRef<Drag | null>(null);
  const suppress = useRef(false);
  const enterFrom = useRef<number | null>(null);          // swap: 切り替えたあと、どちらから入るか（+1 = 右から）
  const latest = useRef({ index, count, onIndex, mode, disabled });
  latest.current = { index, count, onIndex, mode, disabled };

  /** 動かす要素。swap は ref の中の [data-swipe-move]（無ければ ref 自身）。当たりの範囲（ref）は動かさない（見出し・タブ・ボタンの列は動かない）。 */
  const movers = (): HTMLElement[] => {
    const el = ref.current;
    if (!el) return [];
    if (latest.current.mode === "track") return [el];
    const m = Array.from(el.querySelectorAll<HTMLElement>("[data-swipe-move]"));
    return m.length ? m : [el];
  };
  const reduced = () => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
  const dur = (ms: number) => (reduced() ? 80 : ms);
  const width = () => {
    const el = ref.current;
    if (!el) return 1;
    return (latest.current.mode === "track" ? el.parentElement ?? el : el).clientWidth || 1;
  };
  const base = () => (latest.current.mode === "track" ? -latest.current.index * width() : 0);

  // track: index が変わる（点・切り替え・離したあと）ときは帯をその位置へ。CSS の transition が動かす。
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || mode !== "track") return;
    if (disabled) { el.style.transform = ""; return; }
    el.style.transform = `translate3d(${-index * width()}px,0,0)`;
  }, [index, mode, disabled]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (mode !== "track" || disabled) return;
    const on = () => { const el = ref.current; if (el) { el.style.transition = "none"; el.style.transform = `translate3d(${-latest.current.index * width()}px,0,0)`; requestAnimationFrame(() => { if (el) el.style.transition = ""; }); } };
    window.addEventListener("resize", on);
    return () => window.removeEventListener("resize", on);
  }, [mode, disabled]);   // eslint-disable-line react-hooks/exhaustive-deps

  // swap: 切り替わったあと、新しい中身が反対側から入ってくる。
  useLayoutEffect(() => {
    const from = enterFrom.current;
    if (from == null || mode !== "swap") return;
    enterFrom.current = null;
    const w = width();
    for (const el of movers()) {
      el.style.transform = ""; el.style.opacity = "";
      const a = el.animate?.([{ transform: `translate3d(${from * w * 0.35}px,0,0)`, opacity: 0 }, { transform: "translate3d(0,0,0)", opacity: 1 }], { duration: dur(220), easing: "ease-out" });
      if (a) a.onfinish = () => { el.style.willChange = ""; }; else el.style.willChange = "";
    }
  }, [index, mode]);   // eslint-disable-line react-hooks/exhaustive-deps

  const paint = (dx: number) => {
    if (latest.current.mode === "track") { const el = ref.current; if (el) el.style.transform = `translate3d(${base() + dx}px,0,0)`; return; }
    const o = String(Math.max(0.4, 1 - Math.abs(dx) / width() * 0.6));
    for (const el of movers()) { el.style.transform = `translate3d(${dx}px,0,0)`; el.style.opacity = o; }
  };

  const finish = useCallback((commit: boolean, dx: number) => {
    const el = ref.current;
    const { index: cur, mode: m, onIndex: go } = latest.current;
    if (!el) return;
    const w = width();
    const dir = dx > 0 ? -1 : 1;                  // 右へずらす = 前へ
    const next = cur + dir;
    if (m === "track") {
      el.style.transition = "";                   // CSS の transition（ease-out）に戻す
      if (commit) go(next); else el.style.transform = `translate3d(${-cur * w}px,0,0)`;
      return;
    }
    const ms = movers();
    if (!commit) {
      for (const m of ms) {
        const a = m.animate?.([{ transform: `translate3d(${dx}px,0,0)`, opacity: m.style.opacity || "1" }, { transform: "translate3d(0,0,0)", opacity: 1 }], { duration: dur(200), easing: "ease-out" });
        m.style.transform = ""; m.style.opacity = "";
        if (a) a.onfinish = () => { m.style.willChange = ""; }; else m.style.willChange = "";
      }
      return;
    }
    // 出て行く → 中身を替える → 反対側から入る（入る側は上の layout effect）
    const outs = ms.map((m) => m.animate?.([{ transform: `translate3d(${dx}px,0,0)`, opacity: m.style.opacity || "1" }, { transform: `translate3d(${-dir * w * 0.5}px,0,0)`, opacity: 0 }], { duration: dur(140), easing: "ease-out", fill: "forwards" }));
    const swap = () => {
      enterFrom.current = dir;
      for (const m of ms) { m.style.transform = "translate3d(0,0,0)"; m.style.opacity = "0"; }
      outs.forEach((o) => o?.cancel());
      go(next);
      window.setTimeout(() => { if (enterFrom.current != null) { enterFrom.current = null; for (const m of ms) { m.style.transform = ""; m.style.opacity = ""; m.style.willChange = ""; } } }, 400);
    };
    const first = outs[0];
    if (first) first.onfinish = swap; else swap();
  }, []);   // eslint-disable-line react-hooks/exhaustive-deps

  const onPointerDown = useCallback((e: PointerEvent<T>) => {
    const { disabled: off, count: n } = latest.current;
    if (off || n < 2 || e.pointerType === "mouse" || !e.isPrimary) return;
    if (e.clientX < EDGE || e.clientX > window.innerWidth - EDGE) return;
    if ((e.target as HTMLElement).closest?.("input,textarea,select,[data-no-swipe]")) return;
    drag.current = { id: e.pointerId, x0: e.clientX, y0: e.clientY, lock: "none", dx: 0, samples: [{ t: e.timeStamp, x: e.clientX }] };
  }, []);

  const onPointerMove = useCallback((e: PointerEvent<T>) => {
    const d = drag.current;
    if (!d || d.id !== e.pointerId) return;
    const dx = e.clientX - d.x0, dy = e.clientY - d.y0;
    if (d.lock === "none") {
      if (Math.abs(dx) < LOCK && Math.abs(dy) < LOCK) return;
      if (Math.abs(dy) >= Math.abs(dx)) { drag.current = null; return; }      // 縦: ページのスクロールに任せる
      d.lock = "x";
      const el = ref.current;
      if (el) {
        for (const m of movers()) m.style.willChange = "transform";
        if (latest.current.mode === "track") el.style.transition = "none";
        try { el.setPointerCapture(e.pointerId); } catch { /* 取れなくても動く */ }
      }
    }
    const { index: cur, count: n } = latest.current;
    const hasNeighbor = dx > 0 ? cur > 0 : cur < n - 1;
    d.dx = hasNeighbor ? dx : dx * 0.25;           // 端では重くして止める
    d.samples.push({ t: e.timeStamp, x: e.clientX });
    if (d.samples.length > 6) d.samples.shift();
    paint(d.dx);
  }, []);   // eslint-disable-line react-hooks/exhaustive-deps

  const end = useCallback((e: PointerEvent<T>, cancelled: boolean) => {
    const d = drag.current;
    if (!d || d.id !== e.pointerId) return;
    drag.current = null;
    if (d.lock !== "x") return;
    suppress.current = true;                       // 続けて来る click（リンク・summary）は無かったことにする
    window.setTimeout(() => { suppress.current = false; }, 60);
    const { index: cur, count: n } = latest.current;
    const dir = d.dx > 0 ? -1 : 1;
    const target = cur + dir;
    const first = d.samples.find((s) => e.timeStamp - s.t <= 100) ?? d.samples[0];
    const last = d.samples[d.samples.length - 1];
    const v = last.t > first.t ? (last.x - first.x) / (last.t - first.t) : 0;
    const flick = Math.abs(v) >= FLICK && Math.sign(v) === Math.sign(d.dx);
    const far = Math.abs(d.dx) >= width() * COMMIT;
    finish(!cancelled && target >= 0 && target < n && (far || flick), d.dx);
  }, [finish]);   // eslint-disable-line react-hooks/exhaustive-deps

  const onPointerUp = useCallback((e: PointerEvent<T>) => end(e, false), [end]);
  const onPointerCancel = useCallback((e: PointerEvent<T>) => end(e, true), [end]);
  const onClickCapture = useCallback((e: MouseEvent<T>) => {
    if (suppress.current) { e.preventDefault(); e.stopPropagation(); }
  }, []);

  return { ref, handlers: { onPointerDown, onPointerMove, onPointerUp, onPointerCancel, onClickCapture, "data-swipe-surface": "" } };
}
