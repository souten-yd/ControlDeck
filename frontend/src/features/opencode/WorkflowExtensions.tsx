/** 開発ワークフローの拡張（Superpowers・Adversarial Review・Auto Advisor・UI 検証）。
 * ここでは「使う／使わない」と、レビュー・相談に使うモデルだけを選ぶ。プラグイン自体の
 * 導入・更新・削除は 設定 → オプション機能 で行う。
 * ControlDeck から起動する OpenCode セッションにだけ効く。レビュー・相談に別のモデルを
 * 選ぶと、ゲートウェイが要求に合わせて載せ替える（例: Flash-Next で開発し Maya-S でレビュー）。 */
import { useMutation } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import { SettingsGroup, Toggle } from "../models/formControls";

export type ExtensionKey = "superpowers" | "adversarial_review" | "auto_advisor" | "ui_verify";
const EXTENSIONS: ExtensionKey[] = ["superpowers", "adversarial_review", "auto_advisor", "ui_verify"];

interface Diagnosis {
  ok: boolean;
  tools: number;
  steps: { name: string; ok: boolean; detail: string; ms: number | null }[];
}

export interface Workflow {
  superpowers: boolean;
  adversarial_review: boolean;
  auto_advisor: "off" | "observe" | "active";
  review_model: string;
  advisor_model: string;
  /** UI 検証（Playwright MCP）を session に載せるか。 */
  ui_verify?: boolean;
  /** UI 検証のブラウザに localhost 以外への通信を許すか。既定は許さない。 */
  ui_verify_allow_external?: boolean;
  /** このPCに Chrome が見つかったか。 */
  browser?: boolean;
  models?: { alias: string; runtime: string }[];
  /** 拡張ごとのプラグインが導入済みか（オプション機能で入れる）。 */
  installed?: Partial<Record<ExtensionKey, boolean>>;
  /** 拡張に対応するオプション機能の id。 */
  features?: Partial<Record<ExtensionKey, string>>;
}

export function workflowBody(value: Workflow) {
  return { superpowers: value.superpowers, adversarial_review: value.adversarial_review,
    auto_advisor: value.auto_advisor, review_model: value.review_model, advisor_model: value.advisor_model,
    ui_verify: value.ui_verify ?? false, ui_verify_allow_external: value.ui_verify_allow_external ?? false };
}

export function WorkflowExtensions({ value, onChange, editable }: {
  value: Workflow; onChange: (next: Workflow) => void; editable: boolean;
}) {
  const field = "mt-1 min-h-11 w-full rounded-xl border border-zinc-300 bg-white px-3 text-sm disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900";
  const has = (key: ExtensionKey) => value.installed?.[key] ?? false;
  const missing = EXTENSIONS.filter((key) => !has(key));
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
      <Toggle label="UI 検証（Playwright MCP）"
        hint={!has("ui_verify") ? "未導入です"
          : value.browser === false ? "このPCに Chrome が見つかりません。Google Chrome を入れると使えます"
          : "AI が開発中の画面を実際のブラウザで開き、幅を変えて撮影・操作します"}
        value={Boolean(value.ui_verify) && has("ui_verify")} disabled={!editable || !has("ui_verify")}
        onChange={(v) => onChange({ ...value, ui_verify: v })} />
      {!has("ui_verify") && <p className="-mt-1 text-[11px]">{installLink("ui_verify")}</p>}
      {has("ui_verify") && value.ui_verify && <>
        <Toggle label="外部サイトへの通信を許す"
          hint="既定では localhost だけに届き、ControlDeck 自身は開けません。許すと AI のブラウザが外部のサイトも開けます"
          value={Boolean(value.ui_verify_allow_external)} disabled={!editable}
          onChange={(v) => onChange({ ...value, ui_verify_allow_external: v })} />
        {editable && <UiVerifyDiagnosis />}
      </>}
    </SettingsGroup>
  );
}

/** UI 検証の接続診断。導入 → Chrome → 起動 → 道具の一覧 → 空のページを開く、の順に確かめる。 */
export function UiVerifyDiagnosis() {
  const run = useMutation({
    mutationFn: () => api<Diagnosis>("/opencode/tools/ui-verify/diagnose", { method: "POST" }),
  });
  const result = run.data;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <button type="button" onClick={() => run.mutate()} disabled={run.isPending}
          className="min-h-11 rounded-xl border border-zinc-300 px-4 text-sm font-medium hover:bg-zinc-50 disabled:opacity-40 dark:border-zinc-700 dark:hover:bg-zinc-800">
          {run.isPending ? "診断中…（10 秒ほど）" : "接続を診断"}
        </button>
        <span className="min-w-0 flex-1 text-[11px] text-zinc-500">保存済みの設定でブラウザを実際に起こして確かめます</span>
      </div>
      <div aria-live="polite" className="space-y-2">
        {run.isError && <p role="alert" className="break-words text-xs text-zinc-600 dark:text-zinc-300">
          診断できませんでした。{run.error instanceof Error ? run.error.message : ""}
        </p>}
        {result && <>
          <p className={`text-xs font-medium ${result.ok ? "text-emerald-700 dark:text-emerald-400" : "text-amber-700 dark:text-amber-400"}`}>
            {result.ok ? `✓ 使えます（道具 ${result.tools} 個）` : "✗ 途中で止まりました。下の ✗ の行を確かめてください"}
          </p>
          <ol className="divide-y divide-zinc-100 rounded-xl border border-zinc-200 text-xs dark:divide-zinc-800 dark:border-zinc-700">
            {result.steps.map((step, index) => (
              <li key={index} className="flex items-start gap-2 px-3 py-2">
                <span aria-hidden className={`shrink-0 font-semibold ${step.ok ? "text-emerald-600 dark:text-emerald-400" : "text-amber-600 dark:text-amber-400"}`}>{step.ok ? "✓" : "✗"}</span>
                <span className="min-w-0 flex-1">
                  <span className="break-words font-medium">{step.name}<span className="sr-only">{step.ok ? "（成功）" : "（失敗）"}</span></span>
                  {step.detail && <span className="block break-all text-[11px] text-zinc-500">{step.detail}</span>}
                </span>
                {step.ms != null && <span className="shrink-0 tabular-nums text-[11px] text-zinc-400">{Math.round(step.ms)} ms</span>}
              </li>
            ))}
          </ol>
        </>}
      </div>
    </div>
  );
}
