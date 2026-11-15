/** オプション機能の画面での初期設定（OpenCode の拡張・作業の道具）。
 *
 * 導入しただけでは使われない——OpenCode の設定で「使う」を選ぶまで session には載らない。
 * 導入した画面のその場で選べるようにし、UI 検証は接続診断まで済ませられるようにする。
 * レビュー・相談のモデルなど細かい選択は OpenCode 画面の設定に残す。 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import { useToasts } from "../../stores";
import { Toggle } from "../models/formControls";
import { UiVerifyDiagnosis, type ExtensionKey, type Workflow } from "./WorkflowExtensions";

/** オプション機能の id → OpenCode の設定の項目。 */
export const OPENCODE_SETUP_KEYS: Record<string, ExtensionKey> = {
  "opencode-superpowers": "superpowers",
  "opencode-adversarial-review": "adversarial_review",
  "opencode-auto-advisor": "auto_advisor",
  "playwright-mcp": "ui_verify",
};

export const workflowQueryKey = ["opencode-workflow"];

export function useOpenCodeWorkflow(enabled: boolean) {
  return useQuery({
    queryKey: workflowQueryKey,
    queryFn: () => api<Workflow>("/opencode/workflow"),
    enabled,
    staleTime: 10_000,
  });
}

/** 導入済みなのに OpenCode で使う設定がオフ（初期設定が済んでいない）か。 */
export function setupPending(featureId: string, workflow: Workflow | undefined): boolean {
  const key = OPENCODE_SETUP_KEYS[featureId];
  if (!key || !workflow) return false;
  if (key === "auto_advisor") return workflow.auto_advisor === "off";
  return !workflow[key];
}

export function OpenCodeFeatureSetup({ featureId }: { featureId: string }) {
  const key = OPENCODE_SETUP_KEYS[featureId];
  const qc = useQueryClient();
  const show = useToasts((state) => state.show);
  const { data: workflow, isPending, isError } = useOpenCodeWorkflow(Boolean(key));
  const save = useMutation({
    mutationFn: (patch: Partial<Workflow>) => api<Workflow>("/opencode/workflow", { method: "PUT", json: patch }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: workflowQueryKey });
      show("保存しました。次に開く OpenCode のセッションから効きます");
    },
    onError: (error) => show(error instanceof Error ? error.message : "保存できませんでした", "error"),
  });
  if (!key) return null;
  if (isPending) return <p className="text-xs text-zinc-500">OpenCode の設定を確認中…</p>;
  if (isError || !workflow) return <p className="text-xs text-zinc-500">OpenCode の設定を読めませんでした。OpenCode v2 が導入済みか確かめてください。</p>;
  const busy = save.isPending;
  return (
    <div className="space-y-2 rounded-xl bg-zinc-50 p-3 dark:bg-zinc-800/40">
      <p className="text-xs font-semibold">初期設定</p>
      {key === "auto_advisor" ? (
        <label className="block text-xs">OpenCode で使う
          <select aria-label="Auto Advisor の使い方" value={workflow.auto_advisor} disabled={busy}
            onChange={(e) => save.mutate({ auto_advisor: e.target.value as Workflow["auto_advisor"] })}
            className="mt-1 min-h-11 w-full rounded-xl border border-zinc-300 bg-white px-3 text-sm disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900">
            <option value="off">使わない</option>
            <option value="observe">記録だけ（相談はしない）</option>
            <option value="active">必要な場面で相談する</option>
          </select>
        </label>
      ) : (
        <Toggle label="OpenCode で使う"
          hint={key === "ui_verify" ? "子エージェント ui-verifier が、開発中の画面を実ブラウザで確かめます" : "ControlDeck から開く OpenCode のセッションに足します"}
          value={Boolean(workflow[key])} disabled={busy}
          onChange={(v) => save.mutate({ [key]: v })} />
      )}
      {key === "ui_verify" && workflow.browser === false && (
        <p className="rounded-lg bg-amber-50 px-2 py-1.5 text-[11px] text-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
          このPCに Google Chrome が見つかりません。UI 検証は Chrome を使います（ブラウザを別に取得しません）。
        </p>
      )}
      {key === "ui_verify" && workflow.ui_verify && (
        <>
          <Toggle label="外部サイトへの通信を許す"
            hint="既定では localhost だけに届き、ControlDeck 自身は開けません"
            value={Boolean(workflow.ui_verify_allow_external)} disabled={busy}
            onChange={(v) => save.mutate({ ui_verify_allow_external: v })} />
          <UiVerifyDiagnosis />
        </>
      )}
      <p className="text-[11px] text-zinc-500">
        レビュー・相談に使うモデルなどは <Link to="/opencode" className="text-accent-600 underline dark:text-accent-400">OpenCode の設定</Link> で選べます。
      </p>
    </div>
  );
}
