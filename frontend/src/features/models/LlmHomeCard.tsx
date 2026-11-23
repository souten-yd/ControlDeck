/** Home の LLM カード: ローカル LLM と OpenRouter（外部モデル）の「いま」を 1 枚にまとめる。
 *
 * 左右は同じ作り: 見出し → モデル名 → 前処理（OpenRouter は初回応答）・生成・ctx → 区切り →
 * お金の 2 行（ローカルは LLM 処理中の電力と今日の電気代、OpenRouter は残りクレジットと今日の使用）。
 * 携帯（320px〜）でも左右半分ずつで、モデル名が切れない幅を先に確保している。
 * ローカル側は workflows.run の利用者に常に出す（アンロード中は最後に動いていたモデルの値を薄く）。
 * 外部側は settings.manage の利用者だけ。 */
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getLlamaCapacity, type EndpointCapacity, type FailedInstance, type LastLocal, type LlmPower } from "../../api/models";
import {
  CLOUD_QUERY_KEY, getCloudAccount, getCloudState, getCloudSummary, type CloudSummary,
} from "../../api/cloudModels";
import { useAuth } from "../../stores";

function usd(v: number | null | undefined): string {
  if (v == null) return "—";
  if (v === 0) return "$0";
  if (v < 0.01) return `$${v.toFixed(4)}`;
  return `$${v.toFixed(2)}`;
}

function yen(v: number): string {
  return v < 100 ? `¥${v.toFixed(1)}` : `¥${Math.round(v).toLocaleString()}`;
}

function tokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1000) return `${Math.round(n / 1000)}K`;
  return String(n);
}

function duration(seconds: number): string {
  if (seconds < 60) return `${seconds}秒`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}分`;
  return `${(seconds / 3600).toFixed(1)}時間`;
}

/** "anthropic/claude-sonnet-4.5" → "claude-sonnet-4.5"（全体は title に出す）。 */
function shortModel(id: string): string {
  const i = id.indexOf("/");
  return i >= 0 ? id.slice(i + 1) : id;
}

const Muted = ({ children = "—" }: { children?: React.ReactNode }) => <span className="text-zinc-400">{children}</span>;
const Unit = ({ children }: { children: React.ReactNode }) => <span className="text-[10px] text-zinc-400 md:text-[11px]">{children}</span>;

/** モバイルは「名前を上、値を下」にして 2 列に並べる（前処理と生成を横に・平均と今日を横に）。md 以上は従来どおり左右。 */
function Row({ label, children, wide = false }: { label: string; children: React.ReactNode; wide?: boolean }) {
  return (
    <div className={`num flex min-w-0 flex-col text-[11px] leading-tight md:flex-row md:items-baseline md:justify-between md:gap-1.5 md:text-[13px] md:leading-relaxed ${wide ? "col-span-2 md:col-auto" : ""}`}>
      <span className="shrink-0 text-[9px] text-zinc-400 md:text-[11px]">{label}</span>
      <span className="min-w-0 truncate md:text-right">{children}</span>
    </div>
  );
}

/** ctx は 1 行: モバイルは「ctx 12K / 131K」の右に幅半分ほどの棒を並べる（2 段にしない）。md 以上は従来どおり行の下に棒。 */
function CtxRow({ used, total, children, className = "" }: {
  used: number | null | undefined; total: number | null | undefined; children: React.ReactNode; className?: string;
}) {
  const pct = used != null && total ? Math.min(100, (used / total) * 100) : 0;
  return (
    <div className="col-span-2 flex items-center gap-2 md:contents">
      <div className="num flex min-w-0 shrink-0 items-baseline gap-1.5 text-[11px] leading-tight md:justify-between md:text-[13px] md:leading-relaxed">
        <span className="shrink-0 text-[9px] text-zinc-400 md:text-[11px]">ctx</span>
        <span className={`shrink-0 whitespace-nowrap md:min-w-0 md:shrink md:truncate ${className}`}>{children}</span>
      </div>
      <div className="mb-0.5 mt-px h-1 min-w-4 max-w-[50%] flex-1 overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-700 md:max-w-none">
        <div className={`h-full rounded-full ${pct >= 90 ? "bg-amber-500" : "bg-accent-500"}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function Column({ heading, short, to, model, badge, children }: {
  heading: string; short: string; to: string; model: React.ReactNode; badge?: React.ReactNode; children: React.ReactNode;
}) {
  return (
    <div className="min-w-0">
      {/* モバイルは 1 段目に大きめの見出し、2 段目に小さくモデル名。md 以上は見出しの下にモデル名 */}
      <div className="min-w-0">
        <div className="flex shrink-0 items-center gap-1.5">
          <Link to={to} className="text-sm font-bold text-zinc-700 hover:underline dark:text-zinc-200 md:text-xs md:font-semibold md:text-zinc-500 md:dark:text-zinc-500">
            <span className="md:hidden">{short}</span><span className="hidden md:inline">{heading}</span>
          </Link>
          <span className="hidden md:contents">{badge}</span>
        </div>
        <div className="flex min-w-0 items-center gap-1.5 text-[10px] leading-tight text-zinc-500 md:mb-1 md:mt-0.5 md:text-sm md:text-inherit">{model}</div>
      </div>
      <div className="mt-0.5 grid grid-cols-2 gap-x-2 gap-y-0.5 md:mt-0 md:block md:max-w-[18rem]">{children}</div>
    </div>
  );
}

