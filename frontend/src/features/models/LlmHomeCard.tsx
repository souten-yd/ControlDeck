/** Home の LLM カード: ローカル LLM と OpenRouter（外部モデル）の「いま」を 1 枚にまとめる。
 *
 * 出すのは、動いているモデル・tok/s・残りクレジット・今日の使用額と回数だけ（細目は Models 画面）。
 * 携帯（320px〜）で読めることを先に決めた形にしている:
 * - 左右は同じ作り（見出し → モデル名 → 大きな tok/s）にして高さをそろえ、ラベルの列を置かない
 *   （ラベルの列があるとモデル名が数文字で切れる）。
 * - クレジットの 3 つは OpenRouter の列に詰めず、カードの下に全幅で横に並べる。
 * どちらも無ければカードごと出さない。外部側は settings.manage の利用者だけ。 */
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getLlamaCapacity, type EndpointCapacity, type FailedInstance } from "../../api/models";
import {
  CLOUD_QUERY_KEY, getCloudAccount, getCloudState, getCloudSummary, type CloudRunningCall,
} from "../../api/cloudModels";
import { useAuth } from "../../stores";

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

function Heading({ to, children }: { to: string; children: React.ReactNode }) {
  return (
    <Link to={to} className="block truncate text-[10px] font-semibold text-zinc-500 hover:underline md:text-xs">
      {children}
    </Link>
  );
}

/** モデル名と、その下の大きな tok/s。値が無いときは理由を薄く出す。 */
function ModelSpeed({ name, title, active, speed, note }: {
  name: React.ReactNode; title?: string; active: boolean; speed: number | null; note?: string;
}) {
  return (
    <div className="min-w-0">
      <p className="flex min-w-0 items-center gap-1.5 text-xs md:text-sm" title={title}>
        {active && <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-accent-500" />}
        <span className="min-w-0 truncate">{name}</span>
      </p>
      <p className="num leading-tight">
        {speed != null
          ? <><span className="text-lg font-semibold md:text-xl">{speed.toFixed(1)}</span>
              <span className="ml-1 text-[10px] text-zinc-400">tok/s</span></>
          : <span className="text-xs text-zinc-400">{note ?? "—"}</span>}
      </p>
    </div>
  );
}

function localSpeed(item: EndpointCapacity): { speed: number | null; note?: string } {
  if (!item.available || item.slots === 0) return { speed: null, note: "起動中…" };
  if (item.phase === "prefill") return { speed: null, note: "読み込み中" };
  if (item.tokens_per_second > 0 || item.busy > 0) return { speed: item.tokens_per_second };
  return { speed: null, note: "待機中" };
}

function LocalColumn({ endpoints, failed }: { endpoints: EndpointCapacity[]; failed: FailedInstance[] }) {
  return (
    <div className="min-w-0 space-y-1">
      <Heading to="/models?tab=llm">ローカル LLM</Heading>
      {endpoints.map((item) => (
        <ModelSpeed key={item.id} name={item.running_alias} title={`${item.running_alias} :${item.port}`}
          active={item.busy > 0} {...localSpeed(item)} />
      ))}
      {failed.map((item) => (
        <p key={item.alias} className="truncate text-xs text-red-600 dark:text-red-400" title={item.error}>
          起動失敗: {item.alias}
        </p>
      ))}
    </div>
  );
}

function CloudColumn({ running, loaded }: { running: CloudRunningCall[]; loaded: boolean }) {
  const call = running[0];
  return (
    <div className="min-w-0 space-y-1">
      <Heading to="/models?tab=frontier">OpenRouter</Heading>
      {call ? (
        <ModelSpeed
          name={<>{shortModel(call.model)}{running.length > 1 && <span className="ml-1 text-[10px] text-zinc-400">+{running.length - 1}</span>}</>}
          title={call.model} active speed={call.phase === "waiting" ? null : call.tokens_per_second}
          note={call.phase === "waiting" ? "応答待ち" : "生成中"} />
      ) : (
        <ModelSpeed name={<span className="text-zinc-400">{loaded ? "実行中なし" : "…"}</span>} active={false} speed={null} />
      )}
    </div>
  );
}

function Stat({ label, children, warn = false }: { label: string; children: React.ReactNode; warn?: boolean }) {
  return (
    <div className="min-w-0">
      <p className="text-[10px] text-zinc-400">{label}</p>
      <p className={`num truncate text-sm font-medium ${warn ? "text-amber-600 dark:text-amber-400" : ""}`}>{children}</p>
    </div>
  );
}

export function LlmHomeCard() {
  const can = useAuth((s) => s.can);
  const canCloud = can("settings.manage");
  // Models 画面の CapacityWidget と同じキーなので、取得は 1 回で済む。
  const { data: capacity } = useQuery({
    queryKey: ["llama-capacity"], queryFn: getLlamaCapacity,
    refetchInterval: (q) => (q.state.data?.endpoints.length ? 3000 : 20000),
  });
  const { data: cloudState } = useQuery({
    queryKey: CLOUD_QUERY_KEY, queryFn: getCloudState, enabled: canCloud, staleTime: 60_000,
  });
  const openrouter = cloudState?.providers.find((p) => p.id === "openrouter");
  const hasCloud = canCloud && !!openrouter?.configured && !!openrouter.enabled;
  const summary = useQuery({
    queryKey: ["cloud-summary"], queryFn: getCloudSummary, enabled: hasCloud,
    // 実行中だけ短い間隔で追う。
    refetchInterval: (q) => (q.state.data?.running.length ? 3000 : 15000),
  });
  // 残高は OpenRouter へ問い合わせるので、頻繁には取りに行かない。
  const account = useQuery({
    queryKey: ["cloud-account", "openrouter"], queryFn: () => getCloudAccount("openrouter"), enabled: hasCloud,
    staleTime: 60_000, refetchInterval: 300_000, retry: false,
  });

  const endpoints = capacity?.endpoints ?? [];
  const failed = capacity?.failed ?? [];
  const hasLocal = endpoints.length > 0 || failed.length > 0;
  if (!hasLocal && !hasCloud) return null;

  const s = summary.data;
  const a = account.data?.supported ? account.data : null;
  const running = s?.running.filter((r) => r.provider === "openrouter") ?? [];
  const both = hasLocal && hasCloud;
  const pending = <span className="text-zinc-400">…</span>;

  return (
    <section aria-label="LLM の利用状況"
      className="rounded-2xl border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900 md:p-4">
      <div className={both ? "grid grid-cols-2 gap-x-3 md:gap-x-6" : ""}>
        {hasLocal && <LocalColumn endpoints={endpoints} failed={failed} />}
        {hasCloud && (
          <div className={both ? "min-w-0 border-l border-zinc-100 pl-3 dark:border-zinc-800 md:pl-6" : "min-w-0"}>
            <CloudColumn running={running} loaded={!!s} />
          </div>
        )}
      </div>
      {hasCloud && (
        <div className="mt-2.5 grid grid-cols-3 gap-x-3 border-t border-zinc-100 pt-2 dark:border-zinc-800 md:gap-x-6">
          <Stat label="残りクレジット" warn={a?.balance != null && a.balance < 1}>
            {a ? usd(a.balance) : account.isError ? <span className="text-zinc-400">取得不可</span> : pending}
          </Stat>
          <Stat label="今日の使用">{s ? usd(s.today.cost_usd) : pending}</Stat>
          <Stat label="今日の回数">{s ? `${s.today.calls.toLocaleString()} 回` : pending}</Stat>
        </div>
      )}
    </section>
  );
}
