/** 携帯幅で、横に引くとメニュー（ドロワー）が指に付いて出てくる。逆に引くと閉じる。
 *
 * 2026-11-20 利用者「左から右へフリックで出し、右から左で閉じたい。オンオフは設定で」。
 * v0.98 は「60px 以上・0.6 秒以内に離したら開く」だったので、Claude のように画面の真ん中から引いても
 * 出ず、左端から引くと iPhone の「戻る」、右端からは「進む」になった（利用者「思ったのと違う」）。そこで:
 * - 画面のどこから引いても、指に付いてメニューが出てくる。離した位置と速さで開くか戻るかを決める
 * - 左右の端で始めた指は、iPhone の「戻る」「進む」にさせない（touchstart を止める）。
 *   その指で縦に動かしたときは、こちらで縦にスクロールする。端のボタンなどはそのまま押せる
 * - 縦に動かし始めた指はスクロールのまま（横の動きが縦の 1.2 倍を超えたときだけ引く）
 * - 横にスクロールできる所（タブの並び・表・コード）、入力欄、ターミナル、キャンバス、
 *   data-no-nav-swipe を付けた所で始めた指は使わない
 * 開いているときは暗い所で始めた指を見る。ドロワー自身の引き戻しは NavDrawer が持つ。
 * オンオフは 設定 → 操作性（stores/uxPrefs の swipeNav）。切ると端の「戻る」「進む」も元に戻る。
 *
 * 2026-11-21 調査: ホーム画面の Web アプリで端の「戻る」を止める標準の手段（manifest・CSS）は無い。
 * 端の touchstart を止めるのが知られている唯一の回避策で、慣性スクロールが続いている間に端から引くと
 * iOS 側が先に取って戻ってしまう（W3C manifest の issue、2025-05 の報告。WebKit 側の制限）。
 * overscroll-behavior-x は Safari では効かない（WebKit Bugzilla）。 */
import { useEffect, useRef } from "react";

const EDGE_PX = 20;
const LOCK_PX = 10;
const SETTLE_MS = 240;

function startsInBlockedArea(target: EventTarget | null): boolean {
  let el = target instanceof Element ? target : null;
  if (el?.closest("[data-no-nav-swipe], input, textarea, select, [contenteditable='true'], .xterm, canvas, iframe, video")) return true;
  while (el && el !== document.body) {
    if (el instanceof HTMLElement && el.scrollWidth > el.clientWidth + 1) {
      const overflow = getComputedStyle(el).overflowX;
      if (overflow === "auto" || overflow === "scroll") return true;
    }
    el = el.parentElement;
  }
  return false;
}

function isInteractive(target: EventTarget | null): boolean {
  return target instanceof Element && target.closest(
    "a, button, input, select, textarea, label, summary, [role='button'], [role='link'], [role='tab'], [role='switch'], [role='checkbox'], [role='menuitem']",
  ) !== null;
}

/** 端で始めた指を縦に動かしたときに動かす、縦にスクロールできる一番近い入れ物 */
function verticalScroller(target: EventTarget | null): HTMLElement | null {
  let el = target instanceof Element ? target : null;
  while (el && el !== document.body) {
    if (el instanceof HTMLElement && el.scrollHeight > el.clientHeight + 1) {
      const overflow = getComputedStyle(el).overflowY;
      if (overflow === "auto" || overflow === "scroll") return el;
    }
    el = el.parentElement;
  }
  return document.scrollingElement as HTMLElement | null;
}

const panelEl = () => document.querySelector<HTMLElement>("[data-nav-drawer] [role='dialog']");
const backdropEl = () => document.querySelector<HTMLElement>("[data-nav-backdrop]");

interface Drag {
  kind: "open" | "close";
  mode: "pending" | "drag" | "scroll" | "none";
  edge: boolean;
  x0: number; y0: number;
  lastX: number; lastY: number; lastT: number;
  vx: number;
  target: EventTarget | null;
  scroller: HTMLElement | null;
}

