/** 設定 → 開発（作業場）。AI 開発まわりの設定へ 1 か所から届く入口（ROADMAP U10）。
 *
 * 新しい設定値は持たない。すでにある設定・API をここに並べるだけ:
 * - 編集できる: 隔離（isolation）・自動レビュー（auto_review）・AI アシスタントの権限モード・ディスクの下限。
 *   保存先は OpenCode の設定（/opencode/workflow・/opencode/settings・/chat/opencode/permission-mode）で、
 *   OpenCode 画面の「開発ワークフロー」と同じ値。
 * - 要約とリンク: 役割とモデル（編集は OpenCode 画面）・開発対象・関門・自動保存・取り込み先（どれも作業場の決まりで、設定値ではない）。 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import { useMeta } from "../../api/hooks";
import { useAuth, useToasts } from "../../stores";
import { Toggle } from "../models/formControls";
import { ROLE_LABELS } from "../opencode/RolesPanel";
import { workflowQueryKey } from "../opencode/FeatureSetup";
import type { Workflow } from "../opencode/WorkflowExtensions";
import { TARGETS } from "../workflows/workspaceTargets";

interface OpenCodeStatus { settings: { base_url: string; model: string; project_path: string; runtime: string; workspace_min_free_gib?: number } }
type PermissionMode = "auto" | "ask";

const card = "rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900 md:p-5";
const sub = "mt-4 border-t border-zinc-100 pt-4 dark:border-zinc-800";
const linkClass = "text-accent-600 underline dark:text-accent-400";

export function DevWorkspaceSettings() {
  const can = useAuth((s) => s.can);
  const editable = can("settings.manage");
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const { data: meta } = useMeta();
  const opencodePath = meta?.enabled_features.includes("opencode-v2") ? "/opencode-v2" : null;
  const workflow = useQuery({ queryKey: workflowQueryKey, queryFn: () => api<Workflow>("/opencode/workflow"), staleTime: 10_000 });
  const status = useQuery({ queryKey: ["opencode-status", "v2"], queryFn: () => api<OpenCodeStatus>("/opencode/status?runtime=v2"), staleTime: 30_000 });
  const mode = useQuery({ queryKey: ["opencode-permission-mode"], queryFn: () => api<{ mode: PermissionMode }>("/chat/opencode/permission-mode") });

  const saveWorkflow = useMutation({
    mutationFn: (patch: Partial<Workflow>) => api<Workflow>("/opencode/workflow", { method: "PUT", json: patch }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: workflowQueryKey }); show("保存しました。次に開く作業から効きます"); },
    onError: (error) => show(error instanceof Error ? error.message : "保存できませんでした", "error"),
  });
  const saveMode = useMutation({
    mutationFn: (next: PermissionMode) => api("/chat/opencode/permission-mode", { method: "PUT", json: { mode: next } }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["opencode-permission-mode"] }); show("保存しました。次の操作から効きます"); },
    onError: (error) => show(error instanceof Error ? error.message : "保存できませんでした", "error"),
  });
  // ディスクの下限は OpenCode の設定（/opencode/settings）に入っている。その PUT は接続先・モデルも要るので、読んだ値をそのまま添える
  const saved = status.data?.settings.workspace_min_free_gib ?? 15;
  const [minFree, setMinFree] = useState("15");
  useEffect(() => { setMinFree(String(saved)); }, [saved]);
  const minFreeNumber = Number(minFree);
  const minFreeValid = minFree.trim() !== "" && Number.isFinite(minFreeNumber) && minFreeNumber >= 0 && minFreeNumber <= 4096;
  const saveMinFree = useMutation({
    mutationFn: () => {
      const s = status.data!.settings;
      return api("/opencode/settings", { method: "PUT", json: { base_url: s.base_url, model: s.model, project_path: s.project_path, runtime: s.runtime, workspace_min_free_gib: minFreeNumber } });
    },
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["opencode-status"] }); show("ディスクの下限を保存しました"); },
    onError: (error) => show(error instanceof Error ? error.message : "保存できませんでした", "error"),
  });

  const w = workflow.data;
  const roles = w?.roles ?? {};
  const setRoles = ROLE_LABELS.filter(([key]) => roles[key]);
  const defaultModel = status.data?.settings.model;
  const isolation = w?.isolation ?? "box";
  const opencodeLink = (text: string) => opencodePath ? <Link to={opencodePath} className={linkClass}>{text}</Link> : <span>{text}（OpenCode が未導入です）</span>;

  return (
    <section id="dev-workspace" className={card} data-dev-workspace-settings>
      <h2 className="text-sm font-semibold">開発（作業場）</h2>
      <p className="mt-1 text-xs text-zinc-500">AI アシスタントの開発（作業場）まわりの設定を 1 か所にまとめています。OpenCode 画面の「開発ワークフロー」と同じ値です。</p>

      <div className={sub}>
        <h3 className="text-sm font-semibold">作り方（編集できます）</h3>
        {workflow.isError && <p className="mt-2 text-xs text-zinc-500">OpenCode の設定を読めませんでした。OpenCode が導入済みか確かめてください。</p>}
        {w && (
          <div className="mt-2 space-y-2">
            <Toggle label="プロジェクトの箱で動かす（推奨）" value={isolation === "box"} disabled={!editable || saveWorkflow.isPending}
              hint={w.isolation_effective === "host" && isolation === "box" ? "この PC に podman が無いため、いまは箱を使わず手元で動いています" : "AI が書けるのはプロジェクトの中だけになります"}
              onChange={(v) => saveWorkflow.mutate({ isolation: v ? "box" : "host" })} />
            <Toggle label="自動レビュー" value={w.auto_review ?? true} disabled={!editable || saveWorkflow.isPending}
              hint="コードを変えたら、完了の前に子エージェントで確かめます"
              onChange={(v) => saveWorkflow.mutate({ auto_review: v })} />
            <label className="block rounded-xl border border-zinc-200 px-3 py-2 text-xs dark:border-zinc-700">AI アシスタントの権限モード
              <span className="block text-[11px] text-zinc-500">自動: 編集・読む・テストは任せ、危ない操作だけ確認 / 毎回確認: シェルや編集もすべて確認</span>
              <select aria-label="権限モード" value={mode.data?.mode ?? "auto"} disabled={!editable || saveMode.isPending || !mode.data}
                onChange={(e) => saveMode.mutate(e.target.value as PermissionMode)}
                className="mt-1 min-h-11 w-full rounded-xl border border-zinc-300 bg-white px-3 text-sm disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900">
                <option value="auto">自動（危ない操作だけ確認）</option>
                <option value="ask">毎回確認</option>
              </select>
            </label>
          </div>
        )}
        {!editable && <p className="mt-2 text-xs text-amber-700 dark:text-amber-300">変更には管理者権限が必要です。</p>}
      </div>

      <div className={sub}>
        <h3 className="text-sm font-semibold">ディスクの下限と自動保存</h3>
        <p className="mt-1 text-xs text-zinc-500">作業場は、ターンの終わりごとに自動で保存されます（常に有効）。ディスクの空きが下限を下回ると、新しい作業場を作らず、自動保存も飛ばします（会話に「保存できません」と出ます）。下限の 2 倍に満たないときは注意が出ます。</p>
        <label className="mt-2 block text-xs">空きの下限（GiB）
          <div className="mt-1 flex flex-wrap gap-2">
            <input aria-label="ディスクの空きの下限" type="number" inputMode="decimal" min={0} max={4096} value={minFree} disabled={!editable || !status.data}
              onChange={(e) => setMinFree(e.target.value)}
              className="min-h-11 w-32 rounded-xl border border-zinc-300 bg-white px-3 text-sm disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900" />
            <button type="button" disabled={!editable || !minFreeValid || minFreeNumber === saved || saveMinFree.isPending} onClick={() => saveMinFree.mutate()}
              className="min-h-11 rounded-xl bg-accent-600 px-4 text-sm font-medium text-white disabled:opacity-40">{saveMinFree.isPending ? "保存中…" : "保存"}</button>
          </div>
        </label>
        {!minFreeValid && <p className="mt-1 text-xs text-red-600 dark:text-red-400">0〜4096 の数を入れてください。</p>}
      </div>

      <div className={sub}>
        <h3 className="text-sm font-semibold">役割とモデル</h3>
        <p className="mt-1 text-xs text-zinc-500">
          {defaultModel ? `既定のモデル: ${defaultModel}。` : ""}
          {setRoles.length ? "役割ごとの指定: " : "役割ごとの指定はありません（すべて既定のモデルで動きます）。"}
        </p>
        {setRoles.length > 0 && (
          <ul className="mt-1 space-y-0.5 text-xs">
            {setRoles.map(([key, label]) => <li key={key} className="flex gap-2"><span className="w-20 shrink-0 text-zinc-500">{label}</span><span className="min-w-0 break-all">{roles[key]}</span></li>)}
          </ul>
        )}
        <p className="mt-2 text-xs">編集は {opencodeLink("OpenCode 画面の「役割とモデル」")} で行います。</p>
      </div>

      <div className={sub}>
        <h3 className="text-sm font-semibold">開発対象・関門・取り込み先</h3>
        <p className="mt-1 text-xs text-zinc-500">作業場を作るとき AI アシスタントの「＋」→「開発対象」で選びます。次は作業場の決まりで、設定では変えません。</p>
        <ul className="mt-2 space-y-2">
          {TARGETS.map((t) => (
            <li key={t.id} className="rounded-xl border border-zinc-200 px-3 py-2 text-xs dark:border-zinc-700">
              <span className="font-medium">{t.label}</span>
              <span className="block text-zinc-500">{t.note}</span>
              <span className="block text-zinc-500">取り込み先: {t.id === "controldeck" ? "GitHub の PR（main への取り込み）。マージは人が行います" : "元のリポジトリへ、ブランチとして（PR は作りません）"}</span>
            </li>
          ))}
        </ul>
        <p className="mt-2 text-xs text-zinc-500">関門: 取り込む前に <code>./deck.sh dev-check</code> と同じ検査を作業場の中で流し、合格して、その後に変更が無いときだけ取り込めます。main が進んでいれば取り込みの前に main を取り込み、ぶつかれば止まります。</p>
      </div>
    </section>
  );
}
