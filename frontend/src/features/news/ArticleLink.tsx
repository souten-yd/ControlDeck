import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import type { NewsItem } from "../../api/news";

const external = (url: string) => /^https?:\/\//.test(url);

/** ControlDeck の中のリーダーで読む記事か（release・event・取り出しに一度失敗した記事・RAG にだけ残る記事・url の無い記事は今までどおり外へ）。 */
export const readsInside = (item: Pick<NewsItem, "kind" | "url" | "archived" | "id" | "reader_failed">) =>
  Boolean(item.url) && !item.archived && item.id > 0 && item.kind !== "release" && item.kind !== "event" && !item.reader_failed;
export const readerPath = (id: number) => `/news/read/${id}`;

/** 記事へのリンク。リーダーで読む記事は /news/read/<id>（外へ飛ばない）、それ以外は元のページ（別タブ）。開いたら onOpen（好みの記録）。 */
export function ArticleLink({ item, onOpen, className, label, children }: {
  item: NewsItem; onOpen?: () => void; className?: string; label?: string; children: ReactNode;
}) {
  if (!item.url) return <>{children}</>;
  const record = item.archived ? undefined : onOpen;
  if (readsInside(item)) return <Link to={readerPath(item.id)} className={className} aria-label={label} onClick={record} onAuxClick={record}>{children}</Link>;
  return <a href={item.url} className={className} aria-label={label} {...(external(item.url) ? { target: "_blank", rel: "noopener noreferrer" } : {})} onClick={record} onAuxClick={record}>{children}</a>;
}
