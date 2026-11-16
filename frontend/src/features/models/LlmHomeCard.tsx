/** Home の LLM カード: ローカル LLM の利用状況と OpenRouter（外部モデル）の状況を 1 枚にまとめる。
 *
 * Models 画面まで行かなくても「今なにが動いていて、どれだけ速く、いくら使ったか」が分かるようにする。
 * ローカル側は CapacityWidget をそのまま使い、外部側は ControlDeck が中継した呼び出しの記録
 * （/models/cloud-summary）と OpenRouter の残高（/models/cloud/openrouter/account）から出す。
 * どちらも無ければカードごと出さない。外部側は settings.manage の利用者だけ。 */
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getLlamaCapacity } from "../../api/models";
import {
  CLOUD_QUERY_KEY, getCloudAccount, getCloudState, getCloudSummary,
  type CloudModelStat, type CloudRunningCall,
} from "../../api/cloudModels";
import { useAuth } from "../../stores";
import { CapacityWidget, Row } from "./CapacityWidget";

function usd(v: number | null | undefined): string {
  if (v == null) return "—";
  if (v === 0) return "$0";
  if (v < 0.01) return `$${v.toFixed(4)}`;
  return `$${v.toFixed(2)}`;
}

function tokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  return n >= 1000 ? `${(n / 1000).toFixed(1)}K` : String(n);
}

/** "anthropic/claude-sonnet-4.5" → "claude-sonnet-4.5"（全体は title に出す）。 */
function shortModel(id: string): string {
  const i = id.indexOf("/");
  return i >= 0 ? id.slice(i + 1) : id;
}

function ago(at: number): string {
  const s = Math.max(0, Date.now() / 1000 - at);
  if (s < 60) return "たった今";
  if (s < 3600) return `${Math.floor(s / 60)}分前`;
  if (s < 86400) return `${Math.floor(s / 3600)}時間前`;
  return `${Math.floor(s / 86400)}日前`;
}

function RunningCall({ call }: { call: CloudRunningCall }) {
  return (
    <span className="flex min-w-0 flex-wrap items-baseline gap-x-2">
      <span className="flex items-center gap-1.5 truncate" title={call.model}>
        <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-accent-500" />
        {shortModel(call.model)}
      </span>
      <span className="text-[10px] text-zinc-400">
        {call.phase === "waiting" ? "応答待ち" : call.tokens_per_second != null ? `生成中 ${call.tokens_per_second.toFixed(1)} tok/s` : "生成中"}
        {" · "}{call.elapsed_s.toFixed(0)}秒 · {call.caller_label}
      </span>
    </span>
  );
}