const Divider = () => <div className="col-span-2 my-0.5 border-t border-zinc-100 dark:border-zinc-800" />;

function ModelName({ name, title, active, more = 0 }: { name: string; title?: string; active: boolean; more?: number }) {
  return (
    <>
      {active && <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-accent-500" />}
      <span className={`min-w-0 truncate ${active ? "" : "text-zinc-500"}`} title={title ?? name}>{name}</span>
      {more > 0 && <span className="shrink-0 text-[10px] text-zinc-400">+{more}</span>}
    </>
  );
}

function LocalColumn({ endpoints, failed, power, lastSeen }: {
  endpoints: EndpointCapacity[]; failed: FailedInstance[]; power: LlmPower | undefined;
  lastSeen: LastLocal | null | undefined;
}) {
  // 複数動いていれば、処理中のものを先に出す。動いていなければ最後に見えていたモデルを「アンロード」で出す。
  const live = [...endpoints].sort((a, b) => b.busy - a.busy)[0];
  const item: Partial<EndpointCapacity> | null | undefined = live ?? lastSeen;
  // 起動に失敗しているときは、アンロードより失敗を先に出す（速さの行は最後の値を薄く）。
  const startFailed = !live && failed.length > 0;
  const unloaded = !live && !startFailed && !!lastSeen;
  const past = !live ? "text-zinc-500" : "";
  const loading = !!live && (!live.available || live.slots === 0);
  const used = item ? (item.timed_by_controldeck ? item.ctx_active : item.ctx_used) : null;
  const total = item?.ctx_total ?? 0;
  const prefill = item?.prefill_tokens_per_second ?? 0;
  // 待機中に 0 を返す実行環境では、直前の 1 本の速さを出す。
  const decode = (item?.tokens_per_second || item?.tokens_per_second_single) ?? 0;
  const on = power?.enabled;
  // 平均は今日 LLM が処理していた間の積算（電力量 ÷ 時間）。処理中でも瞬間の値にしない。
  const watts = power?.avg_w;
  return (
    <Column heading="ローカル LLM" short="ローカル" to="/models?tab=llm"
      badge={unloaded && (
        <span className="shrink-0 rounded border border-zinc-300 px-1 text-[9px] leading-tight text-zinc-500 dark:border-zinc-600 md:text-[10px]">アンロード</span>
      )}
      model={live
        ? <ModelName name={live.running_alias} title={`${live.running_alias} :${live.port}`} active={live.busy > 0} more={endpoints.length - 1} />
        : startFailed
          ? <span className="truncate text-red-600 dark:text-red-400" title={failed[0].error}>起動失敗: {failed[0].alias}</span>
          : unloaded
            ? <ModelName name={lastSeen!.running_alias ?? ""} title={`${lastSeen!.running_alias}（最後に動いていたモデル）`} active={false} />
            : <Muted>未ロード</Muted>}>
      <Row label={live?.phase === "prefill" ? "前処理中" : "前処理"}>
        {loading ? <Muted>起動中…</Muted>
          : prefill > 0 ? <span className={past}>{Math.round(prefill).toLocaleString()} <Unit>tok/s</Unit></span>
          : <Muted />}
      </Row>
      <Row label={live?.phase === "think" ? "思考中" : "生成"}>
        {!loading && decode > 0
          ? <span className={past}>{decode.toFixed(1)} <Unit>tok/s</Unit></span> : <Muted />}
      </Row>
      <CtxRow used={used} total={total} className={past}>
        {used != null ? tokens(used) : <Muted />} / {total > 0 ? tokens(total) : "—"}
      </CtxRow>
      <Divider />
      <Row label="平均">
        {on && watts != null
          ? <>{Math.round(watts)} <Unit>W</Unit> · {yen(power!.yen_per_hour ?? 0)}<Unit>/h</Unit></>
          : <Muted />}
      </Row>
      <Row label="今日">
        {on ? <>{yen(power!.today_yen ?? 0)} <Unit>· {duration(power!.today_seconds ?? 0)}</Unit></> : <Muted />}
      </Row>
    </Column>
  );
}

function CloudColumn({ summary, balance, balanceError }: {
  summary: CloudSummary | undefined; balance: number | null | undefined; balanceError: boolean;
}) {
  const running = summary?.running.filter((r) => r.provider === "openrouter") ?? [];
  const call = running[0];
  const last = summary?.last?.provider === "openrouter" ? summary.last : null;
  const ttft = call?.ttft_s ?? (call ? null : last?.ttft_s);
  const speed = call ? (call.phase === "generate" ? call.tokens_per_second : null) : last?.tokens_per_second;
  // ctx は終わった呼び出しの値。実行中が別のモデルなら、見出しと食い違うので出さない。
  const ctxFrom = last && (!call || call.model === last.model) ? last : null;
  const ctxUsed = ctxFrom ? ctxFrom.prompt_tokens + ctxFrom.completion_tokens : null;
  const pending = <Muted>…</Muted>;
  return (
    <Column heading="OpenRouter" short="OpenRouter" to="/models?tab=frontier"
      model={call ? <ModelName name={shortModel(call.model)} title={call.model} active more={running.length - 1} />
        : last ? <ModelName name={shortModel(last.model)} title={`${last.model}（直前）`} active={false} />
        : <Muted>{summary ? "なし" : "…"}</Muted>}>
      <Row label="初回応答">
        {call?.phase === "waiting" ? <Muted>応答待ち</Muted> : ttft != null ? <>{ttft.toFixed(1)} <Unit>s</Unit></> : <Muted />}
      </Row>
      <Row label="生成">{speed != null ? <>{speed.toFixed(1)} <Unit>tok/s</Unit></> : <Muted />}</Row>
      <CtxRow used={ctxUsed} total={ctxFrom?.context_length}>
        {ctxUsed != null ? tokens(ctxUsed) : <Muted />}{ctxFrom?.context_length ? ` / ${tokens(ctxFrom.context_length)}` : ""}
      </CtxRow>
      <Divider />
      <Row label="残り">
        <span className={balance != null && balance < 1 ? "text-amber-600 dark:text-amber-400" : ""}>
          {balance !== undefined ? usd(balance) : balanceError ? <Muted>取得不可</Muted> : pending}
        </span>
      </Row>
      <Row label="今日">
        {summary
          ? <>{usd(summary.today.cost_usd)} <Unit>· {summary.today.calls}回</Unit></>
          : pending}
      </Row>
    </Column>
  );
}

export function LlmHomeCard() {
  const can = useAuth((s) => s.can);
  const canCloud = can("settings.manage");
  // ローカル側の API は workflows.run が要る。無い利用者には取りに行かない（403 を出し続けない）。
  const canLocal = can("workflows.run");
  // Models 画面の CapacityWidget と同じキーなので、取得は 1 回で済む。
  const { data: capacity } = useQuery({
    queryKey: ["llama-capacity"], queryFn: getLlamaCapacity, enabled: canLocal,
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
  // ローカル側は、見られる利用者には常に出す（アンロード中は最後に動いていたモデル、無ければ「未ロード」）。
  const hasLocal = canLocal && !!capacity;
  if (!hasLocal && !hasCloud) return null;

  const both = hasLocal && hasCloud;
  const acct = account.data?.supported ? account.data : null;
  return (
    <section aria-label="LLM の利用状況"
      className="rounded-2xl border border-zinc-200 bg-white p-2.5 dark:border-zinc-800 dark:bg-zinc-900 md:p-4">
      <div className={both ? "grid grid-cols-2 gap-x-3 md:gap-x-6" : ""}>
        {hasLocal && <LocalColumn endpoints={endpoints} failed={failed} power={capacity?.power} lastSeen={capacity?.last_local} />}
        {hasCloud && (
          <div className={both ? "min-w-0 border-l border-zinc-100 pl-3 dark:border-zinc-800 md:pl-6" : "min-w-0"}>
            <CloudColumn summary={summary.data}
              balance={acct ? acct.balance : undefined} balanceError={account.isError} />
          </div>
        )}
      </div>
    </section>
  );
}
