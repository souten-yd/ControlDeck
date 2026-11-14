/** 開発ワークフローの拡張（Superpowers・Adversarial Review・Auto Advisor）。
 * ここでは「使う／使わない」と、レビュー・相談に使うモデルだけを選ぶ。プラグイン自体の
 * 導入・更新・削除は 設定 → オプション機能 で行う。
 * ControlDeck から起動する OpenCode セッションにだけ効く。レビュー・相談に別のモデルを
 * 選ぶと、ゲートウェイが要求に合わせて載せ替える（例: Flash-Next で開発し Maya-S でレビュー）。 */
import { Link } from "react-router-dom";
import { SettingsGroup, Toggle } from "../models/formControls";

type ExtensionKey = "superpowers" | "adversarial_review" | "auto_advisor";

export interface Workflow {
  superpowers: boolean;
  adversarial_review: boolean;
  auto_advisor: "off" | "observe" | "active";
  review_model: string;
  advisor_model: string;
  models?: { alias: string; runtime: string }[];
  /** 拡張ごとのプラグインが導入済みか（オプション機能で入れる）。 */
  installed?: Record<ExtensionKey, boolean>;
  /** 拡張に対応するオプション機能の id。 */
  features?: Record<ExtensionKey, string>;
}

export function workflowBody(value: Workflow) {
  return { superpowers: value.superpowers, adversarial_review: value.adversarial_review,
    auto_advisor: value.auto_advisor, review_model: value.review_model, advisor_model: value.advisor_model };
}

export function WorkflowExtensions({ value, onChange, editable }: {
  value: Workflow; onChange: (next: Workflow) => void; editable: boolean;
}) {
  const field = "mt-1 min-h-11 w-full rounded-xl border border-zinc-300 bg-white px-3 text-sm disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900";
  const has = (key: ExtensionKey) => value.installed?.[key] ?? false;
  const missing = (["superpowers", "adversarial_review", "auto_advisor"] as const).filter((key) => !has(key));
  const installLink = (key: ExtensionKey) => (
    <Link to={`/settings/features#feature-${value.features?.[key] ?? ""}`} className="text-accent-600 underline dark:text-accent-400">
      設定 → オプション機能で導入
    </Link>
  );
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
      {missing.length > 0 && (
        <p className="rounded-xl bg-zinc-50 px-3 py-2 text-[11px] text-zinc-600 dark:bg-zinc-800/60 dark:text-zinc-300">
          未導入の拡張は選べません。導入・更新・削除は <Link to="/settings/features" className="text-accent-600 underline dark:text-accent-400">設定 → オプション機能</Link> で行います。
        </p>
      )}
      <Toggle label="計画とテスト駆動の進め方（Superpowers）"
        hint={has("superpowers") ? "計画→テスト駆動→実装→レビューの進め方をスキルとして使います" : "未導入です"}
        value={value.superpowers && has("superpowers")} disabled={!editable || !has("superpowers")}
        onChange={(v) => onChange({ ...value, superpowers: v })} />
      {!has("superpowers") && <p className="-mt-1 text-[11px]">{installLink("superpowers")}</p>}
      <Toggle label="別の目によるレビュー（Adversarial Review）"
        hint={has("adversarial_review") ? "/adversarial-review と /constructive-review。会話を持たない別の文脈で差分を読み、出荷できない理由を探します" : "未導入です"}
        value={value.adversarial_review && has("adversarial_review")} disabled={!editable || !has("adversarial_review")}
        onChange={(v) => onChange({ ...value, adversarial_review: v })} />
      {!has("adversarial_review") && <p className="-mt-1 text-[11px]">{installLink("adversarial_review")}</p>}
      {value.adversarial_review && has("adversarial_review") && modelSelect("レビューに使うモデル", "review_model",
        "例: 開発は Flash-Next、レビューは Maya-S。別のモデルを選ぶとレビューのたびに載せ替えます（約1分）")}
      <label className="block text-xs">行き詰まり時の相談（Auto Advisor）
        <select aria-label="Auto Advisor" value={has("auto_advisor") ? value.auto_advisor : "off"} disabled={!editable || !has("auto_advisor")}
          onChange={(e) => onChange({ ...value, auto_advisor: e.target.value as Workflow["auto_advisor"] })} className={field}>
          <option value="off">使わない</option>
          <option value="observe">記録だけ（相談はしない・しきい値の調整用）</option>
          <option value="active">必要な場面で相談する</option>
        </select>
        <span className="text-[11px] text-zinc-500">{has("auto_advisor")
          ? "方針を決める・行き詰まる・完了を宣言する場面で、別のモデルに作業を見てもらいます。advisor() ツールでいつでも呼べます"
          : installLink("auto_advisor")}</span>
      </label>
      {value.auto_advisor !== "off" && has("auto_advisor") && modelSelect("相談先のモデル", "advisor_model",
        "要否の判断はセッションと同じモデルで行い、相談するときだけこのモデルへ載せ替えます")}
    </SettingsGroup>
  );
}
