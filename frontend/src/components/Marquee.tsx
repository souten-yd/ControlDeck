/** 複数の項目を 1 件ずつ右から左へ流し、流し切ったら縦にめくって次へ進む 1 行のテロップ部品。
 *
 * 2026-11-23 利用者「複数の情報があれば、その一つの情報を流したら、次フリップして別の情報を流す。情報のあるだけ流す。
 * 一つしかなければ、同じテロップをずっと流し続ける」「表示しきらなかった場合は右から左へ自動で流す（ニュースフィードも）」。
 * - 幅に収まる項目は流さず holdMs（既定 4 秒）見せてからめくる。1 件だけで収まるなら止めたまま。
 * - 収まらない項目は speed(px/秒) で流し、端で pauseMs 止まる。1 件だけなら同じ項目を流し続ける（めくらない）。
 * - 最後の次は最初へ戻る。めくる動きは flipMs（上へ抜けて次が下から入る）。
 * - 触れる（マウスは載せている間）・押す（タッチは押すたび 停止/再開）と一時停止。
 * - prefers-reduced-motion: 流さず・めくる動きもなく、省略表示（…）で holdMs ごとに切り替える。
 * - 幅の変化・項目の変化で測り直す。
 * 使い方: <Marquee items={[{ key: "a", node: <>本文</> }, ...]} activeKey={key} onChange={setKey} className="min-w-0 flex-1" />
 *   key は項目の同一性（項目が増減しても表示中の項目を保つ）。activeKey/onChange は、表示中の項目に合わせて
 *   札・「1/3」・閉じる等を外に出したいときだけ（外側で表示中の項目を知る）。固定したい札・× は Marquee の外に置く。
 *   項目の中身が表示中に変わるときは node を変える（key は同じ）。 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";

export interface MarqueeItem { key: string; node: ReactNode }

function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    if (!mq) return;
    setReduced(mq.matches);
    const on = () => setReduced(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return reduced;
}

/** 1 項目の層。横に流す・見せる時間を数えるのは Web Animations で行い（止める・再開が 1 か所で済む）、終わったら onDone。 */
function Slide({ node, multi, speed, pauseMs, holdMs, reduced, paused, onDone }: {
  node: ReactNode; multi: boolean; speed: number; pauseMs: number; holdMs: number;
  reduced: boolean; paused: boolean; onDone: () => void;
}) {
  const outer = useRef<HTMLSpanElement>(null);
  const inner = useRef<HTMLSpanElement>(null);
  const anim = useRef<Animation | null>(null);
  const pausedRef = useRef(paused);
  pausedRef.current = paused;
  const done = useRef(onDone);
  done.current = onDone;
  const [dist, setDist] = useState(0);

  useEffect(() => {
    const o = outer.current, i = inner.current;
    if (!o || !i) return;
    const measure = () => setDist(Math.max(0, Math.ceil(i.scrollWidth - o.clientWidth)));
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(o); ro.observe(i);
    return () => ro.disconnect();
  }, []);

  const run = dist > 2 && !reduced;
  useEffect(() => {
    const i = inner.current;
    if (!i || typeof i.animate !== "function") return;
    let a: Animation | null = null;
    if (run) {
      const move = (dist / speed) * 1000;
      const total = pauseMs * 2 + move;
      a = i.animate([
        { transform: "translateX(0)", offset: 0 },
        { transform: "translateX(0)", offset: pauseMs / total },
        { transform: `translateX(-${dist}px)`, offset: (pauseMs + move) / total },
        { transform: `translateX(-${dist}px)`, offset: 1 },
      ], { duration: total, iterations: multi ? 1 : Infinity, easing: "linear" });
    } else if (multi) {
      a = i.animate([{ opacity: 1 }, { opacity: 1 }], { duration: holdMs });   // 見せる時間を数えるだけ
    }
    if (!a) return;
    if (multi) a.onfinish = () => done.current();
    if (pausedRef.current) a.pause();
    anim.current = a;
    return () => { a?.cancel(); anim.current = null; };
  }, [run, dist, speed, pauseMs, holdMs, multi]);

  useEffect(() => { if (paused) anim.current?.pause(); else anim.current?.play(); }, [paused]);

  return (
    <span ref={outer} data-marquee={run ? "run" : "fit"}
      className={`block overflow-hidden whitespace-nowrap ${reduced ? "text-ellipsis" : ""}`}>
      <span ref={inner} data-inner className="inline-block will-change-transform">{node}</span>
    </span>
  );
}

