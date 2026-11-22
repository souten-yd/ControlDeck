// 作業場の結果のカードの「変更」: ファイルの一覧 → 押すと行ごとの差分（追加は緑・削除は赤・行番号・横スクロール）。
// 差分はホストが管理する export.git から取る（箱の作業ツリーでホストの git を動かさない）。
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";

export type WorkspaceDiffFile = { path: string; added: number; removed: number; binary: boolean };
export type WorkspaceDiffData = {
  base: string; head: string; files: WorkspaceDiffFile[]; limit: number;
  file: { path: string; binary: boolean; truncated: boolean; diff: string; size: number } | null;
  uncommitted: { files: { path: string; status: "untracked" | "modified" }[]; count: number } | null;
};

type Row = { kind: "hunk" | "add" | "del" | "ctx"; text: string; old?: number; next?: number };

/** unified diff を行に分ける。ファイルの見出し（diff --git・index・---・+++）は読みにくいので出さない。 */
export function parseDiff(text: string): Row[] {
  const rows: Row[] = [];
  let old = 0;
  let next = 0;
  let inHunk = false;
  for (const line of text.split("\n")) {
    const hunk = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line);
    if (hunk) { old = Number(hunk[1]); next = Number(hunk[2]); inHunk = true; rows.push({ kind: "hunk", text: line }); continue; }
    if (!inHunk) continue;
    if (line.startsWith("+")) rows.push({ kind: "add", text: line.slice(1), next: next++ });
    else if (line.startsWith("-")) rows.push({ kind: "del", text: line.slice(1), old: old++ });
    else if (line.startsWith("\\")) rows.push({ kind: "hunk", text: line });
    else if (line !== "" || rows.length) rows.push({ kind: "ctx", text: line.slice(1), old: old++, next: next++ });
  }
  while (rows.length && rows[rows.length - 1].kind === "ctx" && rows[rows.length - 1].text === "") rows.pop();
  return rows;
}

const TONE: Record<Row["kind"], string> = {
  add: "bg-emerald-50 text-emerald-900 dark:bg-emerald-950/50 dark:text-emerald-200",
  del: "bg-red-50 text-red-900 dark:bg-red-950/50 dark:text-red-200",
  hunk: "bg-sky-50 text-sky-800 dark:bg-sky-950/40 dark:text-sky-300",
  ctx: "text-zinc-700 dark:text-zinc-300",
};
const MARK: Record<Row["kind"], string> = { add: "+", del: "−", hunk: "", ctx: " " };

export function DiffLines({ diff }: { diff: string }) {
  const rows = parseDiff(diff);
  if (!rows.length) return <p className="px-2 py-1 text-zinc-500">表示する行がありません</p>;
  return (
    <div data-testid="workspace-diff-lines" className="overflow-x-auto rounded border border-zinc-200 font-mono text-[11px] leading-5 dark:border-zinc-800">
      <table className="min-w-full border-collapse">
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} data-diff={row.kind} className={TONE[row.kind]}>
              <td className="w-8 select-none px-1 text-right align-top tabular-nums text-zinc-400">{row.old ?? ""}</td>
              <td className="w-8 select-none px-1 text-right align-top tabular-nums text-zinc-400">{row.next ?? ""}</td>
              <td className="w-4 select-none px-1 align-top">{MARK[row.kind]}</td>
              <td className="whitespace-pre pr-3">{row.text}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function WorkspaceDiff({ name }: { name: string }) {
  const base = `/opencode/workspaces/${encodeURIComponent(name)}/diff`;
  const [path, setPath] = useState("");
  const { data, error, isFetching } = useQuery({
    queryKey: ["workspace-diff", name, path],
    queryFn: () => api<WorkspaceDiffData>(path ? `${base}?path=${encodeURIComponent(path)}` : base),
    staleTime: 15_000,
  });
  const file = data?.file;
  return (
    <div data-testid="workspace-diff" className="min-w-0 space-y-2">
      {error && <p className="text-red-700 dark:text-red-400">{error instanceof Error ? error.message : "差分を読めませんでした"}</p>}
      {!data && !error && <p className="text-zinc-500">読み込み中…</p>}
      {data && !path && (
        <>
          <ul data-testid="workspace-diff-files" className="space-y-0.5 font-mono text-[11px]">
            {data.files.map((f) => (
              <li key={f.path}>
                <button type="button" onClick={() => setPath(f.path)}
                  className="flex min-h-11 w-full min-w-0 items-center gap-2 rounded px-1 text-left hover:bg-zinc-100 dark:hover:bg-zinc-800">
                  <span className="min-w-0 flex-1 truncate">{f.path}</span>
                  <span className="shrink-0 text-zinc-500">
                    {f.binary ? "バイナリ" : <><span className="text-emerald-700 dark:text-emerald-400">+{f.added}</span> <span className="text-red-700 dark:text-red-400">−{f.removed}</span></>}
                  </span>
                </button>
              </li>
            ))}
            {data.files.length === 0 && <li className="text-zinc-500">コミット済みの変更はありません</li>}
          </ul>
          {data.uncommitted && data.uncommitted.count > 0 && (
            <div data-testid="workspace-diff-uncommitted" className="rounded border border-amber-300 px-2 py-1 text-amber-800 dark:border-amber-800 dark:text-amber-300">
              <p>未コミットの変更 {data.uncommitted.count} 件は、取り込む前にコミットされます（中身はコミット後に見られます）。</p>
              <ul className="mt-1 font-mono text-[11px]">
                {data.uncommitted.files.map((f) => (
                  <li key={f.path} className="flex gap-2"><span className="min-w-0 flex-1 truncate">{f.path}</span><span className="shrink-0">{f.status === "untracked" ? "未追跡" : "変更"}</span></li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
      {path && (
        <div className="space-y-2">
          <button type="button" onClick={() => setPath("")} className="min-h-11 text-zinc-700 underline-offset-2 hover:underline dark:text-zinc-200">
            ← ファイルの一覧へ
          </button>
          <p className="break-all font-mono text-[11px] text-zinc-600 dark:text-zinc-300">{path}</p>
          {isFetching && !file && <p className="text-zinc-500">読み込み中…</p>}
          {file?.binary && <p data-testid="workspace-diff-binary" className="text-zinc-500">バイナリのファイル（差分は出しません）</p>}
          {file && !file.binary && <DiffLines diff={file.diff} />}
          {file?.truncated && (
            <p data-testid="workspace-diff-truncated" className="text-amber-700 dark:text-amber-400">
              大きいので先頭の {Math.round((data?.limit ?? 0) / 1024)}KB で打ち切りました（全体 {Math.round(file.size / 1024)}KB）。続きは PR で見てください。
            </p>
          )}
        </div>
      )}
    </div>
  );
}
