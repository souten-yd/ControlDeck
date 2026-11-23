import type { CSSProperties } from "react";
import type { NewsDisplay } from "../../api/news";

/** 表示の設定を、ニュースの根の要素の属性と CSS の変数にする（見た目は news.css が変数で切り替える）。 */
export function newsRootProps(display: NewsDisplay, paused = false) {
  const style: Record<string, string> = {};
  if (display.accent) {
    style["--nw-accent"] = display.accent;
    style["--nw-accent-fg"] = readableOn(display.accent);
  }
  return {
    className: "nw",
    "data-design": display.design, "data-font": display.font_size, "data-density": display.density, "data-images": String(display.images),
    "data-cols": display.columns, "data-reduce": String(display.reduce_motion), "data-paused": String(paused),
    style: style as CSSProperties,
  };
}

/** 背景色の上に載せる文字色（明るい背景には暗い字）。 */
export function readableOn(hex: string): string {
  const n = parseInt(hex.slice(1), 16);
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  return (0.299 * r + 0.587 * g + 0.114 * b) / 255 > 0.6 ? "#14110a" : "#ffffff";
}

export const chNo = (n: number) => `CH ${String(n).padStart(2, "0")}`;
