import { useMemo } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { addonLabel, useEffectiveAddons, type AddonHealthAction } from "../api/addons";
import { PageHeader } from "../components/PageHeader";
import { IconGrid } from "../components/icons";
import { AddonStatusChip, addonStateMessage } from "../features/addons/AddonStatus";
import { useAuth, useToasts } from "../stores";

export default function AddonHostPage() {
  const { addonId = "", viewId = "" } = useParams();
  const [search] = useSearchParams();
  const navigate = useNavigate();
  const can = useAuth((state) => state.can);
  const show = useToasts((state) => state.show);
  const queryClient = useQueryClient();
  const { data, isLoading } = useEffectiveAddons();
  const addon = data?.addons.find((item) => item.id === addonId);
  const contributions = useMemo(() => Object.entries(data?.contributions ?? {}).flatMap(([kind, values]) =>
    values.filter((value) => value.addon_id === addonId).map((value) => ({ kind, ...value }))), [addonId, data]);

  const runAction = async (action: AddonHealthAction | null | undefined) => {
    if (!action) return;
    if (action.kind === "open_route" && action.route) return navigate(action.route);
    if (action.kind === "open_logs") return navigate("/logs");
    if (action.kind === "documentation") return navigate(`/settings?extension=${encodeURIComponent(addonId)}`);
    if (action.kind === "disable" && can("settings.manage")) {
      await api(`/addons/${addonId}/disable`, { method: "POST" });
    } else if (action.kind === "retry" && can("settings.manage")) {
      await api(`/addons/${addonId}/recheck`, { method: "POST" });
    } else {
      return navigate(`/settings?extension=${encodeURIComponent(addonId)}`);
    }
    await queryClient.invalidateQueries({ queryKey: ["addons-effective"] });
    show(action.kind === "disable" ? "拡張機能を無効化しました" : "状態を再確認しました");
  };

  if (isLoading) return <div className="p-6 text-sm text-zinc-400">拡張機能を確認しています…</div>;
  if (!addon) return <MissingAddon addonId={addonId} />;

  const requested = search.get("command") || search.get("action");
  return (
    <div className="mx-auto max-w-4xl space-y-5 p-4 md:p-6">
      <PageHeader title={addon.name} description="Control Deckが描画する拡張機能の状態ページ" />
      <section className="rounded-2xl border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900">
        <div className="flex items-start gap-3">
          <div className="grid h-11 w-11 shrink-0 place-items-center rounded-2xl bg-accent-50 text-accent-700 dark:bg-accent-600/15 dark:text-accent-300"><IconGrid /></div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2"><h2 className="font-semibold">{addon.name}</h2><AddonStatusChip state={addon.state} /></div>
            <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">{addon.health?.message || addonStateMessage(addon.state)}</p>
            {requested && <p className="mt-2 rounded-xl bg-zinc-50 px-3 py-2 text-xs text-zinc-500 dark:bg-zinc-950">選択した操作: {requested}。拡張画面との接続はHost Bridge導入後にこの場所で実行されます。</p>}
          </div>
        </div>

        {addon.health?.setup && addon.health.setup.length > 0 && <div className="mt-5 space-y-2">
          <h3 className="text-xs font-semibold text-zinc-500">セットアップ</h3>
          {addon.health.setup.map((item) => <div key={item.id} className="flex items-start gap-3 rounded-xl border border-zinc-200 p-3 dark:border-zinc-700">
            <span className={`mt-1 h-2.5 w-2.5 shrink-0 rounded-full ${item.state === "ok" ? "bg-emerald-500" : item.state === "checking" ? "bg-sky-500" : "bg-amber-500"}`} />
            <div className="min-w-0 flex-1"><p className="text-sm font-medium">{item.label}</p><p className="text-xs text-zinc-400">{item.detail || item.message || item.state}</p></div>
            {item.action && <button onClick={() => void runAction(item.action)} className="min-h-10 rounded-xl bg-zinc-100 px-3 text-xs font-medium dark:bg-zinc-800">対応する</button>}
          </div>)}
        </div>}

        <div className="mt-5 flex flex-wrap gap-2">
          {addon.health?.action && <button onClick={() => void runAction(addon.health?.action)} className="min-h-11 rounded-xl bg-accent-600 px-4 text-sm font-semibold text-white">{addon.health.action.kind === "retry" ? "再確認" : "対応する"}</button>}
          {can("settings.manage") && <button onClick={() => navigate(`/settings?extension=${encodeURIComponent(addon.id)}`)} className="min-h-11 rounded-xl border border-zinc-200 px-4 text-sm font-medium dark:border-zinc-700">権限・詳細を開く</button>}
          <button onClick={() => navigate("/")} className="min-h-11 rounded-xl px-4 text-sm font-medium text-zinc-500">ホームへ戻る</button>
        </div>
      </section>

      <section className="rounded-2xl border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900">
        <h2 className="text-sm font-semibold text-zinc-500">利用可能な機能</h2>
        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          {contributions.map((item) => <div key={`${item.kind}:${item.id}`} className="rounded-xl bg-zinc-50 p-3 dark:bg-zinc-950">
            <p className="truncate text-sm font-medium">{addonLabel(item.label)}</p><p className="mt-1 text-[11px] text-zinc-400">{item.kind} · {item.availability}</p>
          </div>)}
          {contributions.length === 0 && <p className="text-xs text-zinc-400">現在利用できる機能はありません。再確認または設定を開いてください。</p>}
        </div>
      </section>
      <p className="text-xs text-zinc-400">view: {viewId || "status"}</p>
    </div>
  );
}

function MissingAddon({ addonId }: { addonId: string }) {
  const navigate = useNavigate();
  return <div className="mx-auto max-w-xl p-4 md:p-6"><section className="rounded-2xl border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900"><h1 className="font-semibold">拡張機能を利用できません</h1><p className="mt-2 text-sm text-zinc-500">{addonId || "指定された拡張機能"} は無効、未登録、権限不足のいずれかです。</p><div className="mt-4 flex gap-2"><button onClick={() => navigate("/settings")} className="min-h-11 rounded-xl bg-accent-600 px-4 text-sm font-semibold text-white">設定を開く</button><button onClick={() => navigate("/")} className="min-h-11 rounded-xl px-4 text-sm">ホームへ戻る</button></div></section></div>;
}
