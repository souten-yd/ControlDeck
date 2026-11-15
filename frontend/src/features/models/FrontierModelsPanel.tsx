/** フロンティアモデル（外部の API プロバイダ）の設定。
 *
 * 既定ではどこにも送らない。送られるのは「キーがある・プロバイダが有効・呼び出し元を許可した・
 * その呼び出し元が cloud: のモデルを選んだ」がそろったときだけなので、その状態を先頭に 1 行で出す。
 * キーは保存後に画面へ戻ってこない（末尾 4 文字だけ）。 */
import { useDeferredValue, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CLOUD_QUERY_KEY, cloudGatewayId, getCloudState, getCloudUsage, listCloudRemoteModels,
  saveCloudEgress, saveCloudProvider,
  type CloudProvider, type CloudProviderPatch, type CloudRemoteModel, type CloudState, type CloudUsageRow,
} from "../../api/cloudModels";
import { BottomSheet, ConfirmDialog, Skeleton } from "../../components/ui";
import { IconSearch, IconShield, IconX } from "../../components/icons";
import { useToasts } from "../../stores";
import { SettingsGroup, SheetActions, Toggle, primaryButton, secondaryButton } from "./formControls";

const field = "min-h-11 w-full min-w-0 rounded-xl border border-zinc-300 bg-white px-3 text-sm disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900";
const smallButton = "min-h-11 shrink-0 rounded-xl border border-zinc-300 px-3 text-xs font-medium hover:bg-zinc-50 disabled:opacity-40 dark:border-zinc-700 dark:hover:bg-zinc-800";
const MAX_MODELS = 200;
const PAGE = 100;
const MODEL_ID = /^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}$/;

/** 呼び出し元ごとに、何が外へ出るのか。知らない呼び出し元には何も足さない。 */
const CALLER_SENDS: Record<string, string> = {
  opencode: "指示・コード・ファイルの内容・コマンドの出力",
  assistant: "会話・添付・検索や道具の結果",
  workflows: "LLM ノードに渡した入力",
  api: "そのクライアントが送ってきた要求の内容",
};

function usd(value: number): string {
  return `$${value.toFixed(value !== 0 && Math.abs(value) < 1 ? 4 : 2)}`;
}

function contextLabel(n: number | null): string {
  if (!n) return "";
  return n >= 1_000_000 ? `${+(n / 1_000_000).toFixed(1)}M` : `${Math.round(n / 1000)}K`;
}

function errorText(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

function useSaveProvider(provider: string) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  return useMutation({
    mutationFn: (patch: CloudProviderPatch) => saveCloudProvider(provider, patch),
    onSuccess: (next) => {
      qc.setQueryData(CLOUD_QUERY_KEY, next);
      // OpenCode / AI アシスタントのモデル一覧に cloud: の行がすぐ出入りするように。
      qc.invalidateQueries({ queryKey: ["llm-endpoints"] });
    },
    onError: (e) => show(errorText(e, "保存できませんでした"), "error"),
  });
}

export function FrontierModelsGroup() {
  const { data, isLoading, error, refetch } = useQuery({ queryKey: CLOUD_QUERY_KEY, queryFn: getCloudState });
  if (isLoading) return <Skeleton className="h-11" />;
  if (!data) {
    return (
      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-zinc-200 px-3 py-2 text-xs text-zinc-500 dark:border-zinc-700">
        <span className="min-w-0 flex-1">フロンティアモデルの設定を読み込めませんでした{error instanceof Error ? `（${error.message}）` : ""}</span>
        <button type="button" onClick={() => refetch()} className={smallButton}>再試行</button>
      </div>
    );
  }
  return <FrontierModelsLoaded state={data} />;
}