function ModelLine({ m }: { m: CloudModelStat }) {
  return (
    <li className="flex items-baseline gap-2 text-[11px] md:text-xs">
      <span className="min-w-0 flex-1 truncate" title={m.model}>{shortModel(m.model)}</span>
      <span className="num shrink-0 text-zinc-500">
        {m.tokens_per_second != null ? `${m.tokens_per_second.toFixed(0)} tok/s` : "—"}
      </span>
      <span className="num w-14 shrink-0 text-right text-[10px] text-zinc-400" title="最初のトークンまでの平均">
        {m.ttft_s != null ? `初回 ${m.ttft_s.toFixed(1)}s` : ""}
      </span>
      <span className="num w-10 shrink-0 text-right text-[10px] text-zinc-400">{m.calls}回</span>
    </li>
  );
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
  const budget = provider!.monthly_budget_usd;

  return (
    <div className="min-w-0">
      <div className="mb-1 flex items-baseline gap-2">
        <h3 className="text-[11px] font-semibold text-zinc-500 md:text-xs">OpenRouter</h3>
        <Link to="/models?tab=frontier" className="ml-auto text-[11px] text-zinc-400 hover:underline" title="アカウントの残高（入金 − 使用）">
          残高 <span className={`num font-semibold ${a?.balance != null && a.balance < 1 ? "text-amber-600 dark:text-amber-400" : "text-zinc-700 dark:text-zinc-200"}`}>
            {a ? usd(a.balance) : account.isError ? "取得不可" : "…"}
          </span>
        </Link>
      </div>
      <div className="space-y-0.5">
        <Row label="実行中">
          {running.length === 0
            ? <span className="text-zinc-400">なし</span>
            : <span className="block space-y-0.5">{running.slice(0, 3).map((c, i) => <RunningCall key={i} call={c} />)}</span>}
        </Row>
        <Row label="今日">
          {s ? (
            <span className="flex flex-wrap items-baseline gap-x-2">
              <span>{usd(s.today.cost_usd)}</span>
              <span className="text-[10px] text-zinc-400">
                {s.today.calls}回 · {tokens(s.today.prompt_tokens)}→{tokens(s.today.completion_tokens)} tok
                {s.today.errors > 0 && <span className="text-amber-600 dark:text-amber-400"> · 失敗 {s.today.errors}</span>}
              </span>
            </span>
          ) : <span className="text-zinc-400">…</span>}
        </Row>
        <Row label="今月">
          {s ? (
            <span className="flex flex-wrap items-baseline gap-x-2">
              <span>{usd(s.month.cost_usd)}</span>
              <span className="text-[10px] text-zinc-400">
                {s.month.calls}回{budget > 0 ? ` · 上限 ${usd(budget)}（${Math.round((s.month.cost_usd / budget) * 100)}%）` : ""}
              </span>
            </span>
          ) : <span className="text-zinc-400">…</span>}
        </Row>
        {s?.last && (
          <Row label="前回">
            <span className="flex min-w-0 flex-wrap items-baseline gap-x-2">
              <span className="truncate" title={s.last.model}>{shortModel(s.last.model)}</span>
              <span className="text-[10px] text-zinc-400">
                {s.last.status >= 400 ? `失敗（HTTP ${s.last.status}）` : [
                  s.last.tokens_per_second != null ? `${s.last.tokens_per_second.toFixed(1)} tok/s` : null,
                  s.last.cost_usd != null ? usd(s.last.cost_usd) : null,
                ].filter(Boolean).join(" · ")}
                {" · "}{ago(s.last.at)}
              </span>
            </span>
          </Row>
        )}
        {a?.free_requests_remaining != null && (
          <Row label="無料枠">
            <span>今日あと {a.free_requests_remaining.toLocaleString()} 回</span>
          </Row>
        )}
      </div>
      {s && s.models.length > 0 && (
        <div className="mt-1.5 border-t border-zinc-100 pt-1.5 dark:border-zinc-800">
          <p className="mb-0.5 text-[10px] text-zinc-400">最近 7 日のモデル（平均の速さ）</p>
          <ul className="space-y-0.5">
            {s.models.filter((m) => m.provider === "openrouter").slice(0, 3).map((m) => <ModelLine key={m.model} m={m} />)}
          </ul>
        </div>
      )}
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

  return (
    <section aria-label="LLM の利用状況"
      className="rounded-2xl border border-zinc-200 bg-white p-2.5 dark:border-zinc-800 dark:bg-zinc-900 md:p-4">
      <div className={`grid gap-x-6 gap-y-2.5 ${hasLocal && hasCloud ? "sm:grid-cols-2" : ""}`}>
        {hasLocal && (
          <div className="min-w-0">
            <h3 className="mb-1 text-[11px] font-semibold text-zinc-500 md:text-xs">ローカル LLM</h3>
            <CapacityWidget compact showAlias />
          </div>
        )}
        {hasCloud && (
          <div className={hasLocal ? "border-t border-zinc-100 pt-2 dark:border-zinc-800 sm:border-l sm:border-t-0 sm:pl-6 sm:pt-0" : ""}>
            <OpenRouterSection />
          </div>
        )}
      </div>
    </section>
  );
}
