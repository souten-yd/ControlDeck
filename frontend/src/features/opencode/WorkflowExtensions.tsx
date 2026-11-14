/** 開発ワークフローの拡張（Superpowers・Adversarial Review・Auto Advisor）。
 * ControlDeck から起動する OpenCode セッションにだけ効く。レビュー・相談に別のモデルを
 * 選ぶと、ゲートウェイが要求に合わせて載せ替える（例: Flash-Next で開発し Maya-S でレビュー）。
 * 保存は OpenCode 設定シートの「保存」1本にまとめるため、ここは値の編集だけを持つ。 */
import { SettingsGroup, Toggle } from "../models/formControls";

export interface Workflow {
  superpowers: boolean;
  adversarial_review: boolean;
  auto_advisor: "off" | "observe" | "active";
  review_model: string;
  advisor_model: string;
  models?: { alias: string; runtime: string }[];
}

export function workflowBody(value: Workflow) {
  return { superpowers: value.superpowers, adversarial_review: value.adversarial_review,
    auto_advisor: value.auto_advisor, review_model: value.review_model, advisor_model: value.advisor_model };
}

export function WorkflowExtensions({ value, onChange, runtime, editable }: {
  value: Workflow; onChange: (next: Workflow) => void; runtime: string; editable: boolean;
}) {
  const field = "mt-1 min-h-11 w-full rounded-xl border border-zinc-300 bg-white px-3 text-sm disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900";
  const advisorUnsupported = runtime === "v1";
  const modelSelect = (label: string, key: "review_model" | "advisor_model", hint: string) => (
    <label className="block text-xs">{label}
      <select aria-label={label} value={value[key]} disabled={!editable}
        onChange={(e) => onChange({ ...value, [key]: e.target.value })} className={field}>
        <option value="">セッションと同じモデル</option>
        {(value.models ?? []).map((m) => <option key={m.alias} value={m.alias}>{m.alias}{m.runtime ? `（${m.runtime}）` : ""}</option>)}
      </select>
      <span className="text-[11px] text-zinc-500">{hint}</span>
    </label>
  );
  return (
    <SettingsGroup title="開発ワークフロー" description="ControlDeck から開く OpenCode セッションに足す拡張。次に開くセッションから反映されます">
      <Toggle label="計画とテスト駆動の進め方（Superpowers）" hint="計画→テスト駆動→実装→レビューの進め方をスキルとして使います"
        value={value.superpowers} disabled={!editable} onChange={(v) => onChange({ ...value, superpowers: v })} />
      <Toggle label="別の目によるレビュー（Adversarial Review）" hint="/adversarial-review と /constructive-review。会話を持たない別の文脈で差分を読み、出荷できない理由を探します"
        value={value.adversarial_review} disabled={!editable} onChange={(v) => onChange({ ...value, adversarial_review: v })} />
      {value.adversarial_review && modelSelect("レビューに使うモデル", "review_model",
        "例: 開発は Flash-Next、レビューは Maya-S。別のモデルを選ぶとレビューのたびに載せ替えます（約1分）")}
      <label className="block text-xs">行き詰まり時の相談（Auto Advisor）
        <select aria-label="Auto Advisor" value={value.auto_advisor} disabled={!editable || advisorUnsupported}
          onChange={(e) => onChange({ ...value, auto_advisor: e.target.value as Workflow["auto_advisor"] })} className={field}>
          <option value="off">使わない</option>
          <option value="observe">記録だけ（相談はしない・しきい値の調整用）</option>
          <option value="active">必要な場面で相談する</option>
        </select>
        <span className="text-[11px] text-zinc-500">{advisorUnsupported
          ? "OpenCode v2 のセッションでだけ使えます。"
          : "方針を決める・行き詰まる・完了を宣言する場面で、別のモデルに作業を見てもらいます。advisor() ツールでいつでも呼べます"}</span>
      </label>
      {value.auto_advisor !== "off" && !advisorUnsupported && modelSelect("相談先のモデル", "advisor_model",
        "要否の判断はセッションと同じモデルで行い、相談するときだけこのモデルへ載せ替えます")}
    </SettingsGroup>
  );
}
