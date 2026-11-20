/** 携帯幅で、左から右へのフリックでメニュー（ドロワー）を開き、右から左のフリックで閉じる。
 *
 * 2026-11-20 利用者「左上のメニューボタンを、左から右へフリックで出し、右から左で閉じたい。オンオフは設定で」。
 * オンオフは 設定 → 操作性（stores/uxPrefs の swipeNav）。
 *
 * 誤って開かないための決まり:
 * - 横の動きが縦の 2 倍以上・60px 以上・0.6 秒以内のときだけ（縦のスクロールでは開かない）
 * - 横にスクロールできる所（タブの並び・表・コード）、入力欄、ターミナル、キャンバスで始めた指は使わない
 * - data-no-nav-swipe を付けた所（そこで横の操作を使う画面）では使わない
 * - 左端 16px は iPhone の「戻る」の操作と取り合うので使わない（Safari のとき）
 * 閉じる方はドロワーの外（暗い所）で始めた指だけ見る。ドロワー自身の引き戻しは NavDrawer が持つ。 */
import { useEffect, useRef } from "react";

const MIN_DX = 60;
const MAX_MS = 600;
const EDGE_PX = 16;

function startsInBlockedArea(target: EventTarget | null): boolean {
  let el = target instanceof Element ? target : null;
  while (el && el !== document.body) {
    if (el.closest("[data-no-nav-swipe], input, textarea, select, [contenteditable='true'], .xterm, canvas, iframe, video")) return true;
    if (el instanceof HTMLElement && el.scrollWidth > el.clientWidth + 1) {
      const overflow = getComputedStyle(el).overflowX;
      if (overflow === "auto" || overflow === "scroll") return true;
    }
    el = el.parentElement;
  }
  return false;
}

export function useNavSwipe({ enabled, open, onOpen, onClose }: {
  enabled: boolean; open: boolean; onOpen: () => void; onClose: () => void;
}) {
  const state = useRef({ open, onOpen, onClose });
  state.current = { open, onOpen, onClose };

  useEffect(() => {
    if (!enabled) return;
    const mobile = window.matchMedia("(max-width: 767px)");
    let start: { x: number; y: number; at: number; usable: boolean } | null = null;
    const onStart = (event: TouchEvent) => {
      if (!mobile.matches || event.touches.length !== 1) { start = null; return; }
      const touch = event.touches[0];
      const target = event.target;
      const inDrawer = target instanceof Element && target.closest("[data-nav-drawer]") !== null;
      const onBackdrop = target instanceof Element && target.closest("[data-nav-backdrop]") !== null;
      const usable = state.current.open
        ? onBackdrop
        : !inDrawer && touch.clientX > EDGE_PX && !startsInBlockedArea(target);
      start = { x: touch.clientX, y: touch.clientY, at: Date.now(), usable };
    };
    const onEnd = (event: TouchEvent) => {
      const begin = start;
      start = null;
      if (!begin?.usable) return;
      const touch = event.changedTouches[0];
      if (!touch) return;
      const dx = touch.clientX - begin.x;
      const dy = touch.clientY - begin.y;
      if (Date.now() - begin.at > MAX_MS || Math.abs(dx) < MIN_DX || Math.abs(dx) < Math.abs(dy) * 2) return;
      // 選んでいる文字があるとき（範囲選択の操作）は開かない
      if (!state.current.open && String(window.getSelection?.() ?? "").length > 0) return;
      if (dx > 0 && !state.current.open) state.current.onOpen();
      else if (dx < 0 && state.current.open) state.current.onClose();
    };
    const onCancel = () => { start = null; };
    document.addEventListener("touchstart", onStart, { passive: true });
    document.addEventListener("touchend", onEnd, { passive: true });
    document.addEventListener("touchcancel", onCancel, { passive: true });
    return () => {
      document.removeEventListener("touchstart", onStart);
      document.removeEventListener("touchend", onEnd);
      document.removeEventListener("touchcancel", onCancel);
    };
  }, [enabled]);
}