function FrontierModelsLoaded({ state }: { state: CloudState }) {
  // 開閉は最初の状態で決める。キーを消した瞬間に足元で畳まれないように、あとからは変えない。
  const active = state.providers.filter((p) => p.enabled);
  return (
    <SettingsGroup title="フロンティアモデル"
      description={active.length > 0
        ? `外部の API モデル · ${active.map((p) => p.name).join("・")} が有効`
        : "外部の API モデル（OpenRouter・OpenAI・Claude・Gemini ほか）· 既定では使いません"}>
      <EgressSummary state={state} />
      <ul className="space-y-2" aria-label="プロバイダ">
        {state.providers.map((p) => <ProviderCard key={p.id} provider={p} />)}
      </ul>
      <EgressBlock state={state} />
      <UsageBlock providers={state.providers} callerLabels={state.caller_labels} />
      <p className="rounded-xl bg-zinc-50 px-3 py-2.5 text-[11px] leading-relaxed text-zinc-600 dark:bg-zinc-800/60 dark:text-zinc-300">
        <span className="font-medium">ChatGPT / Claude の定額プランを使う場合</span>
        <span className="block">API キーではなく、公式の Codex CLI / Claude Code に機器認証でログインして使います（次の段階で対応予定）。プランのログイン情報を API キーの代わりに使うことはできません。</span>
      </p>
    </SettingsGroup>
  );
}

/** いま外へ出うる経路を 1 行で。何も出ないなら、そう言い切る。 */
function EgressSummary({ state }: { state: CloudState }) {
  const callers = Object.keys(state.egress).filter((c) => state.egress[c]).map((c) => state.caller_labels[c] ?? c);
  const providers = state.providers.filter((p) => p.enabled && p.models.length > 0).map((p) => p.name);
  const open = callers.length > 0 && providers.length > 0;
  return (
    <div role="status" className={`flex items-start gap-2 rounded-xl px-3 py-2.5 text-xs leading-relaxed ${open
      ? "bg-amber-50 text-amber-900 dark:bg-amber-950/40 dark:text-amber-100"
      : "bg-zinc-50 text-zinc-700 dark:bg-zinc-800/60 dark:text-zinc-200"}`}>
      <IconShield className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
      {open ? (
        <p className="min-w-0">
          <span className="font-semibold">外部へ送信できる状態です</span>
          <span className="block break-words">{callers.join("・")} → {providers.join("・")}</span>
          <span className="block text-[11px] opacity-80">送られるのは <span className="font-mono">cloud:</span> のモデルを選んだときだけです。</span>
        </p>
      ) : (
        <p className="min-w-0">
          <span className="font-semibold">いまは外部へ何も送信されません</span>
          <span className="block text-[11px] opacity-80">
            {providers.length === 0 ? "有効なプロバイダと使うモデルがありません。" : "どの呼び出し元にも外部への送信を許可していません。"}
          </span>
        </p>
      )}
    </div>
  );
}

function StatusChip({ provider }: { provider: CloudProvider }) {
  const [label, tone, dot] = !provider.configured
    ? ["未設定", "bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400", "border border-zinc-400"]
    : provider.enabled
      ? ["有効", "bg-emerald-100 text-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300", "bg-emerald-500"]
      : ["無効", "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300", "bg-zinc-400"];
  return (
    <span className={`inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-medium ${tone}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${dot}`} aria-hidden />{label}
    </span>
  );
}

