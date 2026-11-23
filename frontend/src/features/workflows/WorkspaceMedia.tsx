/** 作業場の画面の記録（撮影・操作テストの動画）の表示と削除（ROADMAP U28・U34）。
 *  - MessageMedia: チャットの完了カードの下。この回に撮った画像（縮小・押すと大きく）と動画。消された記録は「消しました」。
 *  - WorkspaceMediaSection: 作業場の結果のカードの「画面の記録」。1 件ずつ・まとめて消せる。
 *  中身は /opencode/workspaces/{name}/media/file?path=（持ち主だけ・symlink を辿らない配信）から読む。 */
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { ConfirmDialog } from "../../components/ui";
import { formatBytes } from "../../lib/format";
import { useToasts } from "../../stores";
import type { ChatMedia } from "./chatState";

export type MediaItem = { path: string; kind: "image" | "video"; size: number; mtime?: number };
export type MediaList = { items: MediaItem[]; count: number; total_size: number };

export const mediaFileUrl = (workspace: string, path: string) =>
  `/api/v1/opencode/workspaces/${encodeURIComponent(workspace)}/media/file?path=${encodeURIComponent(path)}`;
const baseName = (path: string) => path.split("/").pop() ?? path;
const BTN = "min-h-11 shrink-0 rounded-lg border border-zinc-300 px-3 text-sm text-zinc-700 hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800";

function Lightbox({ workspace, item, onClose }: { workspace: string; item: MediaItem; onClose: () => void }) {
  return (
    <div role="dialog" aria-label={baseName(item.path)} data-media-viewer
      className="fixed inset-0 z-50 flex flex-col items-center justify-center gap-2 bg-black/80 p-3" onClick={onClose}>
      <div className="flex max-h-full min-w-0 max-w-full items-center justify-center" onClick={(e) => e.stopPropagation()}>
        {item.kind === "video"
          ? <video src={mediaFileUrl(workspace, item.path)} controls autoPlay preload="metadata" className="max-h-[80vh] max-w-full rounded-lg" />
          : <img src={mediaFileUrl(workspace, item.path)} alt={baseName(item.path)} className="max-h-[80vh] max-w-full rounded-lg object-contain" />}
      </div>
      <p className="max-w-full truncate text-xs text-white/80">{item.path}</p>
      <button type="button" onClick={onClose} className="min-h-11 rounded-lg bg-white/90 px-4 text-sm font-medium text-zinc-900">閉じる</button>
    </div>
  );
}

/** チャットの完了カードの下: この回に撮った画像と動画。 */
export function MessageMedia({ media }: { media?: ChatMedia }) {
  const [gone, setGone] = useState<Set<string>>(new Set());
  const [big, setBig] = useState<MediaItem | null>(null);
  const markGone = (path: string) => setGone((prev) => new Set(prev).add(path));
  // 動画は再生できない理由が消された以外にもある（形式・途中で切れた）ので、404 かどうかを先頭 1 バイトの取得で確かめる
  useEffect(() => {
    for (const item of media?.items ?? []) {
      if (item.kind !== "video") continue;
      fetch(mediaFileUrl(media!.workspace, item.path), { credentials: "same-origin", headers: { Range: "bytes=0-0" } })
        .then((res) => { if (res.status === 404) markGone(item.path); })
        .catch(() => undefined);
    }
  }, [media]);
  if (!media?.items?.length) return null;
  return (
    <section data-chat-media aria-label="この回の画面の記録" className="min-w-0 rounded-xl border border-zinc-200 bg-white px-3 py-2 text-xs dark:border-zinc-700 dark:bg-zinc-900">
      <p className="font-semibold text-zinc-700 dark:text-zinc-200">画面の記録（{media.items.length} 件）</p>
      <ul className="mt-1.5 flex min-w-0 flex-wrap gap-2">
        {media.items.map((item) => (
          <li key={item.path} data-media-item={item.kind} className="min-w-0 max-w-full">
            {gone.has(item.path) ? (
              <p data-media-gone className="rounded-lg border border-dashed border-zinc-300 px-2 py-3 text-zinc-500 dark:border-zinc-700">消しました（{baseName(item.path)}）</p>
            ) : item.kind === "video" ? (
              <video src={mediaFileUrl(media.workspace, item.path)} controls preload="metadata" aria-label={baseName(item.path)}
                className="max-h-48 max-w-full rounded-lg bg-black" />
            ) : (
              <button type="button" onClick={() => setBig(item)} aria-label={`${baseName(item.path)} を大きく見る`} className="block">
                <img src={mediaFileUrl(media.workspace, item.path)} alt={baseName(item.path)} loading="lazy"
                  onError={() => markGone(item.path)} className="h-24 w-auto max-w-full rounded-lg border border-zinc-200 object-cover dark:border-zinc-700" />
              </button>
            )}
          </li>
        ))}
      </ul>
      {big && <Lightbox workspace={media.workspace} item={big} onClose={() => setBig(null)} />}
    </section>
  );
}

