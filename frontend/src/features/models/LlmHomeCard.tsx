/** Home の LLM カード: ローカル LLM の利用状況と OpenRouter（外部モデル）の状況を 1 枚にまとめる。
 *
 * 出すのは「動いているモデル・tok/s・残りクレジット・今日の使用額と回数」だけ（細目は Models 画面）。ローカル側は CapacityWidget の詰めた表示、外部側は
 * ControlDeck が中継した呼び出しの記録（/models/cloud-summary）と OpenRouter の残高から出す。
 * どちらも無ければカードごと出さない。外部側は settings.manage の利用者だけ。 */
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getLlamaCapacity } from "../../api/models";
import {
  CLOUD_QUERY_KEY, getCloudAccount, getCloudState, getCloudSummary,
} from "../../api/cloudModels";
import { useAuth } from "../../stores";
import { CapacityWidget, DenseRows, Row } from "./CapacityWidget";

function usd(v: number | null | undefined): string {
  if (v == null) return "—";
  if (v === 0) return "$0";
  if (v < 0.01) return `$${v.toFixed(4)}`;
  return `$${v.toFixed(2)}`;
}

/** "anthropic/claude-sonnet-4.5" → "claude-sonnet-4.5"（全体は title に出す）。 */
function shortModel(id: string): string {
  const i = id.indexOf("/");
  return i >= 0 ? id.slice(i + 1) : id;
}

function OpenRouterSection() {
  const state = useQuery({ queryKey: CLOUD_QUERY_KEY, queryFn: getCloudState, staleTime: 60_000 });
  const provider = state.data?.providers.find((p) => p.id === "openrouter");
  const on = !!provider?.configured && !!provider.enabled;
  const summary = useQuery({
    queryKey: ["cloud-summary"], queryFn: getCloudSummary, enabled: on,
    // 実行中だけ短い間隔で追う。
    refetchInterval: (q) => (q.state.data?.running.length ? 3000 : 15000),
  });
  // 残高は OpenRouter へ問い合わせるので、頻繁には取りに行かない。
  const account = useQuery({
    queryKey: ["cloud-account", "openrouter"], queryFn: () => getCloudAccount("openrouter"), enabled: on,
    staleTime: 60_000, refetchInterval: 300_000, retry: false,
  });
  if (!on) return null;
  const s = summary.data;
  const a = account.data?.supported ? account.data : null;
  const running = s?.running.filter((r) => r.provider === "openrouter") ?? [];
  const call = running[0];
  const pending = <span className="text-zinc-400">…</span>;

  return (
    <div className="min-w-0">
      <Link to="/models?tab=frontier" className="mb-1 block text-[11px] font-semibold text-zinc-500 hover:underline md:text-xs">
        OpenRouter
      </Link>
      <div className="space-y-0.5">
        <Row label="モデル">
          {call
            ? <span className="flex min-w-0 items-center gap-1.5">
                <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-accent-500" />
                <span className="min-w-0 truncate" title={call.model}>{shortModel(call.model)}</span>
                {running.length > 1 && <span className="shrink-0 text-[10px] text-zinc-400">+{running.length - 1}</span>}
              </span>
            : <span className="text-zinc-400">{s ? "なし" : "…"}</span>}
        </Row>
        <Row label="tok/s">
          {!call ? <span className="text-zinc-400">—</span>
            : call.phase === "waiting" ? <span className="text-zinc-400">応答待ち</span>
            : <span>{call.tokens_per_second != null ? call.tokens_per_second.toFixed(1) : "…"}</span>}
        </Row>
        <Row label="残り">
          <span className={a?.balance != null && a.balance < 1 ? "text-amber-600 dark:text-amber-400" : ""}>
            {a ? usd(a.balance) : account.isError ? <span className="text-zinc-400">取得不可</span> : pending}
          </span>
        </Row>
        <Row label="今日">{s ? <span>{usd(s.today.cost_usd)}</span> : pending}</Row>
        <Row label="回数">{s ? <span>{s.today.calls.toLocaleString()} 回</span> : pending}</Row>
      </div>
    </div>
  );
}

export function LlmHomeCard() {
  const can = useAuth((s) => s.can);
  const canCloud = can("settings.manage");
  // CapacityWidget と同じキーなので、取得は 1 回で済む。
  const { data: capacity } = useQuery({
    queryKey: ["llama-capacity"], queryFn: getLlamaCapacity,
    refetchInterval: (q) => (q.state.data?.endpoints.length ? 3000 : 20000),
  });
  const { data: cloudState } = useQuery({
    queryKey: CLOUD_QUERY_KEY, queryFn: getCloudState, enabled: canCloud, staleTime: 60_000,
  });
  const hasLocal = !!capacity && (capacity.endpoints.length > 0 || (capacity.failed?.length ?? 0) > 0);
  const openrouter = cloudState?.providers.find((p) => p.id === "openrouter");
  const hasCloud = canCloud && !!openrouter?.configured && !!openrouter.enabled;
  if (!hasLocal && !hasCloud) return null;

  // 両方あるときは携帯でも左右半分ずつ。狭い幅ではラベルを短くした詰めた表示にする。
  const both = hasLocal && hasCloud;
  return (
    <section aria-label="LLM の利用状況"
      className="rounded-2xl border border-zinc-200 bg-white p-2.5 dark:border-zinc-800 dark:bg-zinc-900 md:p-4">
      <DenseRows.Provider value={true}>
        <div className={both ? "grid grid-cols-2 gap-x-2.5 sm:gap-x-6" : ""}>
          {hasLocal && (
            <div className="min-w-0">
              <h3 className="mb-1 text-[11px] font-semibold text-zinc-500 md:text-xs">ローカル LLM</h3>
              <CapacityWidget compact />
            </div>
          )}
          {hasCloud && (
            <div className={both ? "min-w-0 border-l border-zinc-100 pl-2.5 dark:border-zinc-800 sm:pl-6" : "min-w-0"}>
              <OpenRouterSection />
            </div>
          )}
        </div>
      </DenseRows.Provider>
    </section>
  );
}
