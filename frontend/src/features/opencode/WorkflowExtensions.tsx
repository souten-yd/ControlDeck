/** 開発ワークフローの拡張（Superpowers・Adversarial Review・Auto Advisor）。
 * ControlDeck から起動する OpenCode セッションにだけ効く。レビュー・相談に別のモデルを
 * 選ぶと、ゲートウェイが要求に合わせて載せ替える（例: Flash-Next で開発し Maya-S でレビュー）。 */
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { useAuth, useToasts } from "../../stores";
import { SettingsGroup, Toggle } from "../models/formControls";

interface Workflow {
  superpowers: boolean;
  adversarial_review: boolean;
  auto_advisor: "off" | "observe" | "active";
  review_model: string;
  advisor_model: string;
  models?: { alias: string; runtime: string }[];
}

export function WorkflowExtensions({ runtime }: { runtime: string }) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const can = useAuth((s) => s.can);
  const { data } = useQuery({ queryKey: ["opencode-workflow"], queryFn: () => api<Workflow>("/opencode/workflow") });
  const [form, setForm] = useState<Workflow | null>(null);
  useEffect(() => { if (data) setForm(data); }, [data]);
  const save = useMutation({
    mutationFn: (value: Workflow) => api<Workflow>("/opencode/workflow", {
      method: "PUT",
      json: { superpowers: value.superpowers, adversarial_review: value.adversarial_review,
        auto_advisor: value.auto_advisor, review_model: value.review_model, advisor_model: value.advisor_model },
    }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["opencode-workflow"] }); show("開発ワークフローを保存しました。次に開くセッションから反映されます"); },
    onError: (e) => show(e instanceof Error ? e.message : "保存できませんでした", "error"),
  });
  if (!form) return null;
  const editable = can("settings.manage");
  const field = "mt-1 min-h-11 w-full rounded-xl border border-zinc-300 bg-white px-3 text-sm disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900";
  const modelSelect = (label: string, key: "review_model" | "advisor_model", hint: string) => (
    <label className="block text-xs">{label}
      <select aria-label={label} value={form[key]} disabled={!editable}
        onChange={(e) => setForm({ ...form, [key]: e.target.value })} className={field}>
        <option value="">セッションと同じモデル</option>
        {(data?.models ?? []).map((m) => <option key={m.alias} value={m.alias}>{m.alias}{m.runtime ? `（${m.runtime}）` : ""}</option>)}
      </select>
      <span className="text-[10px] text-zinc-500">{hint}</span>
    </label>
  );
  return (
    <SettingsGroup title="開発ワークフロー" description="ControlDeck から開く OpenCode セッションに足す拡張。次に開くセッションから反映されます">
      <Toggle label="Superpowers" hint="計画→テスト駆動→実装→レビューの進め方をスキルとして使います（obra/superpowers v7.0.2）"
        value={form.superpowers} disabled={!editable} onChange={(v) => setForm({ ...form, superpowers: v })} />
      <Toggle label="Adversarial Review" hint="/adversarial-review と /constructive-review。会話を持たない別の文脈で差分を読み、出荷できない理由を探します"
        value={form.adversarial_review} disabled={!editable} onChange={(v) => setForm({ ...form, adversarial_review: v })} />
      {form.adversarial_review && modelSelect("レビューに使うモデル", "review_model",
        "例: 開発は Flash-Next、レビューは Maya-S。別のモデルを選ぶとレビューのたびに載せ替えます（約1分）")}
      <label className="block text-xs">Auto Advisor{runtime === "v1" && "（OpenCode v2 のみ）"}
        <select aria-label="Auto Advisor" value={form.auto_advisor} disabled={!editable}
          onChange={(e) => setForm({ ...form, auto_advisor: e.target.value as Workflow["auto_advisor"] })} className={field}>
          <option value="off">使わない</option>
          <option value="observe">記録だけ（相談はしない・しきい値の調整用）</option>
          <option value="active">必要な場面で相談する</option>
        </select>
        <span className="text-[10px] text-zinc-500">方針を決める・行き詰まる・完了を宣言する場面で、別のモデルに作業を見てもらいます。advisor() ツールでいつでも呼べます</span>
      </label>
      {form.auto_advisor !== "off" && modelSelect("相談先のモデル", "advisor_model",
        "要否の判断はセッションと同じモデルで行い、相談するときだけこのモデルへ載せ替えます")}
      {editable && (
        <button onClick={() => save.mutate(form)} disabled={save.isPending}
          className="min-h-11 w-full rounded-xl border border-accent-500 text-sm text-accent-600 disabled:opacity-50">
          {save.isPending ? "保存中…" : "開発ワークフローを保存"}
        </button>
      )}
    </SettingsGroup>
  );
}
