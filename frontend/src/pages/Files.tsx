import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { useAuth, useToasts } from "../stores";
import { formatBytes } from "../lib/format";
import { BottomSheet, ConfirmDialog, DropdownMenu, Skeleton } from "../components/ui";
import { IconDots, IconFile, IconUpload } from "../components/icons";

const TextEditor = lazy(() => import("../features/files/TextEditor"));

interface Entry {
  name: string;
  path: string;
  is_dir: boolean;
  is_symlink: boolean;
  size: number;
  mtime: number;
  hidden: boolean;
}

const TEXT_EXT = /\.(txt|md|json|ya?ml|toml|ini|cfg|conf|py|sh|js|ts|tsx|jsx|css|html|xml|csv|log|env|service|sql|rs|go|c|h|cpp)$/i;
const IMAGE_EXT = /\.(png|jpe?g|gif|webp|svg|ico|bmp)$/i;

export default function FilesPage() {
  const can = useAuth((s) => s.can);
  const show = useToasts((s) => s.show);
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const [showHidden, setShowHidden] = useState(false);
  const [detail, setDetail] = useState<Entry | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState<Entry | null>(null);
  const [deleting, setDeleting] = useState<Entry | null>(null);
  const [dialog, setDialog] = useState<
    | { kind: "mkdir" }
    | { kind: "rename"; entry: Entry }
    | { kind: "copy" | "move"; entry: Entry }
    | null
  >(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const { data: roots } = useQuery({
    queryKey: ["file-roots"],
    queryFn: () => api<string[]>("/files/roots"),
    staleTime: Infinity,
    retry: false,
  });

  const path = params.get("path") || roots?.[0] || "";
  const setPath = (p: string) => setParams({ path: p });

  const { data: listing, isLoading, error } = useQuery({
    queryKey: ["files", path],
    queryFn: () => api<{ path: string; entries: Entry[] }>(`/files/list?path=${encodeURIComponent(path)}`),
    enabled: path !== "",
  });

  useEffect(() => {
    if (params.get("upload") === "1") {
      fileInputRef.current?.click();
      setParams({ path });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const entries = useMemo(
    () => (listing?.entries ?? []).filter((e) => showHidden || !e.hidden),
    [listing, showHidden],
  );

  const refresh = () => qc.invalidateQueries({ queryKey: ["files", path] });

  const upload = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    for (const file of Array.from(files)) {
      try {
        await apiUpload(path, file);
        show(`${file.name} をアップロードしました`);
      } catch (e) {
        if (e instanceof Error && e.message.includes("既に存在")) {
          if (confirm(`${file.name} は既に存在します。上書きしますか？`)) {
            try {
              await apiUpload(path, file, true);
              show(`${file.name} を上書きしました`);
            } catch (e2) {
              show(e2 instanceof Error ? e2.message : "アップロード失敗", "error");
            }
          }
        } else {
          show(e instanceof Error ? e.message : "アップロード失敗", "error");
        }
      }
    }
    refresh();
  };

  const openEntry = (e: Entry) => {
    if (e.is_dir) return setPath(e.path);
    if (IMAGE_EXT.test(e.name)) return setPreviewing(e);
    if (TEXT_EXT.test(e.name) || e.size < 256 * 1024) return setEditing(e.path);
    setDetail(e);
  };

  const crumbs = useMemo(() => {
    if (!path) return [];
    const root = roots?.find((r) => path.startsWith(r));
    if (!root) return [{ label: path, path }];
    const rest = path.slice(root.length).split("/").filter(Boolean);
    const list = [{ label: root.split("/").filter(Boolean).pop() || root, path: root }];
    let acc = root;
    for (const part of rest) {
      acc = `${acc}/${part}`.replace("//", "/");
      list.push({ label: part, path: acc });
    }
    return list;
  }, [path, roots]);

  if (roots && roots.length === 0) {
    return (
      <div className="grid h-full place-items-center p-8 text-center text-sm text-zinc-400">
        <div>
          <p>ファイルアクセスが設定されていません。</p>
          <p className="mt-1">config.yaml の files.allowed_roots に許可ディレクトリを追加してください。</p>
        </div>
      </div>
    );
  }

  return (
    <div
      className="mx-auto flex h-full max-w-5xl flex-col p-4 md:p-6"
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault();
        if (can("files.edit")) upload(e.dataTransfer.files);
      }}
    >
      {/* パンくず + 操作 */}
      <div className="mb-3 flex items-center gap-2">
        <nav aria-label="パス" className="min-w-0 flex-1 overflow-x-auto whitespace-nowrap text-sm">
          {roots && roots.length > 1 && (
            <select
              value={crumbs[0]?.path ?? ""}
              onChange={(e) => setPath(e.target.value)}
              className="mr-2 rounded-lg border border-zinc-300 bg-white px-2 py-1 text-xs dark:border-zinc-700 dark:bg-zinc-900"
              aria-label="ルートを選択"
            >
              {roots.map((r) => (
                <option key={r} value={r}>{r}</option>
              ))}
            </select>
          )}
          {crumbs.map((c, i) => (
            <span key={c.path}>
              {i > 0 && <span className="mx-1 text-zinc-300 dark:text-zinc-700">/</span>}
              <button
                onClick={() => setPath(c.path)}
                className={`rounded px-1 py-0.5 hover:bg-zinc-100 dark:hover:bg-zinc-800 ${
                  i === crumbs.length - 1 ? "font-semibold" : "text-zinc-500"
                }`}
              >
                {c.label}
              </button>
            </span>
          ))}
        </nav>
        <DropdownMenu
          ariaLabel="ファイル操作メニュー"
          trigger={<IconDots />}
          items={[
            { label: showHidden ? "隠しファイルを隠す" : "隠しファイルを表示", onSelect: () => setShowHidden(!showHidden) },
            ...(can("files.edit")
              ? [{ label: "新しいフォルダ", onSelect: () => setDialog({ kind: "mkdir" }) }]
              : []),
            { label: "再読み込み", onSelect: refresh },
          ]}
        />
      </div>

      {/* 一覧 */}
      <div className="min-h-0 flex-1 overflow-y-auto rounded-2xl border border-zinc-200 dark:border-zinc-800">
        {isLoading ? (
          <div className="space-y-2 p-4">
            {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-10" />)}
          </div>
        ) : error ? (
          <p className="p-6 text-center text-sm text-red-500">
            {error instanceof Error ? error.message : "読み込みに失敗しました"}
          </p>
        ) : entries.length === 0 ? (
          <p className="p-8 text-center text-sm text-zinc-400">空のフォルダです</p>
        ) : (
          <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
            {entries.map((e) => (
              <li
                key={e.path}
                className="flex cursor-pointer items-center gap-3 bg-white px-3 py-2.5 hover:bg-zinc-50 dark:bg-zinc-900 dark:hover:bg-zinc-800/60"
                onClick={() => openEntry(e)}
              >
                <span className="text-lg" aria-hidden>
                  {e.is_dir ? "📁" : <IconFile className="text-zinc-400" />}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm">{e.name}{e.is_symlink && " ↗"}</p>
                  <p className="num text-xs text-zinc-400">
                    {e.is_dir ? "フォルダ" : formatBytes(e.size)} ·{" "}
                    {new Date(e.mtime * 1000).toLocaleString("ja-JP", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                  </p>
                </div>
                <DropdownMenu
                  ariaLabel={`${e.name} のメニュー`}
                  trigger={<IconDots />}
                  items={[
                    ...(!e.is_dir
                      ? [{ label: "ダウンロード", onSelect: () => window.open(`/api/v1/files/download?path=${encodeURIComponent(e.path)}`, "_blank") }]
                      : []),
                    ...(!e.is_dir && can("files.edit")
                      ? [{ label: "編集", onSelect: () => setEditing(e.path) }]
                      : []),
                    ...(can("files.edit")
                      ? [
                          { label: "名前を変更", onSelect: () => setDialog({ kind: "rename", entry: e }) },
                          { label: "コピー", onSelect: () => setDialog({ kind: "copy", entry: e }) },
                          { label: "移動", onSelect: () => setDialog({ kind: "move", entry: e }) },
                        ]
                      : []),
                    { label: "情報", onSelect: () => setDetail(e) },
                    ...(can("files.delete")
                      ? [{ label: "削除", danger: true, onSelect: () => setDeleting(e) }]
                      : []),
                  ]}
                />
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* アップロード FAB */}
      {can("files.edit") && (
        <>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={(e) => {
              upload(e.target.files);
              e.target.value = "";
            }}
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            aria-label="ファイルをアップロード"
            className="fixed bottom-24 right-4 z-20 grid place-items-center rounded-2xl bg-accent-600 p-3.5 text-xl text-white shadow-lg hover:bg-accent-700 md:bottom-8"
          >
            <IconUpload />
          </button>
        </>
      )}

      {/* ダイアログ群 */}
      {dialog && (
        <PathDialog
          dialog={dialog}
          currentPath={path}
          onClose={() => setDialog(null)}
          onDone={() => {
            setDialog(null);
            refresh();
          }}
        />
      )}
      {deleting && (
        <ConfirmDialog
          title={`「${deleting.name}」を削除しますか？`}
          message={deleting.is_dir ? "フォルダ内のすべてのファイルが削除されます。この操作は取り消せません。" : "この操作は取り消せません。"}
          confirmLabel="削除する"
          onConfirm={async () => {
            try {
              await api(`/files?path=${encodeURIComponent(deleting.path)}`, { method: "DELETE" });
              show("削除しました");
              refresh();
            } catch (e) {
              show(e instanceof Error ? e.message : "削除に失敗しました", "error");
            }
            setDeleting(null);
          }}
          onClose={() => setDeleting(null)}
        />
      )}
      {detail && (
        <BottomSheet title={detail.name} onClose={() => setDetail(null)}>
          <dl className="space-y-2 text-sm">
            <InfoRow k="パス" v={detail.path} />
            <InfoRow k="種類" v={detail.is_dir ? "フォルダ" : "ファイル"} />
            {!detail.is_dir && <InfoRow k="サイズ" v={formatBytes(detail.size)} />}
            <InfoRow k="更新" v={new Date(detail.mtime * 1000).toLocaleString("ja-JP")} />
          </dl>
        </BottomSheet>
      )}
      {previewing && (
        <BottomSheet title={previewing.name} onClose={() => setPreviewing(null)} wide>
          <img
            src={`/api/v1/files/preview?path=${encodeURIComponent(previewing.path)}`}
            alt={previewing.name}
            className="mx-auto max-h-[65dvh] max-w-full rounded-lg object-contain"
          />
        </BottomSheet>
      )}
      {editing && (
        <Suspense
          fallback={
            <div className="fixed inset-0 z-50 grid place-items-center bg-black/40 text-sm text-white">
              エディターを読み込み中...
            </div>
          }
        >
          <TextEditor path={editing} onClose={() => setEditing(null)} readOnly={!can("files.edit")} />
        </Suspense>
      )}
    </div>
  );
}

async function apiUpload(directory: string, file: File, overwrite = false): Promise<void> {
  const form = new FormData();
  form.append("file", file);
  await api(`/files/upload?directory=${encodeURIComponent(directory)}&overwrite=${overwrite}`, {
    method: "POST",
    body: form,
  });
}

function InfoRow({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex gap-4">
      <dt className="w-16 shrink-0 text-zinc-400">{k}</dt>
      <dd className="num min-w-0 break-all">{v}</dd>
    </div>
  );
}

function PathDialog({
  dialog,
  currentPath,
  onClose,
  onDone,
}: {
  dialog: { kind: "mkdir" } | { kind: "rename"; entry: Entry } | { kind: "copy" | "move"; entry: Entry };
  currentPath: string;
  onClose: () => void;
  onDone: () => void;
}) {
  const show = useToasts((s) => s.show);
  const [value, setValue] = useState(
    dialog.kind === "mkdir" ? "" : dialog.kind === "rename" ? dialog.entry.name : currentPath,
  );
  const [busy, setBusy] = useState(false);
  const titles = { mkdir: "新しいフォルダ", rename: "名前を変更", copy: "コピー先フォルダ", move: "移動先フォルダ" };

  const run = async () => {
    setBusy(true);
    try {
      if (dialog.kind === "mkdir") {
        await api("/files/directory", { method: "POST", json: { path: `${currentPath}/${value}` } });
      } else if (dialog.kind === "rename") {
        await api("/files/rename", { method: "PATCH", json: { path: dialog.entry.path, new_name: value } });
      } else {
        await api(`/files/${dialog.kind}`, {
          method: "POST",
          json: { source: dialog.entry.path, destination_dir: value },
        });
      }
      show("完了しました");
      onDone();
    } catch (e) {
      show(e instanceof Error ? e.message : "操作に失敗しました", "error");
      setBusy(false);
    }
  };

  return (
    <BottomSheet title={titles[dialog.kind]} onClose={onClose}>
      <input
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && value.trim() && run()}
        autoFocus
        className="w-full rounded-xl border border-zinc-300 bg-white px-3.5 py-2.5 font-mono text-sm outline-none focus:border-accent-500 dark:border-zinc-700 dark:bg-zinc-900"
        placeholder={dialog.kind === "mkdir" ? "フォルダ名" : dialog.kind === "rename" ? "新しい名前" : "/path/to/dir"}
      />
      <div className="mt-4 flex justify-end gap-2">
        <button onClick={onClose} className="rounded-xl px-4 py-2 text-sm font-medium hover:bg-zinc-100 dark:hover:bg-zinc-800">
          キャンセル
        </button>
        <button
          onClick={run}
          disabled={busy || !value.trim()}
          className="rounded-xl bg-accent-600 px-4 py-2 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-40"
        >
          実行
        </button>
      </div>
    </BottomSheet>
  );
}
