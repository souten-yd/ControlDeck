/** 開発ワークフローの拡張（Superpowers・Adversarial Review・Auto Advisor・UI 検証）。
 * ここでは「使う／使わない」と、レビュー・相談に使うモデルだけを選ぶ。プラグイン自体の
 * 導入・更新・削除は 設定 → オプション機能 で行う。
 * ControlDeck から起動する OpenCode セッションにだけ効く。レビュー・相談に別のモデルを
 * 選ぶと、ゲートウェイが要求に合わせて載せ替える（例: Flash-Next で開発し Maya-S でレビュー）。 */
import { useMutation } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import { SettingsGroup, Toggle } from "../models/formControls";

export type ExtensionKey = "superpowers" | "adversarial_review" | "auto_advisor" | "ui_verify" | "context7";
const EXTENSIONS: ExtensionKey[] = ["superpowers", "adversarial_review", "auto_advisor", "ui_verify", "context7"];

interface Diagnosis {
  ok: boolean;
  tools: number;
  steps: { name: string; ok: boolean; detail: string; ms: number | null }[];
}

export interface Workflow {
  superpowers: boolean;
  adversarial_review: boolean;
  /** 自動レビュー: コードを変えたら完了の前に reviewer で確かめ、行き詰まったら advisor() で相談する。 */
  auto_review?: boolean;
  auto_advisor: "off" | "observe" | "active";
  /** 旧設定（最終検証として読む）。画面は roles.final_check を使う。 */
  review_model?: string;
  advisor_model: string;
  /** 役割ごとのモデル（空＝既定）。plan / build / research / quick_check / final_check / evaluate。 */
  roles?: Partial<Record<"plan" | "build" | "research" | "quick_check" | "final_check" | "evaluate", string>>;
  /** 開発担当（実装を任せる子エージェント developer）を session に足すか。 */
  developer_agent?: boolean;
  /** 開発担当に使うモデル。空ならセッションと同じモデル。 */
  developer_model?: string;
  /** 役割に選べる外部のモデル（OpenCode からの外部送信を許可したときだけ並ぶ）。 */
  cloud_models?: { id: string; name: string; provider: string }[];
  /** 有効な外部モデルはあるが、OpenCode からの送信が許可されていない。 */
  cloud_blocked?: boolean;
  /** UI 検証（Playwright MCP）を session に載せるか。 */
  ui_verify?: boolean;
  /** UI 検証のブラウザに localhost 以外への通信を許すか。既定は許さない。 */
  ui_verify_allow_external?: boolean;
  /** 精密調査（ControlDeck の Deep Research を OpenCode の道具として渡す）。 */
  research_tool?: boolean;
  /** 精密調査で Web・GitHub・論文の検索を使うか。既定はローカル（コード・RAG）だけ。 */
  research_external?: boolean;
  /** Context7（ライブラリの版に合った公式文書）。問い合わせは外部へ出る。 */
  context7?: boolean;
  /** この PC で設定済みの gh を session から使わせるか。 */
  github_cli?: boolean;
  /** この PC の gh が導入・ログイン済みか。 */
  github?: { installed: boolean; configured: boolean };
  /** OpenCode を動かす場所。box: プロジェクトの箱（推奨）、host: 利用者と同じ権限。 */
  isolation?: "box" | "host";
  /** いま実際に使われる場所（podman が無ければ host）。 */
  isolation_effective?: "box" | "host";
  /** レビューで構文検索（ast-grep）が使えるか。 */
  review_tool?: boolean;
  /** このPCに Chrome が見つかったか。 */
  browser?: boolean;
  models?: { alias: string; runtime: string }[];
  /** 拡張ごとのプラグインが導入済みか（オプション機能で入れる）。 */
  installed?: Partial<Record<ExtensionKey, boolean>>;
  /** 拡張に対応するオプション機能の id。 */
  features?: Partial<Record<ExtensionKey, string>>;
}

