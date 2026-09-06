/** Add-on がいま何をしているか。
 *
 * MediaForge や SonicForge は OpenCode などの agent から MCP 経由で呼ばれる。
 * その間 GPU も時間も使うが、呼び出しは agent の中で完結するので、Home からは
 * 「何も起きていない」ようにしか見えなかった。画像を作っている最中なのか、
 * ただ止まっているのかが分からない。
 *
 * 出すのは実行中のものだけにする。終わったものを並べると、いま動いているものが
 * 埋もれるうえ、済んだ作業を「動いている」と読み違える。
 *
 * 表示は英語。add-on の label もツール名も英語なので、ここだけ日本語にすると
 * 画面の中で言葉が混ざる。
 */
import { useQuery } from "@tanstack/react-query";
import {
  listAgentToolJobs,
  splitAgentToolKind,
  type AgentToolJob,
} from "../../api/addonTools";

const RUNNING = new Set(["queued", "running"]);

function elapsed(from: number | null | undefined): string {
  if (!from) return "";
  const seconds = Math.max(0, Math.round(Date.now() / 1000 - from));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  return minutes < 60 ? `${minutes}m ${seconds % 60}s` : `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

/** 何をしているか。job の title に入っていればそれを使う。
 *
 *  title は job を作るときに引数から決めている（画像か動画かは引数にしか無い）。
 *  古い job や、種別を割り出せなかったものは `{addon}: {tool}` のままなので、
 *  そのときは add-on 名だけを出して「何かしている」ことは伝える。 */
function activity(job: AgentToolJob): { label: string; addon: string } {
  const { addon } = splitAgentToolKind(job.kind);
  const title = (job.title || "").trim();
  const derived = title && !title.includes(": ") ? title : "";
  return { label: derived || "Working", addon };
}

export function AgentToolWidget({ compact = false }: { compact?: boolean }) {
  const { data } = useQuery({
    queryKey: ["addon-agent-tool-jobs"],
    queryFn: () => listAgentToolJobs(20),
    // 動いている間だけ短くする。止まっていれば無駄に叩かない。
    refetchInterval: (q) =>
      (q.state.data ?? []).some((job) => RUNNING.has(job.status)) ? 2000 : 15000,
  });

  const running = (data ?? []).filter((job) => RUNNING.has(job.status));
  // 動いていなければ出さない。空の枠は場所を取るだけになる。
  if (running.length === 0) return null;

  return (
    <section
      className={
        compact
          ? ""
          : "rounded-2xl border border-zinc-200 bg-white p-2.5 dark:border-zinc-800 dark:bg-zinc-900 md:p-4"
      }
    >
      <div className="space-y-2">
        {running.map((job) => {
          const { label, addon } = activity(job);
          return (
            <div key={job.id} className="flex items-center gap-2.5">
              <span
                className="h-2 w-2 shrink-0 animate-pulse rounded-full bg-accent-500"
                aria-hidden
              />
              <span className="min-w-0 flex-1 truncate text-xs font-medium md:text-sm">
                {label}
                <span className="ml-1.5 text-[10px] font-normal text-zinc-400 md:text-[11px]">
                  {addon}
                </span>
              </span>
              <span className="num shrink-0 text-[10px] text-zinc-400 md:text-[11px]">
                {elapsed(job.created_at)}
              </span>
            </div>
          );
        })}
      </div>
    </section>
  );
}
