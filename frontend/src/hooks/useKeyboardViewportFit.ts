import { useEffect } from "react";

/** iPhone でキーボードが出ている間、アプリの殻を「見えている範囲」に合わせる。
 *
 *  iOS Safari はキーボードを出しても layout viewport を縮めない。見えている範囲
 *  （visual viewport）だけが縮み、入力欄を見せるために文書が上へずれる。その結果、
 *  上の行が画面の外へ出て、入力欄と一緒に画面全体が動く。
 *  ここでは visual viewport の高さと位置を CSS 変数へ写し、`html.cd-keyboard-fit #root`
 *  がそれに従う。Android Chrome は `interactive-widget=resizes-content` で layout
 *  viewport 自体が縮むので差が出ず、何もしない。
 *
 *  画面全体の殻（AppLayout）で 1 回だけ呼ぶ。2026-11-21 まではAI アシスタントだけが呼んでいて、設定などの画面では
 *  殻が満寸のまま入力欄がキーボードの裏に隠れた（実機 iOS 18.7: innerHeight 932 のまま・visual viewport 517・殻 932。
 *  利用者「API キーの入力欄をタップするとキーボードで隠れて貼り付けられない」）。
 *  殻を縮めた後も入力欄が見える範囲の外なら、欄を含むスクロール領域を動かして中ほどへ出す。
 *  ターミナルは自前で visual viewport を扱う（XtermViewV2）ので、ターミナルに触れている間は何もしない。 */
export function useKeyboardViewportFit(): void {
  useEffect(() => {
    const viewport = window.visualViewport;
    if (!viewport) return;
    const root = document.documentElement;
    let frame = 0;
    const apply = () => {
      frame = 0;
      /* 拡大表示中は利用者が自分で動かしている。触らない。 */
      const zoomed = Math.abs(viewport.scale - 1) > 0.01;
      const covered = window.innerHeight - viewport.height;
      const inTerminal = document.activeElement instanceof Element && document.activeElement.closest(".xterm") !== null;
      if (zoomed || covered < 120 || inTerminal) {
        root.classList.remove("cd-keyboard-fit");
        root.style.removeProperty("--cd-app-height");
        root.style.removeProperty("--cd-app-top");
        return;
      }
      root.style.setProperty("--cd-app-height", `${Math.round(viewport.height)}px`);
      root.style.setProperty("--cd-app-top", `${Math.max(0, Math.round(viewport.offsetTop))}px`);
      root.classList.add("cd-keyboard-fit");
      revealFocusedField();
    };
    /* 殻の高さが反映されてから測り、隠れていれば欄を見せる。 */
    const revealFocusedField = () => {
      const field = document.activeElement;
      if (!(field instanceof HTMLElement) || !isEditable(field)) return;
      requestAnimationFrame(() => requestAnimationFrame(() => {
        if (document.activeElement !== field || !root.classList.contains("cd-keyboard-fit")) return;
        const top = Math.max(0, viewport.offsetTop);
        const rect = field.getBoundingClientRect();
        if (rect.top >= top + 8 && rect.bottom <= top + viewport.height - 8) return;
        field.scrollIntoView({ block: "center", inline: "nearest" });
      }));
    };
    /* キーボードが出たまま別の欄へ移ったとき（viewport の resize が来ない） */
    const onFocusIn = () => {
      if (root.classList.contains("cd-keyboard-fit")) revealFocusedField();
      else schedule();
    };
    function schedule() {
      if (!frame) frame = requestAnimationFrame(apply);
    }
    viewport.addEventListener("resize", schedule);
    viewport.addEventListener("scroll", schedule);
    document.addEventListener("focusin", onFocusIn);
    apply();
    return () => {
      viewport.removeEventListener("resize", schedule);
      viewport.removeEventListener("scroll", schedule);
      document.removeEventListener("focusin", onFocusIn);
      if (frame) cancelAnimationFrame(frame);
      root.classList.remove("cd-keyboard-fit");
      root.style.removeProperty("--cd-app-height");
      root.style.removeProperty("--cd-app-top");
    };
  }, []);
}

function isEditable(element: HTMLElement): boolean {
  if (element.isContentEditable) return true;
  if (element instanceof HTMLTextAreaElement || element instanceof HTMLSelectElement) return true;
  if (!(element instanceof HTMLInputElement)) return false;
  return !["button", "checkbox", "radio", "range", "color", "file", "submit", "reset", "image", "hidden"].includes(element.type);
}
