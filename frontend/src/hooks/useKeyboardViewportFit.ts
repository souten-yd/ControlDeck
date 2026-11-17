import { useEffect } from "react";

/** iPhone でキーボードが出ている間、アプリの殻を「見えている範囲」に合わせる。
 *
 *  iOS Safari はキーボードを出しても layout viewport を縮めない。見えている範囲
 *  （visual viewport）だけが縮み、入力欄を見せるために文書が上へずれる。その結果、
 *  上の行が画面の外へ出て、入力欄と一緒に画面全体が動く。
 *  ここでは visual viewport の高さと位置を CSS 変数へ写し、`html.cd-keyboard-fit #root`
 *  がそれに従う。Android Chrome は `interactive-widget=resizes-content` で layout
 *  viewport 自体が縮むので差が出ず、何もしない。 */
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
      if (zoomed || covered < 120) {
        root.classList.remove("cd-keyboard-fit");
        root.style.removeProperty("--cd-app-height");
        root.style.removeProperty("--cd-app-top");
        return;
      }
      root.style.setProperty("--cd-app-height", `${Math.round(viewport.height)}px`);
      root.style.setProperty("--cd-app-top", `${Math.max(0, Math.round(viewport.offsetTop))}px`);
      root.classList.add("cd-keyboard-fit");
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(apply);
    };
    viewport.addEventListener("resize", schedule);
    viewport.addEventListener("scroll", schedule);
    apply();
    return () => {
      viewport.removeEventListener("resize", schedule);
      viewport.removeEventListener("scroll", schedule);
      if (frame) cancelAnimationFrame(frame);
      root.classList.remove("cd-keyboard-fit");
      root.style.removeProperty("--cd-app-height");
      root.style.removeProperty("--cd-app-top");
    };
  }, []);
}