function ProviderCard({ provider: p }: { provider: CloudProvider }) {
  const show = useToasts((s) => s.show);
  const save = useSaveProvider(p.id);
  const [open, setOpen] = useState(p.configured);
  const [editingKey, setEditingKey] = useState(false);
  const [key, setKey] = useState("");
  const [removing, setRemoving] = useState(false);
  const [picking, setPicking] = useState(false);
  const [baseUrl, setBaseUrl] = useState(p.base_url);
  const [budget, setBudget] = useState(p.monthly_budget_usd ? String(p.monthly_budget_usd) : "");
  const isCustom = p.id === "custom";
  const panelId = `cloud-provider-${p.id}`;
  const busy = save.isPending;

  const submitKey = (e: React.FormEvent) => {
    e.preventDefault();
    const value = key.trim();
    if (!value) return;
    save.mutate({ api_key: value }, {
      onSuccess: () => { setKey(""); setEditingKey(false); show(`${p.name} の API キーを保存しました`); },
    });
  };
  const budgetValue = budget.trim() === "" ? 0 : Number(budget);
  const budgetValid = Number.isFinite(budgetValue) && budgetValue >= 0 && budgetValue <= 100_000;
  const submitBudget = (e: React.FormEvent) => {
    e.preventDefault();
    if (!budgetValid) return;
    save.mutate({ monthly_budget_usd: budgetValue }, {
      onSuccess: () => show(budgetValue > 0 ? `月の上限を ${usd(budgetValue)} にしました` : "月の上限をなくしました"),
    });
  };
  const submitBaseUrl = (e: React.FormEvent) => {
    e.preventDefault();
    save.mutate({ base_url: baseUrl.trim() }, { onSuccess: () => show("送信先を保存しました") });
  };
  const copy = (text: string) => {
    const done = navigator.clipboard?.writeText(text);
    if (!done) { show("コピーできませんでした。長押しで選択してください", "error"); return; }
    done.then(() => show("コピーしました"), () => show("コピーできませんでした", "error"));
  };
  const enableBlocked = !p.configured ? "先に API キーを保存します"
    : isCustom && !p.base_url ? "先に送信先の URL を保存します" : "";
  const ratio = p.monthly_budget_usd > 0 ? p.spent_this_month_usd / p.monthly_budget_usd : 0;

  return (
    <li className="rounded-xl bg-zinc-50 dark:bg-zinc-800/50">
      <button type="button" aria-expanded={open} aria-controls={panelId} onClick={() => setOpen(!open)}
        className="flex min-h-11 w-full items-center gap-2 rounded-xl px-3 py-2 text-left">
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm font-semibold">{p.name}<StatusChip provider={p} /></span>
          <span className="mt-0.5 block text-[11px] leading-relaxed text-zinc-500">{p.note}</span>
        </span>
        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2"
          className={`shrink-0 text-zinc-400 transition-transform ${open ? "rotate-90" : ""}`} aria-hidden><path d="m9 6 6 6-6 6" /></svg>
      </button>
      {open && (
        <div id={panelId} className="space-y-3 px-3 pb-3">
          {isCustom && (
            <form onSubmit={submitBaseUrl} className="block text-xs">
              <label htmlFor={`${panelId}-url`}>送信先の URL（HTTPS）</label>
              <div className="mt-1 flex gap-2">
                <input id={`${panelId}-url`} type="url" inputMode="url" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)}
                  placeholder="https://example.com/v1" autoComplete="off" spellCheck={false} className={`${field} font-mono text-xs`} />
                <button type="submit" disabled={busy || !baseUrl.trim() || baseUrl.trim() === p.base_url} className={smallButton}>保存</button>
              </div>
            </form>
          )}

          <div className="text-xs">
            <div className="flex flex-wrap items-center justify-between gap-x-3">
              <label htmlFor={`${panelId}-key`} className="font-medium">API キー</label>
              {p.keys_url && (
                <a href={p.keys_url} target="_blank" rel="noopener noreferrer"
                  className="inline-flex min-h-11 items-center text-accent-600 underline dark:text-accent-400">キーを取得<span aria-hidden className="ml-0.5">↗</span></a>
              )}
            </div>
            {p.configured && !editingKey ? (
              <div className="flex flex-wrap items-center gap-2">
                <p className="min-w-0 flex-1 whitespace-nowrap">設定済み <span className="num font-mono" aria-label={`末尾 ${p.key_hint}`}>••••{p.key_hint}</span></p>
                <button type="button" onClick={() => setEditingKey(true)} disabled={busy} className={smallButton}>変更</button>
                <button type="button" onClick={() => setRemoving(true)} disabled={busy}
                  className={`${smallButton} text-red-600 dark:text-red-400`}>削除</button>
              </div>
            ) : (
              <form onSubmit={submitKey}>
                <div className="flex gap-2">
                  <input id={`${panelId}-key`} type="password" value={key} onChange={(e) => setKey(e.target.value)}
                    autoComplete="off" autoCapitalize="off" autoCorrect="off" spellCheck={false}
                    placeholder={p.configured ? "新しいキー" : "キーを貼り付け"} className={`${field} font-mono`} />
                  <button type="submit" disabled={busy || !key.trim()} className={smallButton}>{busy ? "保存中…" : "保存"}</button>
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-x-3">
                  <p className="min-w-0 flex-1 text-[11px] text-zinc-500">暗号化して保存します。保存後は末尾 4 文字しか表示しません。</p>
                  {editingKey && <button type="button" onClick={() => { setEditingKey(false); setKey(""); }} className="min-h-11 text-xs text-zinc-600 underline dark:text-zinc-300">変更をやめる</button>}
                </div>
              </form>
            )}
          </div>

          <Toggle label="このプロバイダを有効にする" hint={enableBlocked || "切ると、キーを残したまま使えなくします"}
            value={p.enabled} disabled={busy || (!p.enabled && !!enableBlocked)}
            onChange={(v) => save.mutate({ enabled: v }, { onSuccess: () => show(v ? `${p.name} を有効にしました` : `${p.name} を無効にしました`) })} />

          {(p.enabled || p.models.length > 0) && (
            <div className="text-xs">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="font-medium">使うモデル<span className="num ml-1 font-normal text-zinc-500">{p.models.length} 件</span></p>
                <button type="button" onClick={() => setPicking(true)} disabled={!p.enabled || busy} className={smallButton}>モデルを選ぶ</button>
              </div>
              {p.models.length === 0 ? (
                <p className="text-[11px] text-zinc-500">選んだモデルが OpenCode / AI アシスタントのモデル一覧に並びます。</p>
              ) : (
                <ul className="mt-1 space-y-1" aria-label={`${p.name} で使うモデル`}>
                  {p.models.map((m) => {
                    const gatewayId = cloudGatewayId(p.id, m);
                    return (
                      <li key={m} className="flex items-center gap-1 rounded-lg bg-white pl-2.5 dark:bg-zinc-900">
                        <span className="min-w-0 flex-1 break-all py-1.5 font-mono text-[11px] leading-snug">{gatewayId}</span>
                        <button type="button" onClick={() => copy(gatewayId)} aria-label={`${gatewayId} をコピー`}
                          className="min-h-11 shrink-0 rounded-lg px-2 text-[11px] text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800">コピー</button>
                        <button type="button" disabled={busy} aria-label={`${m} を外す`}
                          onClick={() => save.mutate({ models: p.models.filter((x) => x !== m) }, { onSuccess: () => show(`${m} を外しました`) })}
                          className="flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-lg text-zinc-500 hover:bg-zinc-100 disabled:opacity-40 dark:hover:bg-zinc-800"><IconX className="h-4 w-4" /></button>
                      </li>
                    );
                  })}
                </ul>
              )}
              {!p.enabled && <p className="mt-1 text-[11px] text-zinc-500">無効のあいだは、これらのモデルは一覧に出ません。</p>}
            </div>
          )}

          {p.configured && (
            <form onSubmit={submitBudget} className="text-xs">
              <label htmlFor={`${panelId}-budget`} className="font-medium">月の上限（USD）</label>
              <div className="mt-1 flex gap-2">
                <input id={`${panelId}-budget`} type="number" inputMode="decimal" min={0} max={100000} step="any" value={budget}
                  onChange={(e) => setBudget(e.target.value)} placeholder="上限なし" className={`${field} num`} />
                <button type="submit" disabled={busy || !budgetValid || budgetValue === p.monthly_budget_usd} className={smallButton}>保存</button>
              </div>
              <p className="num mt-1.5 flex flex-wrap justify-between gap-x-2 text-[11px] text-zinc-600 dark:text-zinc-300">
                <span>今月の使用 {usd(p.spent_this_month_usd)}</span>
                <span className="text-zinc-500">{p.monthly_budget_usd > 0 ? `上限 ${usd(p.monthly_budget_usd)}（${Math.round(ratio * 100)}%）` : "上限なし"}</span>
              </p>
              {p.monthly_budget_usd > 0 && (
                <>
                  <div role="progressbar" aria-label={`${p.name} の今月の使用`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.min(100, Math.round(ratio * 100))}
                    className="mt-1 h-1.5 overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-700">
                    <div className={`h-full rounded-full ${ratio >= 1 ? "bg-red-500" : ratio >= 0.8 ? "bg-amber-500" : "bg-accent-500"}`}
                      style={{ width: `${Math.min(100, ratio * 100)}%` }} />
                  </div>
                  {ratio >= 1 && <p className="mt-1 text-[11px] text-red-600 dark:text-red-400">上限に達したため、今月はこのプロバイダへの送信を止めています。</p>}
                </>
              )}
            </form>
          )}
        </div>
      )}
      {removing && (
        <ConfirmDialog title={`${p.name} の API キーを削除しますか？`}
          message="保存したキーを消し、このプロバイダを無効にします。選んだモデルと上限の設定は残ります。"
          confirmLabel="削除する" busy={busy} onClose={() => setRemoving(false)}
          onConfirm={() => save.mutate({ api_key: "" }, {
            onSuccess: () => { setRemoving(false); setEditingKey(false); setKey(""); show(`${p.name} の API キーを削除しました`); },
            onError: () => setRemoving(false),
          })} />
      )}
      {picking && <ModelPickerSheet provider={p} onClose={() => setPicking(false)} />}
    </li>
  );
}