export function useNavSwipe({ enabled, open, onOpen, onClose, onPeek }: {
  enabled: boolean; open: boolean; onOpen: () => void; onClose: () => void;
  /** 閉じた状態から引き始めたら true（ドロワーを置く）、開かずに戻したら false */
  onPeek: (peek: boolean) => void;
}) {
  const state = useRef({ open, onOpen, onClose, onPeek });
  state.current = { open, onOpen, onClose, onPeek };

  useEffect(() => {
    if (!enabled) return;
    const mobile = window.matchMedia("(max-width: 767px)");
    let drag: Drag | null = null;
    let settleTimer = 0;
    let settleDone: (() => void) | null = null;
    /** 前の滑りの後始末をすぐに済ませる（滑っている途中で次の指が来たとき） */
    const flushSettle = () => {
      window.clearTimeout(settleTimer);
      const done = settleDone;
      settleDone = null;
      done?.();
    };

    /** 指の位置に合わせてドロワーを置く。dx はドロワーが開いている量（0..幅）で表す */
    const place = (shownPx: number) => {
      const panel = panelEl();
      if (!panel) return;
      const width = panel.offsetWidth || 1;
      const shown = Math.max(0, Math.min(width, shownPx));
      panel.style.transition = "none";
      panel.style.transform = `translateX(${shown - width}px)`;
      const backdrop = backdropEl();
      if (backdrop) {
        backdrop.style.transition = "none";
        backdrop.style.opacity = String(shown / width);
      }
    };
    /** 離した後、開いた位置か閉じた位置へ滑らせ、終わったら手で付けた見た目を外す */
    const settle = (toOpen: boolean, done?: () => void) => {
      const panel = panelEl();
      const backdrop = backdropEl();
      for (const el of [panel, backdrop]) if (el) el.style.transition = "";
      if (panel) panel.style.transform = toOpen ? "translateX(0)" : "translateX(-100%)";
      if (backdrop) backdrop.style.opacity = toOpen ? "1" : "0";
      flushSettle();
      settleDone = () => {
        for (const el of [panelEl(), backdropEl()]) if (el) { el.style.transform = ""; el.style.opacity = ""; el.style.transition = ""; }
        done?.();
      };
      settleTimer = window.setTimeout(flushSettle, SETTLE_MS);
    };

    const onStart = (event: TouchEvent) => {
      drag = null;
      if (!mobile.matches || event.touches.length !== 1) return;
      const touch = event.touches[0];
      const target = event.target;
      const isOpen = state.current.open;
      if (isOpen) {
        if (!(target instanceof Element && target.closest("[data-nav-backdrop]"))) return;
      } else {
        if (target instanceof Element && target.closest("[data-nav-drawer]")) return;
        if (startsInBlockedArea(target)) return;
        // 選んでいる文字があるとき（範囲選択の操作）は使わない
        if (String(window.getSelection?.() ?? "").length > 0) return;
      }
      const edge = !isOpen && (touch.clientX < EDGE_PX || touch.clientX > window.innerWidth - EDGE_PX) && !isInteractive(target);
      // 端の指を止めて、iPhone の「戻る」「進む」にさせない
      if (edge && event.cancelable) event.preventDefault();
      drag = {
        kind: isOpen ? "close" : "open", mode: "pending", edge,
        x0: touch.clientX, y0: touch.clientY, lastX: touch.clientX, lastY: touch.clientY, lastT: event.timeStamp,
        vx: 0, target, scroller: null,
      };
    };

    const onMove = (event: TouchEvent) => {
      const d = drag;
      if (!d || d.mode === "none") return;
      const touch = event.touches[0];
      if (!touch) return;
      const dx = touch.clientX - d.x0;
      const dy = touch.clientY - d.y0;
      if (d.mode === "pending") {
        if (Math.abs(dx) + Math.abs(dy) < LOCK_PX) {
          if (d.edge && event.cancelable) event.preventDefault();
          return;
        }
        const towards = d.kind === "open" ? dx > 0 : dx < 0;
        if (towards && Math.abs(dx) > Math.abs(dy) * 1.2) {
          d.mode = "drag";
          flushSettle();
          if (d.kind === "open") state.current.onPeek(true);
        } else if (d.edge) {
          d.mode = "scroll";
          d.scroller = verticalScroller(d.target);
        } else {
          d.mode = "none";
          return;
        }
      }
      if (event.cancelable) event.preventDefault();
      const dt = Math.max(1, event.timeStamp - d.lastT);
      if (d.mode === "drag") {
        d.vx = 0.7 * ((touch.clientX - d.lastX) / dt) + 0.3 * d.vx;
        const width = panelEl()?.offsetWidth ?? 300;
        place(d.kind === "open" ? dx : width + dx);
      } else if (d.mode === "scroll" && d.scroller) {
        d.scroller.scrollTop -= touch.clientY - d.lastY;
      }
      d.lastX = touch.clientX;
      d.lastY = touch.clientY;
      d.lastT = event.timeStamp;
    };

    const onEnd = () => {
      const d = drag;
      drag = null;
      if (!d || d.mode !== "drag") return;
      const width = panelEl()?.offsetWidth ?? 300;
      const dx = d.lastX - d.x0;
      if (d.kind === "open") {
        const commit = d.vx > 0.35 || (d.vx > -0.2 && dx > width * 0.35);
        if (commit) {
          state.current.onOpen();
          settle(true, () => state.current.onPeek(false));
        } else {
          settle(false, () => state.current.onPeek(false));
        }
      } else {
        const commit = d.vx < -0.35 || (d.vx < 0.2 && -dx > width * 0.35);
        if (commit) {
          settle(false);
          state.current.onClose();
        } else {
          settle(true);
        }
      }
    };

    const onCancel = () => {
      const d = drag;
      drag = null;
      if (d?.mode === "drag") settle(d.kind === "close", d.kind === "open" ? () => state.current.onPeek(false) : undefined);
    };

    document.addEventListener("touchstart", onStart, { passive: false });
    document.addEventListener("touchmove", onMove, { passive: false });
    document.addEventListener("touchend", onEnd, { passive: true });
    document.addEventListener("touchcancel", onCancel, { passive: true });
    return () => {
      flushSettle();
      document.removeEventListener("touchstart", onStart);
      document.removeEventListener("touchmove", onMove);
      document.removeEventListener("touchend", onEnd);
      document.removeEventListener("touchcancel", onCancel);
    };
  }, [enabled]);
}