export function workflowBody(value: Workflow) {
  return { superpowers: value.superpowers, adversarial_review: value.adversarial_review, auto_review: value.auto_review ?? true,
    auto_advisor: value.auto_advisor, roles: value.roles ?? {}, advisor_model: value.advisor_model,
    developer_agent: value.developer_agent ?? false, developer_model: value.developer_model ?? "",
    ui_verify: value.ui_verify ?? false, ui_verify_allow_external: value.ui_verify_allow_external ?? false,
    research_tool: value.research_tool ?? true, research_external: value.research_external ?? false,
    context7: value.context7 ?? false, github_cli: value.github_cli ?? false, isolation: value.isolation ?? "box" };
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
  const cloudModels = value.cloud_models ?? [];
  const modelSelect = (label: string, key: "advisor_model" | "developer_model", hint: string) => {
    const current = value[key] ?? "";
    const external = cloudModels.find((m) => m.id === current);
    // 保存済みの外部モデルが、いまは選べない（送信の許可を外した・モデルを外した）。
    const stale = current.startsWith("cloud:") && !external;
    return (
      <div className="block text-xs">
        <label className="block">{label}
          <select aria-label={label} value={current} disabled={!editable}
            onChange={(e) => onChange({ ...value, [key]: e.target.value })} className={field}>
            <option value="">セッションと同じモデル</option>
            {(value.models ?? []).map((m) => <option key={m.alias} value={m.alias}>{m.alias}{m.runtime ? `（${m.runtime}）` : ""}</option>)}
            {cloudModels.length > 0 && (
              <optgroup label="外部（フロンティア）— 外部へ送信">
                {cloudModels.map((m) => <option key={m.id} value={m.id}>外部｜{m.name}</option>)}
              </optgroup>
            )}
            {stale && <option value={current}>{current}（いまは使えません）</option>}
          </select>
        </label>
        <span className="text-[11px] text-zinc-500">{hint}</span>
        {external && (
          <p role="note" className="mt-1 rounded-lg bg-amber-50 px-2 py-1.5 text-[11px] leading-relaxed text-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
            外部へ送信: この役割に渡す指示・コード・ファイルの内容が {external.name.split(" / ")[0]} へ送られます。未公開の情報を扱うプロジェクトではローカルのモデルを選んでください。
          </p>
        )}
        {stale && (
          <p role="note" className="mt-1 rounded-lg bg-zinc-50 px-2 py-1.5 text-[11px] leading-relaxed text-zinc-600 dark:bg-zinc-800/60 dark:text-zinc-300">
            この外部モデルはいま使えないため、セッションと同じモデルで動きます（Models → Frontierで、モデルと OpenCode からの送信を確かめてください）。
          </p>
        )}
      </div>
    );
  };
  return (
    <SettingsGroup title="開発ワークフロー" description="ControlDeck から開く OpenCode セッションに足す拡張。次に開くセッションから反映されます">
      <Toggle label="プロジェクトの箱で動かす（推奨）"
        hint={value.isolation_effective === "host" && (value.isolation ?? "box") === "box"
          ? "podman が見つからないため、いまは利用者と同じ権限で動いています"
          : "AI が書けるのは開いたプロジェクトだけになります。本番のデータ・他のプロジェクト・秘密鍵は見えません。ホストの機能（systemd やファン）を直す作業だけはオフにします"}
        value={(value.isolation ?? "box") === "box"} disabled={!editable}
        onChange={(v) => onChange({ ...value, isolation: v ? "box" : "host" })} />
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
      <Toggle label="自動レビュー（完了の前に別の目で確かめる）"
        hint="コードを変えたら、完了を報告する前に子エージェント reviewer が差分を読み、重大な指摘は直してから終えます。同じ失敗が続いたり指摘が消えないときは、下の相談先のモデルに自動で相談します"
        value={value.auto_review ?? true} disabled={!editable}
        onChange={(v) => onChange({ ...value, auto_review: v })} />
      {((value.auto_review ?? true) || (value.adversarial_review && has("adversarial_review"))) && (
        <p className="-mt-1 text-[11px] text-zinc-500">レビューに使うモデルは、上の「役割とモデル」の最終検証で選びます。</p>
      )}
      <Toggle label="別の目によるレビュー（Adversarial Review）"
        hint={has("adversarial_review") ? "大きな変更（公開の API・認証・データの移行や削除）では、自動レビューの後に出荷できない理由を探す係も読みます。/adversarial-review で手動でも呼べます" : "未導入です"}
        value={value.adversarial_review && has("adversarial_review")} disabled={!editable || !has("adversarial_review")}
        onChange={(v) => onChange({ ...value, adversarial_review: v })} />
      {!has("adversarial_review") && <p className="-mt-1 text-[11px]">{installLink("adversarial_review")}</p>}
      <label className="block text-xs">行き詰まったら別のモデルに相談（Auto Advisor）
        <select aria-label="Auto Advisor" value={has("auto_advisor") ? value.auto_advisor : "off"} disabled={!editable || !has("auto_advisor")}
          onChange={(e) => onChange({ ...value, auto_advisor: e.target.value as Workflow["auto_advisor"] })} className={field}>
          <option value="off">使わない</option>
          <option value="observe">記録だけ（相談はしない・しきい値の調整用）</option>
          <option value="active">必要な場面で相談する</option>
        </select>
        <span className="text-[11px] text-zinc-500">{has("auto_advisor")
          ? "方針を決める・行き詰まる・完了を宣言する場面で、相談先のモデルが作業を見て助言します。自動レビューで指摘が消えないときも相談します"
          : installLink("auto_advisor")}</span>
      </label>
      {value.auto_advisor !== "off" && has("auto_advisor") && modelSelect("相談先のモデル", "advisor_model",
        "要否の判断はセッションと同じモデルで行い、相談するときだけこのモデルへ載せ替えます")}
      <Toggle label="開発担当（実装を任せる子エージェント）"
        hint="子エージェント developer を足します。主の会話は方針と確認に集中し、まとまった実装を別の文脈（別のモデルも可）へ任せられます"
        value={Boolean(value.developer_agent)} disabled={!editable}
        onChange={(v) => onChange({ ...value, developer_agent: v })} />
      {value.developer_agent && modelSelect("開発担当のモデル", "developer_model",
        "例: 相談と確認は手元のモデル、実装は別のモデル。ローカルの別モデルを選ぶと、任せるたびに載せ替えます")}
      {value.cloud_blocked && (
        <p className="rounded-xl bg-zinc-50 px-3 py-2 text-[11px] leading-relaxed text-zinc-600 dark:bg-zinc-800/60 dark:text-zinc-300">
          外部のモデル（OpenRouter など）をレビュー・相談・開発担当に使うには、
          <Link to="/models?tab=frontier" className="text-accent-600 underline dark:text-accent-400">Models → Frontier</Link>
          で「OpenCode」からの外部への送信を許可します。
        </p>
      )}
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
      <Toggle label="精密調査（ControlDeck の Deep Research）"
        hint="AI が根拠を集める道具。計画→検索→反証→引用の確認まで行い、根拠つきで答えます"
        value={value.research_tool ?? true} disabled={!editable}
        onChange={(v) => onChange({ ...value, research_tool: v })} />
      {(value.research_tool ?? true) && <>
        <Toggle label="精密調査で外部の検索を使う"
          hint="既定ではこのプロジェクトのコードと RAG だけを調べます。オンにすると Web・GitHub・論文の検索サービスへ検索語が送られます"
          value={Boolean(value.research_external)} disabled={!editable}
          onChange={(v) => onChange({ ...value, research_external: v })} />
        {value.research_external && (
          <p className="-mt-1 rounded-lg bg-amber-50 px-2 py-1.5 text-[11px] text-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
            未公開の発明・請求項・秘密の情報を扱うプロジェクトではオフにしてください。検索語は外部のサービスに残ります。
          </p>
        )}
      </>}
      <Toggle label="ライブラリの公式文書（Context7）"
        hint={!has("context7") ? "未導入です"
          : "使っているライブラリの版に合った文書を AI が引きます。ライブラリ名と調べたい内容が外部（context7.com）へ送られます"}
        value={Boolean(value.context7) && has("context7")} disabled={!editable || !has("context7")}
        onChange={(v) => onChange({ ...value, context7: v })} />
      {!has("context7") && <p className="-mt-1 text-[11px]">{installLink("context7")}</p>}
      <Toggle label="GitHub 連携（この PC の gh を使う）"
        hint={!value.github?.installed ? "gh（GitHub CLI）が見つかりません"
          : !value.github?.configured ? "gh がログインしていません。ターミナルで gh auth login を実行してください"
          : "AI が gh と git push で PR・Issue・Actions を扱えます。この PC の GitHub の権限をそのまま使います"}
        value={Boolean(value.github_cli)} disabled={!editable || !value.github?.configured}
        onChange={(v) => onChange({ ...value, github_cli: v })} />
      {value.github_cli && (
        <p className="-mt-1 rounded-lg bg-amber-50 px-2 py-1.5 text-[11px] text-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
          AI が push・PR の作成やマージまで行えるようになります。token の表示（gh auth）、リポジトリの削除・アーカイブ、secret の操作、gh api は禁止しています。
        </p>
      )}
      <p className="text-[11px] text-zinc-500">
        レビューは子エージェント reviewer が行います（ファイルは変更しません）。
        {value.review_tool ? "ast-grep で呼び出し側まで構文で追います。" : "ast-grep を導入すると、呼び出し側を構文で正確に追えます。"}
      </p>
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
