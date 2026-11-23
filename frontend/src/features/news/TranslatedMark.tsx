/** 英語の抜粋を訳した概要の印。開くと元の抜粋が見られる（題は訳さない）。 */
import type { NewsItem } from "../../api/news";

export function TranslatedMark({ item }: { item: Pick<NewsItem, "translated" | "summary_original"> }) {
  if (!item.translated) return null;
  return (
    <details className="nw-orig" data-testid="translated-mark">
      <summary aria-label="訳（元の抜粋を見る）">訳</summary>
      {item.summary_original && <p lang="en">{item.summary_original}</p>}
    </details>
  );
}
