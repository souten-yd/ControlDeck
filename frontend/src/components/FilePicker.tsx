/** サーバー上のファイル/フォルダ選択ダイアログ。files API（許可ルート内）を使用。 */
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "../api/client";
import { IconChevronLeft, IconFile, IconFolder, IconX } from "./icons";

interface Entry {
  name: string;
  path: string;
  is_dir: boolean;
  hidden: boolean;
}

export function FilePicker({
  mode,
  title,
  initialPath,
  onSelect,
  onClose,
}: {
  mode: "file" | "dir";
  title?: string;
  initialPath?: string;
  onSelect: (path: string) => void;
  onClose: () => void;
}) {
  const [roots, setRoots] = useState<string[]>([]);
  const [path, setPath] = useState<string | null>(null); // null = ルート一覧
  const [entries, setEntries] = useState<Entry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<string[]>("/files/roots")
      .then((r) => {
        setRoots(r);
        // 初期パスがあればそこから、ルートが1つならそこから開始
        if (initialPath && r.some((root) => initialPath.startsWith(root))) {
          setPath(initialPath);
        } else if (r.length === 1) {
          setPath(r[0]);
        } else {
          setLoading(false);
        }
      })
      .catch((e) => {
        setError(e instanceof Error ? e.message : "ルートを取得できません");
        setLoading(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (path == null) return;
    setLoading(true);
    setError(null);
    api<{ path: string; entries: Entry[] }>(`/files/list?path=${encodeURIComponent(path)}`)
      .then((r) => {
        setPath(r.path);
        setEntries(r.entries);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "読み込みに失敗しました"))
      .finally(() => setLoading(false));
  }, [path]);

  const atRoot = path != null && roots.includes(path);
  const goUp = () => {
    if (path == null) return;
    if (atRoot) {
      if (roots.length > 1) setPath(null);
      return;
    }
    setPath(path.slice(0, path.lastIndexOf("/")) || "/");
  };

  return createPortal(
    <div className="fixed inset-0 z-[70] flex items-end justify-center bg-black/40 sm:items-center" onClick={onClose}>
      <div
        className="flex h-[80vh] w-full max-w-lg flex-col rounded-t-2xl bg-white sm:h-[70vh] sm:rounded-2xl dark:bg-zinc-900"
        onClick={(e) => e.stopPropagation()}
      >
        {/* ヘッダー */}
        <div className="flex shrink-0 items-center gap-2 border-b border-zinc-200 px-3 py-2.5 dark:border-zinc-800">
          <button
            onClick={goUp}
            disabled={path == null || (atRoot && roots.length <= 1)}
            aria-label="上のフォルダへ"
            className="rounded-lg p-1.5 text-zinc-500 hover:bg-zinc-100 disabled:opacity-30 dark:hover:bg-zinc-800"
          >
            <IconChevronLeft />
          </button>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold">{title ?? (mode === "dir" ? "フォルダを選択" : "ファイルを選択")}</p>
            <p className="truncate font-mono text-[11px] text-zinc-400">{path ?? "ルートを選択"}</p>
          </div>
          <button onClick={onClose} aria-label="閉じる" className="rounded-lg p-1.5 text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800">
            <IconX />
          </button>
        </div>

        {/* 一覧 */}
        <div className="min-h-0 flex-1 overflow-y-auto">
          {error && <p className="px-4 py-3 text-xs text-red-500">{error}</p>}
          {loading ? (
            <p className="px-4 py-3 text-xs text-zinc-400">読み込み中...</p>
          ) : path == null ? (
            <ul>
              {roots.map((r) => (
                <li key={r}>
                  <button
                    onClick={() => setPath(r)}
                    className="flex w-full items-center gap-2.5 px-4 py-2.5 text-left text-sm hover:bg-zinc-50 dark:hover:bg-zinc-800/60"
                  >
                    <FolderIcon />
                    <span className="truncate font-mono text-xs">{r}</span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <ul>
              {entries.map((e) => {
                const selectable = mode === "file" ? !e.is_dir : e.is_dir;
                return (
                  <li key={e.path}>
                    <button
                      onClick={() => {
                        if (e.is_dir) setPath(e.path);
                        else if (mode === "file") onSelect(e.path);
                      }}
                      disabled={!e.is_dir && mode === "dir"}
                      className={`flex w-full items-center gap-2.5 px-4 py-2.5 text-left text-sm hover:bg-zinc-50 disabled:cursor-default disabled:hover:bg-transparent dark:hover:bg-zinc-800/60 ${
                        e.hidden ? "opacity-45" : ""
                      } ${!selectable && !e.is_dir ? "text-zinc-400" : ""}`}
                    >
                      {e.is_dir ? <FolderIcon /> : <IconFile className="h-4 w-4 shrink-0 text-zinc-400" />}
                      <span className="truncate">{e.name}</span>
                      {e.is_dir && <span className="ml-auto text-zinc-300 dark:text-zinc-600">›</span>}
                    </button>
                  </li>
                );
              })}
              {entries.length === 0 && <p className="px-4 py-3 text-xs text-zinc-400">（空のフォルダ）</p>}
            </ul>
          )}
        </div>

        {/* フッター（フォルダ選択モード） */}
        {mode === "dir" && path != null && (
          <div className="shrink-0 border-t border-zinc-200 p-3 dark:border-zinc-800">
            <button
              onClick={() => onSelect(path)}
              className="w-full rounded-xl bg-accent-600 py-2.5 text-sm font-medium text-white hover:bg-accent-700"
            >
              このフォルダを選択
            </button>
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}

function FolderIcon() {
  return <IconFolder className="h-4 w-4 shrink-0 text-amber-500" />;
}
