/** チームに添える資料（/data1tb・/data2tb のファイル・フォルダ）。
 *
 * 中身は送らず、パスの一覧だけをチームリーダーへ渡す（読むかどうかはチームリーダーが決める）。
 * 選ぶのは既存の FilePicker（作業フォルダと同じ許可ルート）で、選んだパスは札で見せて外せる。 */
import { useState } from "react";
import { FilePicker } from "../../components/FilePicker";
import { IconPaperclip, IconX } from "../../components/icons";
import { WORKDIR_SOURCE } from "./NewConsoleSheet";

export function MaterialAttach({
  paths, onChange, label = "資料を添える",
}: {
  paths: string[];
  onChange: (paths: string[]) => void;
  label?: string;
}) {
  const [picking, setPicking] = useState<"file" | "dir" | null>(null);
  const add = (path: string) => {
    if (!paths.includes(path)) onChange([...paths, path]);
    setPicking(null);
  };
  return (
    <div data-team-materials className="mt-1.5 min-w-0">
      <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs text-zinc-600 dark:text-zinc-300">
        <IconPaperclip className="h-3.5 w-3.5 shrink-0" />
        <span className="shrink-0 font-medium">{label}</span>
        <button type="button" data-material-add="file" onClick={() => setPicking("file")} className="min-h-9 font-semibold text-accent-600 underline dark:text-accent-400">
          ファイル
        </button>
        <button type="button" data-material-add="dir" onClick={() => setPicking("dir")} className="min-h-9 font-semibold text-accent-600 underline dark:text-accent-400">
          フォルダ
        </button>
        <span className="text-[11px] text-zinc-500 dark:text-zinc-400">パスだけを渡します</span>
      </div>
      {paths.length > 0 && (
        <ul data-team-material-list className="mt-1 flex min-w-0 flex-wrap gap-1">
          {paths.map((path) => (
            <li key={path} data-team-material={path} className="flex min-w-0 max-w-full items-center gap-1 rounded-full bg-zinc-100 px-2 py-0.5 text-[11px] dark:bg-zinc-800">
              <span className="min-w-0 truncate font-mono" title={path}>{path}</span>
              <button
                type="button" aria-label={`${path} を外す`} onClick={() => onChange(paths.filter((p) => p !== path))}
                className="shrink-0 rounded-full p-0.5 text-zinc-500 hover:text-red-600 dark:hover:text-red-400"
              >
                <IconX className="h-3 w-3" />
              </button>
            </li>
          ))}
        </ul>
      )}
      {picking && (
        <FilePicker
          mode={picking} title={picking === "file" ? "添えるファイルを選ぶ" : "添えるフォルダを選ぶ"}
          source={WORKDIR_SOURCE} onSelect={add} onClose={() => setPicking(null)}
        />
      )}
    </div>
  );
}
