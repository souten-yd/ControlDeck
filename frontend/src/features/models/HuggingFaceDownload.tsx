/** HuggingFace から GGUF を直接ダウンロードする。
 *
 * repo 検索 → 量子化バリアント選択 → 保存先ライブラリ の3段階。
 * バリアントはサイズと選択中ライブラリの空き容量を並べ、入らないものは選べない
 * （途中で詰まると中途半端なファイルが残るため、開始前に弾く）。 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  listHfRepoFiles, searchHfRepos, startHfDownload,
  type HfVariant, type ModelLibrary,
} from "../../api/models";
import { useToasts } from "../../stores";
import { Skeleton } from "../../components/ui";
import { IconSearch } from "../../components/icons";

function gb(n: number): string {
  return n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : `${(n / 1e6).toFixed(0)} MB`;
}

export function HuggingFaceDownload({ onStarted }: { onStarted: (jobId: string) => void }) {
  const show = useToasts((s) => s.show);
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [repo, setRepo] = useState("");
  const [libraryId, setLibraryId] = useState("");
  const [alias, setAlias] = useState("");
  const [starting, setStarting] = useState(false);

  const { data: repos, isFetching } = useQuery({
    queryKey: ["hf-search", submitted],
    queryFn: () => searchHfRepos(submitted),
    enabled: submitted.length > 0,
  });
  const { data: detail, isFetching: loadingFiles } = useQuery({
    queryKey: ["hf-files", repo],
    queryFn: () => listHfRepoFiles(repo),
    enabled: repo.length > 0,
  });

  const libraries = detail?.libraries ?? [];
  const library: ModelLibrary | undefined =
    libraries.find((l) => l.id === libraryId) ?? libraries.find((l) => l.default) ?? libraries[0];
  const free = library?.free_bytes ?? null;

  const fits = (variant: HfVariant) => free === null || variant.size < free;

  const start = async (variant: HfVariant) => {
    if (!library) return;
    setStarting(true);
    try {
      const { job_id } = await startHfDownload({
        repo, files: variant.files, library_id: library.id,
        expected_bytes: variant.size, alias: alias.trim() || undefined,
      });
      show("ダウンロードを開始しました（閉じても継続します）");
      onStarted(job_id);
    } catch (e) {
      show(e instanceof Error ? e.message : "開始に失敗しました", "error");
    } finally {
      setStarting(false);
    }
  };

  const input = "w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900";

  return (
    <div className="space-y-3">
      <form onSubmit={(e) => { e.preventDefault(); setSubmitted(query.trim()); setRepo(""); }}
        className="flex gap-1.5">
        <input value={query} onChange={(e) => setQuery(e.target.value)}
          placeholder="モデル名で検索（例: qwen3 gguf）" className={`${input} min-w-0 flex-1`} />
        <button type="submit" disabled={!query.trim()}
          className="flex shrink-0 items-center gap-1 rounded-xl bg-accent-600 px-3 py-2 text-sm font-medium text-white disabled:opacity-40">
          <IconSearch /> 検索
        </button>
      </form>

      {isFetching && <Skeleton className="h-20" />}
      {!repo && repos && repos.length > 0 && (
        <ul className="max-h-56 space-y-1 overflow-y-auto">
          {repos.map((item) => (
            <li key={item.repo}>
              <button onClick={() => setRepo(item.repo)}
                className="flex w-full items-center gap-2 rounded-xl border border-zinc-200 px-3 py-2 text-left hover:border-accent-400 dark:border-zinc-700">
                <span className="min-w-0 flex-1 truncate font-mono text-xs">{item.repo}</span>
                {item.gated && (
                  <span className="shrink-0 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] text-amber-700 dark:bg-amber-900/50 dark:text-amber-300">
                    要トークン
                  </span>
                )}
                <span className="num shrink-0 text-[10px] text-zinc-400">
                  ↓{item.downloads.toLocaleString()}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {!repo && repos?.length === 0 && (
        <p className="text-xs text-zinc-400">見つかりませんでした。</p>
      )}

      {repo && (
        <div className="space-y-2.5 rounded-xl border border-zinc-200 p-3 dark:border-zinc-700">
          <div className="flex items-center gap-2">
            <span className="min-w-0 flex-1 truncate font-mono text-xs font-semibold">{repo}</span>
            <button onClick={() => setRepo("")} className="shrink-0 text-[11px] text-zinc-500">戻る</button>
          </div>

          <label className="block">
            <span className="mb-1 block text-xs font-medium text-zinc-500">保存先</span>
            <select value={library?.id ?? ""} onChange={(e) => setLibraryId(e.target.value)} className={input}>
              {libraries.filter((l) => l.mounted).map((l) => (
                <option key={l.id} value={l.id}>
                  {l.label}（空き {l.free_bytes !== null ? gb(l.free_bytes) : "?"}）
                </option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="mb-1 block text-xs font-medium text-zinc-500">
              モデル名（alias・空なら登録せずダウンロードのみ）
            </span>
            <input value={alias} onChange={(e) => setAlias(e.target.value)}
              placeholder="登録する場合のみ入力" className={`${input} font-mono text-xs`} />
          </label>

          {loadingFiles ? <Skeleton className="h-24" /> : (
            <ul className="max-h-64 space-y-1 overflow-y-auto">
              {detail?.variants.map((variant) => {
                const ok = fits(variant) && variant.complete;
                return (
                  <li key={variant.name}
                    className="flex items-center gap-2 rounded-lg border border-zinc-200 px-2.5 py-2 dark:border-zinc-700">
                    <div className="min-w-0 flex-1">
                      <p className="truncate font-mono text-[11px]">{variant.name}</p>
                      <p className="num text-[10px] text-zinc-400">
                        {gb(variant.size)}
                        {variant.sharded && ` · ${variant.files.length}分割`}
                        {!variant.complete && " · 分割が揃っていません"}
                        {!fits(variant) && " · 空き容量が足りません"}
                      </p>
                    </div>
                    <button onClick={() => void start(variant)} disabled={!ok || starting || !library}
                      className="shrink-0 rounded-lg bg-accent-600 px-2.5 py-1.5 text-[11px] font-medium text-white disabled:opacity-40">
                      取得
                    </button>
                  </li>
                );
              })}
              {detail?.variants.length === 0 && (
                <p className="text-xs text-zinc-400">この repo に GGUF がありません。</p>
              )}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
