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
import { marqueeCss } from "../lib/debugFlags";
import { memo, useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";

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

let cssSeq = 0;
/** ?marquee=css 用: @keyframes の <style> を足し、要素にインラインで当てて、その CSSAnimation を返す（止める・位置の持ち越しは WAAPI と同じ道）。 */
function cssAnimation(el: HTMLElement, keyframes: string, durationMs: number, once: boolean) {
  const name = `mqcss${++cssSeq}`;
  const style = document.createElement("style");
  style.textContent = keyframes.replace("NAME", name);
  document.head.appendChild(style);
  el.style.animation = `${name} ${durationMs}ms linear ${once ? "1" : "infinite"}`;
  const anim = el.getAnimations().find((x) => (x as { animationName?: string }).animationName === name) ?? el.animate([], { duration: durationMs });
  return { anim, dispose: () => { el.style.animation = ""; style.remove(); } };
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
  // 作り直しても位置（px）を保つための持ち越し。データの読み直しで dist・multi などが変わっても、流れが最初へ戻らない。
  const carry = useRef<{ x: number; hold: number }>({ x: 0, hold: 0 });
  const meta = useRef<{ pauseMs: number; move: number; dist: number; speed: number } | null>(null);

  useEffect(() => {
    const o = outer.current, i = inner.current;
    if (!o || !i) return;
    // 1px 以下の揺れ（小数の丸め）では測り直さない（アニメを付け直さない）
    const measure = () => setDist((prev) => { const next = Math.max(0, Math.ceil(i.scrollWidth - o.clientWidth)); return next !== 0 && prev !== 0 && Math.abs(next - prev) <= 1 ? prev : next; });
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
    let css: { anim: Animation; dispose: () => void } | null = null;
    if (run) {
      const move = (dist / speed) * 1000;
      const total = pauseMs * 2 + move;
      // transform（translate3d）だけで動かす。速さは px/秒で一定（距離 ÷ speed）
      if (marqueeCss()) {
        // 切り分け用（?marquee=css）: 同じ動きを CSS の @keyframes（transform だけ）で動かす
        const p1 = (pauseMs / total) * 100, p2 = ((pauseMs + move) / total) * 100;
        css = cssAnimation(i, `@keyframes NAME{0%,${p1}%{transform:translate3d(0,0,0)}${p2}%,100%{transform:translate3d(-${dist}px,0,0)}}`, total, multi);
        a = css.anim;
      } else a = i.animate([
        { transform: "translate3d(0,0,0)", offset: 0 },
        { transform: "translate3d(0,0,0)", offset: pauseMs / total },
        { transform: `translate3d(-${dist}px,0,0)`, offset: (pauseMs + move) / total },
        { transform: `translate3d(-${dist}px,0,0)`, offset: 1 },
      ], { duration: total, iterations: multi ? 1 : Infinity, easing: "linear" });
      const x = Math.min(carry.current.x, dist);
      if (x > 0) a.currentTime = pauseMs + (x / speed) * 1000;          // 前の位置から続ける
      meta.current = { pauseMs, move, dist, speed };
    } else if (multi) {
      if (marqueeCss()) { css = cssAnimation(i, "@keyframes NAME{from{opacity:1}to{opacity:1}}", holdMs, true); a = css.anim; }
      else a = i.animate([{ opacity: 1 }, { opacity: 1 }], { duration: holdMs });   // 見せる時間を数えるだけ
      if (carry.current.hold > 0) a.currentTime = Math.min(carry.current.hold, holdMs - 1);
      meta.current = null;
    }
    if (!a) return;
    if (multi) a.onfinish = () => done.current();
    if (pausedRef.current) a.pause();
    anim.current = a;
    return () => {
      // 作り直す前に、いまの位置を覚える
      const t = Number(a?.currentTime ?? 0), m = meta.current;
      if (m) { const rel = t % (m.pauseMs * 2 + m.move); carry.current = { x: Math.min(m.dist, Math.max(0, ((rel - m.pauseMs) / m.move) * m.dist)), hold: 0 }; }
      else carry.current = { x: 0, hold: t };
      a?.cancel(); css?.dispose(); anim.current = null;
    };
  }, [run, dist, speed, pauseMs, holdMs, multi]);

  useEffect(() => { if (paused) anim.current?.pause(); else anim.current?.play(); }, [paused]);

  return (
    <span ref={outer} data-marquee={run ? "run" : "fit"}
      className={`block overflow-hidden whitespace-nowrap ${reduced ? "text-ellipsis" : ""}`}>
      <span ref={inner} data-inner className="inline-block will-change-transform [backface-visibility:hidden]">{node}</span>
    </span>
  );
}

function MarqueeBase({ items, activeKey, onChange, className = "", speed = 50, pauseMs = 1000, holdMs = 4000, flipMs = 350 }: {
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
    enter.current?.animate?.([{ transform: "translate3d(0,100%,0)", opacity: 0 }, { transform: "translate3d(0,0,0)", opacity: 1 }],
      { duration: flipMs, easing: "ease-out" });
  }, [cur?.key, flipMs]);
  useEffect(() => {
    if (!leaving) return;
    const t = window.setTimeout(() => setLeaving(null), flipMs);
    return () => window.clearTimeout(t);
  }, [leaving, flipMs]);

  // 前の層を上へ抜く。ref を毎回新しい関数にすると再描画のたびに animate が付け直されて途中でやり直す（カクつきの原因の 1 つ）ので固定する
  const leaveRef = useCallback((el: HTMLDivElement | null) => {
    el?.animate?.([{ transform: "translate3d(0,0,0)", opacity: 1 }, { transform: "translate3d(0,-100%,0)", opacity: 0 }],
      { duration: flipMs, easing: "ease-out", fill: "forwards" });
  }, [flipMs]);
  const touchToggle = useRef(false);
  if (!cur) return null;
  return (
    <div ref={root} className={`relative overflow-hidden [contain:layout_paint_style] ${className}`} data-testid="marquee" data-count={items.length}
      onPointerEnter={(e) => { if (e.pointerType === "mouse") setPaused(true); }}
      onPointerLeave={(e) => { if (e.pointerType === "mouse") setPaused(false); }}
      onPointerDown={(e) => { if (e.pointerType !== "mouse") { touchToggle.current = !touchToggle.current; setPaused(touchToggle.current); } }}>
      <div ref={enter} data-enter>
        <Slide key={cur.key} node={cur.node} multi={items.length > 1} speed={speed} pauseMs={pauseMs}
          holdMs={holdMs} reduced={reduced} paused={paused} onDone={next} />
      </div>
      {leaving && (
        <div className="pointer-events-none absolute inset-0 whitespace-nowrap will-change-transform" ref={leaveRef}>
          <span className="inline-block" style={{ transform: `translate3d(${leaving.x}px,0,0)` }}>{leaving.node}</span>
        </div>
      )}
    </div>
  );
}

/** 親が描き直しても、items・props が同じなら描き直さない（流れの animation を付け直さない）。 */
export const Marquee = memo(MarqueeBase);