/** 使うモデルを選ぶ。OpenRouter は数千件あるので、絞り込んだ先頭だけを描く。 */
function ModelPickerSheet({ provider: p, onClose }: { provider: CloudProvider; onClose: () => void }) {
  const show = useToasts((s) => s.show);
  const save = useSaveProvider(p.id);
  const remote = useQuery({
    queryKey: ["cloud-remote-models", p.id], queryFn: () => listCloudRemoteModels(p.id),
    staleTime: 5 * 60_000, retry: false,
  });
  const [selected, setSelected] = useState<Set<string>>(() => new Set(p.models));
  const [query, setQuery] = useState("");
  const [freeOnly, setFreeOnly] = useState(false);
  const [selectedOnly, setSelectedOnly] = useState(false);
  const [limit, setLimit] = useState(PAGE);
  const deferredQuery = useDeferredValue(query);

  // 選んであるのに一覧に無いもの（提供終了・手入力）も、外せるように先頭へ並べる。
  const all = useMemo<CloudRemoteModel[]>(() => {
    const list = remote.data?.models ?? [];
    const known = new Set(list.map((m) => m.id));
    const extra = [...selected].filter((id) => !known.has(id)).map((id) => ({ id, name: id, context_length: null, free: false }));
    return [...extra, ...list];
    // 並びは開いたときと読み込み後だけ決める。チェックのたびに行が動かないように selected は見ない。
  }, [remote.data]);
  const hasFree = p.id === "openrouter" || all.some((m) => m.free);
  const matches = useMemo(() => {
    const words = deferredQuery.trim().toLowerCase().split(/\s+/).filter(Boolean);
    return all.filter((m) => {
      if (freeOnly && !m.free) return false;
      if (selectedOnly && !selected.has(m.id)) return false;
      if (words.length === 0) return true;
      const text = `${m.id} ${m.name}`.toLowerCase();
      return words.every((w) => text.includes(w));
    });
  }, [all, deferredQuery, freeOnly, selectedOnly, selected]);
  const typed = query.trim();
  const canAddTyped = MODEL_ID.test(typed) && !all.some((m) => m.id === typed) && !selected.has(typed);
  const full = selected.size >= MAX_MODELS;
  const toggle = (id: string, on: boolean) => setSelected((current) => {
    const next = new Set(current);
    if (on) next.add(id); else next.delete(id);
    return next;
  });
  const dirty = selected.size !== p.models.length || p.models.some((m) => !selected.has(m));
  const submit = () => save.mutate({ models: [...selected] }, {
    onSuccess: () => { show(`${p.name} で使うモデルを保存しました（${selected.size} 件）`); onClose(); },
  });

  return (
    <BottomSheet title={`${p.name} のモデルを選ぶ`} onClose={onClose} sideOnDesktop stable>
      <div className="sticky top-0 z-10 -mx-5 space-y-2 bg-white px-5 pb-2 dark:bg-zinc-900">
        <label className="relative block">
          <span className="sr-only">モデルを検索</span>
          <IconSearch className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-zinc-400" aria-hidden />
          <input type="search" value={query} onChange={(e) => { setQuery(e.target.value); setLimit(PAGE); }}
            placeholder="名前や ID で検索" autoComplete="off" spellCheck={false} className={`${field} pl-9`} />
        </label>
        <div className="flex flex-wrap items-center gap-x-4 text-xs">
          {hasFree && (
            <label className="flex min-h-11 items-center gap-2">
              <input type="checkbox" checked={freeOnly} onChange={(e) => { setFreeOnly(e.target.checked); setLimit(PAGE); }} className="h-5 w-5" />無料だけ
            </label>
          )}
          <label className="flex min-h-11 items-center gap-2">
            <input type="checkbox" checked={selectedOnly} onChange={(e) => { setSelectedOnly(e.target.checked); setLimit(PAGE); }} className="h-5 w-5" />選択中だけ
          </label>
          <span className="num ml-auto text-[11px] text-zinc-500" aria-live="polite">
            {remote.data ? `${matches.length.toLocaleString()} 件` : ""} · 選択 {selected.size}/{MAX_MODELS}
          </span>
        </div>
      </div>

      {remote.isLoading && (
        <div className="space-y-2" aria-busy="true">
          <p className="text-xs text-zinc-500">モデルの一覧を取得しています（数秒かかることがあります）</p>
          {[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-11" />)}
        </div>
      )}
      {remote.isError && (
        <div className="mb-2 flex flex-wrap items-center gap-2 rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:bg-amber-950/40 dark:text-amber-100">
          <p className="min-w-0 flex-1">一覧を取得できませんでした: {errorText(remote.error, "不明なエラー")}。モデルの ID を検索欄に入れて、そのまま追加することはできます。</p>
          <button type="button" onClick={() => remote.refetch()} className={smallButton}>再試行</button>
        </div>
      )}
      {!remote.isLoading && (
        <>
          <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
            {matches.slice(0, limit).map((m) => {
              const checked = selected.has(m.id);
              const ctx = contextLabel(m.context_length);
              return (
                <li key={m.id}>
                  <label className={`flex min-h-11 items-start gap-3 py-2 ${!checked && full ? "opacity-50" : "cursor-pointer"}`}>
                    <input type="checkbox" checked={checked} disabled={!checked && full} onChange={(e) => toggle(m.id, e.target.checked)}
                      className="mt-0.5 h-5 w-5 shrink-0" />
                    <span className="min-w-0 flex-1">
                      {m.name && m.name !== m.id && <span className="block break-words text-sm">{m.name}</span>}
                      <span className="block break-all font-mono text-[11px] text-zinc-600 dark:text-zinc-300">{m.id}</span>
                    </span>
                    <span className="num flex shrink-0 flex-col items-end gap-0.5 text-[11px] text-zinc-500">
                      {ctx && <span title="コンテキスト長">{ctx}</span>}
                      {m.free && <span className="rounded bg-emerald-100 px-1.5 py-0.5 font-medium text-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300">無料</span>}
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>
          {matches.length > limit && (
            <button type="button" onClick={() => setLimit(limit + PAGE)} className={`${smallButton} mt-2 w-full`}>
              さらに表示（残り {(matches.length - limit).toLocaleString()} 件）
            </button>
          )}
          {matches.length === 0 && !remote.isError && (
            <p className="py-6 text-center text-xs text-zinc-500">{all.length === 0 ? "モデルがありません" : "条件に合うモデルがありません"}</p>
          )}
          {canAddTyped && (
            <button type="button" disabled={full} onClick={() => { toggle(typed, true); setQuery(""); show(`${typed} を選択に加えました`, "info"); }}
              className={`${smallButton} mt-2 w-full break-all py-2`}>
              「{typed}」を ID のまま追加
            </button>
          )}
        </>
      )}
      <SheetActions note={full ? `選べるのは ${MAX_MODELS} 件までです` : undefined}>
        <button type="button" onClick={onClose} className={secondaryButton}>キャンセル</button>
        <button type="button" onClick={submit} disabled={save.isPending || !dirty} className={primaryButton}>
          {save.isPending ? "保存中…" : `${selected.size} 件を保存`}
        </button>
      </SheetActions>
    </BottomSheet>
  );
}

/** 呼び出し元ごとの許可。オンにするときだけ確かめる（オフは安全側なので即時）。 */
function EgressBlock({ state }: { state: CloudState }) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const [confirming, setConfirming] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (patch: Record<string, boolean>) => saveCloudEgress(patch),
    onSuccess: (next, patch) => {
      qc.setQueryData(CLOUD_QUERY_KEY, next);
      qc.invalidateQueries({ queryKey: ["llm-endpoints"] });
      const [caller, on] = Object.entries(patch)[0];
      const label = next.caller_labels[caller] ?? caller;
      show(on ? `${label} からの外部への送信を許可しました` : `${label} からの外部への送信を止めました`);
      setConfirming(null);
    },
    onError: (e) => { show(errorText(e, "保存できませんでした"), "error"); setConfirming(null); },
  });
  const label = (caller: string) => state.caller_labels[caller] ?? caller;
  return (
    <section aria-labelledby="cloud-egress-title" className="space-y-2">
      <h4 id="cloud-egress-title" className="text-sm font-semibold">外部への送信</h4>
      <p className="rounded-xl bg-amber-50 px-3 py-2.5 text-[11px] leading-relaxed text-amber-900 dark:bg-amber-950/40 dark:text-amber-100">
        オンにした呼び出し元が外部のモデルを選ぶと、<span className="font-semibold">プロンプト・会話・コードがそのプロバイダへ送信されます</span>。未公開の特許など機密の作業では、オフのままにするかローカルのモデルを使ってください。
      </p>
      {Object.keys(state.egress).map((caller) => (
        <Toggle key={caller} label={label(caller)} hint={CALLER_SENDS[caller] ? `送られるもの: ${CALLER_SENDS[caller]}` : undefined}
          value={state.egress[caller]} disabled={save.isPending}
          onChange={(v) => (v ? setConfirming(caller) : save.mutate({ [caller]: false }))} />
      ))}
      <p className="text-[11px] leading-relaxed text-zinc-500">
        ローカル専用を求める Add-on（PatentBuddy など）は、この設定に関係なく外部へ送信されません。
      </p>
      {confirming && (
        <ConfirmDialog danger={false} title={`${label(confirming)} から外部へ送信できるようにしますか？`}
          message={`${label(confirming)} が外部のモデル（cloud: で始まる名前）を選んだとき、${CALLER_SENDS[confirming] ?? "プロンプトやコード"}がそのプロバイダへ送信されます。ローカルのモデルを使っているあいだは送信されません。`}
          confirmLabel="送信を許可する" busy={save.isPending}
          onConfirm={() => save.mutate({ [confirming]: true })} onClose={() => setConfirming(null)} />
      )}
    </section>
  );
}

/** 過去 30 日の使用量。開いたときだけ取りに行く。 */
function UsageBlock({ providers, callerLabels }: { providers: CloudProvider[]; callerLabels: Record<string, string> }) {
  const [open, setOpen] = useState(false);
  const usage = useQuery({ queryKey: ["cloud-usage", 30], queryFn: () => getCloudUsage(30), enabled: open });
  const rows = usage.data?.rows ?? [];
  const name = (id: string) => providers.find((p) => p.id === id)?.name ?? id;
  const totals = useMemo(() => {
    const byProvider = new Map<string, { calls: number; failed: number; tokens: number; cost: number; unpriced: number }>();
    for (const row of rows) {
      const t = byProvider.get(row.provider) ?? { calls: 0, failed: 0, tokens: 0, cost: 0, unpriced: 0 };
      t.calls += 1;
      if (row.status >= 400) t.failed += 1;
      t.tokens += row.prompt_tokens + row.completion_tokens;
      if (row.cost_usd === null) t.unpriced += 1; else t.cost += row.cost_usd;
      byProvider.set(row.provider, t);
    }
    return [...byProvider.entries()];
  }, [rows]);
  const unpriced = totals.some(([, t]) => t.unpriced > 0);
  return (
    <details className="group/usage rounded-xl bg-zinc-50 dark:bg-zinc-800/50" onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary className="flex min-h-11 cursor-pointer list-none items-center gap-2 px-3 text-sm font-semibold">
        <span className="flex-1">使用量<span className="ml-1 text-[11px] font-normal text-zinc-500">過去 30 日</span></span>
        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" className="text-zinc-400 transition-transform group-open/usage:rotate-90" aria-hidden><path d="m9 6 6 6-6 6" /></svg>
      </summary>
      <div className="space-y-3 px-3 pb-3 text-xs">
        {usage.isLoading ? <Skeleton className="h-16" />
          : usage.isError ? (
            <div className="flex flex-wrap items-center gap-2 text-zinc-600 dark:text-zinc-300">
              <p className="min-w-0 flex-1">使用量を読み込めませんでした: {errorText(usage.error, "不明なエラー")}</p>
              <button type="button" onClick={() => usage.refetch()} className={smallButton}>再試行</button>
            </div>
          ) : rows.length === 0 ? <p className="text-zinc-500">まだ外部への呼び出しはありません。</p>
            : (
              <>
                <ul className="space-y-1.5" aria-label="プロバイダごとの合計">
                  {totals.map(([id, t]) => (
                    <li key={id} className="flex flex-wrap items-baseline justify-between gap-x-3">
                      <span className="font-medium">{name(id)}</span>
                      <span className="num text-zinc-600 dark:text-zinc-300">
                        {t.calls.toLocaleString()} 回{t.failed > 0 ? `（失敗 ${t.failed.toLocaleString()}）` : ""} · {t.tokens.toLocaleString()} tok · {t.unpriced === t.calls ? "費用 —" : usd(t.cost)}
                      </span>
                    </li>
                  ))}
                </ul>
                {unpriced && <p className="text-[11px] text-zinc-500">費用は、プロバイダが金額を返した呼び出しだけの合計です。</p>}
                <div>
                  <p className="mb-1 font-medium">最近の呼び出し</p>
                  <ul className="divide-y divide-zinc-200 dark:divide-zinc-700">
                    {rows.slice(0, 20).map((row, i) => <UsageRow key={`${row.at}-${i}`} row={row} caller={callerLabels[row.caller] ?? row.caller} />)}
                  </ul>
                  {rows.length > 20 && <p className="num mt-1 text-[11px] text-zinc-500">ほか {(rows.length - 20).toLocaleString()} 件</p>}
                </div>
                <p className="text-[11px] text-zinc-500">記録するのは日時・呼び出し元・モデル・トークン数・費用だけです。本文は記録しません。</p>
              </>
            )}
      </div>
    </details>
  );
}

function UsageRow({ row, caller }: { row: CloudUsageRow; caller: string }) {
  const failed = row.status >= 400;
  const when = new Date(row.at * 1000).toLocaleString("ja-JP", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
  return (
    <li className="py-1.5">
      <p className="break-all font-mono text-[11px]">{row.provider}/{row.model}</p>
      <p className="num flex flex-wrap gap-x-2 text-[11px] text-zinc-500">
        <span>{when}</span>
        <span>{caller}</span>
        <span>{row.prompt_tokens.toLocaleString()} → {row.completion_tokens.toLocaleString()} tok</span>
        <span>{row.cost_usd === null ? "費用 —" : usd(row.cost_usd)}</span>
        <span className={failed ? "text-red-600 dark:text-red-400" : ""}>{failed ? `失敗（${row.status}）` : "成功"}</span>
      </p>
    </li>
  );
}