/** 作業場の結果のカード: 画面の記録の一覧・1 件の削除・この作業場の記録をすべて消す。 */
export function WorkspaceMediaSection({ name, ago }: { name: string; ago: (seconds: number) => string }) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const base = `/opencode/workspaces/${encodeURIComponent(name)}/media`;
  const [big, setBig] = useState<MediaItem | null>(null);
  const [confirming, setConfirming] = useState(false);
  const { data } = useQuery({ queryKey: ["workspace-media", name], queryFn: () => api<MediaList>(base), staleTime: 15_000 });
  const refresh = () => { void qc.invalidateQueries({ queryKey: ["workspace-media", name] }); void qc.invalidateQueries({ queryKey: ["workspaces-media"] }); };
  const removeOne = useMutation({
    mutationFn: (path: string) => api(`${base}/file?path=${encodeURIComponent(path)}`, { method: "DELETE" }),
    onSuccess: () => { refresh(); show("消しました"); },
    onError: (e) => { refresh(); show(e instanceof Error ? e.message : "消せませんでした", "error"); },
  });
  const removeAll = useMutation({
    mutationFn: () => api<{ count: number; size: number }>(base, { method: "DELETE" }),
    onSuccess: (done) => { setConfirming(false); refresh(); show(`${done.count} 件（${formatBytes(done.size)}）を消しました`); },
    onError: (e) => show(e instanceof Error ? e.message : "消せませんでした", "error"),
  });
  if (!data || data.count === 0) return null;
  return (
    <div data-testid="workspace-media" className="min-w-0">
      <p className="font-medium text-zinc-700 dark:text-zinc-200">画面の記録（{data.count} 件・{formatBytes(data.total_size)}）</p>
      <ul className="mt-1 max-h-64 space-y-1 overflow-y-auto overflow-x-hidden">
        {data.items.map((item) => (
          <li key={item.path} data-media-row className="flex min-w-0 items-center gap-2">
            <button type="button" onClick={() => setBig(item)} aria-label={`${baseName(item.path)} を大きく見る`}
              className="flex h-12 w-16 shrink-0 items-center justify-center overflow-hidden rounded border border-zinc-200 bg-zinc-100 dark:border-zinc-700 dark:bg-zinc-800">
              {item.kind === "image"
                ? <img src={mediaFileUrl(name, item.path)} alt="" loading="lazy" className="h-full w-full object-cover" />
                : <span aria-hidden className="text-lg">▶</span>}
            </button>
            <span className="min-w-0 flex-1">
              <span className="block truncate font-mono text-[11px]" title={item.path}>{item.kind === "video" ? "動画 " : ""}{baseName(item.path)}</span>
              <span className="num block text-[11px] text-zinc-500">{formatBytes(item.size)}{item.mtime ? `・${ago(item.mtime)}` : ""}</span>
            </span>
            <button type="button" onClick={() => removeOne.mutate(item.path)} disabled={removeOne.isPending} className={BTN}>消す</button>
          </li>
        ))}
      </ul>
      {data.count > data.items.length && <p className="mt-1 text-[11px] text-zinc-500">新しい {data.items.length} 件を表示しています。</p>}
      <button type="button" onClick={() => setConfirming(true)} className={`mt-1 ${BTN}`}>この作業場の記録をすべて消す</button>
      {big && <Lightbox workspace={name} item={big} onClose={() => setBig(null)} />}
      {confirming && (
        <ConfirmDialog title="画面の記録を消す" message={`この作業場の画面の記録 ${data.count} 件（${formatBytes(data.total_size)}）をすべて消します。元には戻せません。`}
          confirmLabel="すべて消す" busy={removeAll.isPending} onConfirm={() => removeAll.mutate()} onClose={() => setConfirming(false)} />
      )}
    </div>
  );
}