export function Marquee({ items, activeKey, onChange, className = "", speed = 50, pauseMs = 1000, holdMs = 4000, flipMs = 350 }: {
  items: MarqueeItem[]; activeKey?: string; onChange?: (key: string) => void; className?: string;
  speed?: number; pauseMs?: number; holdMs?: number; flipMs?: number;
}) {
  const reduced = useReducedMotion();
  const [curKey, setCurKey] = useState<string | undefined>(activeKey);
  const found = items.findIndex((x) => x.key === curKey);
  const idx = found >= 0 ? found : 0;
  const cur = items[idx];
  const [leaving, setLeaving] = useState<{ node: ReactNode; x: number } | null>(null);
  const [paused, setPaused] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const enter = useRef<HTMLDivElement>(null);
  const flipped = useRef(false);

  useEffect(() => { if (cur && cur.key !== curKey) setCurKey(cur.key); }, [cur, curKey]);
  useEffect(() => { if (cur) onChange?.(cur.key); }, [cur?.key]);   // eslint-disable-line react-hooks/exhaustive-deps

  const next = useCallback(() => {
    if (items.length < 2) return;
    const inner = root.current?.querySelector<HTMLElement>("[data-enter] [data-inner]");
    const m = inner ? new DOMMatrix(getComputedStyle(inner).transform).m41 : 0;
    if (!reduced) {
      setLeaving({ node: items[idx].node, x: m });
      flipped.current = true;
    }
    setCurKey(items[(idx + 1) % items.length].key);
  }, [items, idx, reduced]);

  // めくる: 前の層は上へ抜け、次の層は下から入る。
  useLayoutEffect(() => {
    if (!flipped.current) return;
    flipped.current = false;
    enter.current?.animate?.([{ transform: "translateY(100%)", opacity: 0 }, { transform: "translateY(0)", opacity: 1 }],
      { duration: flipMs, easing: "ease-out" });
  }, [cur?.key, flipMs]);
  useEffect(() => {
    if (!leaving) return;
    const t = window.setTimeout(() => setLeaving(null), flipMs);
    return () => window.clearTimeout(t);
  }, [leaving, flipMs]);

  const touchToggle = useRef(false);
  if (!cur) return null;
  return (
    <div ref={root} className={`relative overflow-hidden ${className}`} data-testid="marquee" data-count={items.length}
      onPointerEnter={(e) => { if (e.pointerType === "mouse") setPaused(true); }}
      onPointerLeave={(e) => { if (e.pointerType === "mouse") setPaused(false); }}
      onPointerDown={(e) => { if (e.pointerType !== "mouse") { touchToggle.current = !touchToggle.current; setPaused(touchToggle.current); } }}>
      <div ref={enter} data-enter>
        <Slide key={cur.key} node={cur.node} multi={items.length > 1} speed={speed} pauseMs={pauseMs}
          holdMs={holdMs} reduced={reduced} paused={paused} onDone={next} />
      </div>
      {leaving && (
        <div className="pointer-events-none absolute inset-0 whitespace-nowrap"
          ref={(el) => { el?.animate?.([{ transform: "translateY(0)", opacity: 1 }, { transform: "translateY(-100%)", opacity: 0 }],
            { duration: flipMs, easing: "ease-in", fill: "forwards" }); }}>
          <span className="inline-block" style={{ transform: `translateX(${leaving.x}px)` }}>{leaving.node}</span>
        </div>
      )}
    </div>
  );
}
