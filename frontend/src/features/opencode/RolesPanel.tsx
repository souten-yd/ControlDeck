/** 役割とモデル。OpenCode でモデルを選ぶ場所はここ 1 か所（既定のモデル＋役割ごとのモデル）。
 * 計画・開発・調査・簡易検証・最終検証・評価・相談に別々のモデルを選ぶ。開発担当（子エージェント developer）は
 * 「開発」と同じモデル、Auto Advisor の相談先は「相談」のモデルで動く（別の選択欄は持たない）。
 * どの役割にもどのモデルでも選べる（全部同じでも全部違ってもよい。2026-11-22 利用者「変な制限は設けなくて良い」）。
 * 同じモデルが続けば載せ替えは起きない。同じ段に別のローカルがあれば、待ち時間が増える旨をお知らせだけ出す
 * （保存せずに確かめる API /opencode/roles/check の notes）。 */
import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { api } from "../../api/client";
import { SettingsGroup } from "../models/formControls";
import type { Workflow } from "./WorkflowExtensions";

export type RoleKey = "plan" | "build" | "research" | "quick_check" | "final_check" | "evaluate" | "advise";
export type Roles = Partial<Record<RoleKey, string>>;

interface RoleCheck {
  error: string;
  notes: string[];
  effective: Partial<Record<RoleKey, { model: string; explicit: boolean; stage: number }>>;
}

const STAGES: { n: number; title: string; hint: string; roles: { key: RoleKey; label: string; hint: string; fallback: string }[] }[] = [
  { n: 1, title: "① 計画", hint: "計画と指示書を作る（OpenCode の plan）", roles: [
    { key: "plan", label: "計画", hint: "計画・指示書を作るモデル", fallback: "既定のモデルで動く" },
  ] },
  { n: 2, title: "② 作る", hint: "開発と、その間に呼ぶ子エージェント", roles: [
    { key: "build", label: "開発", hint: "実装するモデル（build）。実装を任せる子エージェント（開発担当）もこのモデルで動きます", fallback: "既定のモデルで動く" },
    { key: "research", label: "調査", hint: "コードや資料を読む・探す子エージェント", fallback: "開発と同じ" },
    { key: "quick_check", label: "簡易検証", hint: "変更の直後にテストや関門を流す子エージェント", fallback: "開発と同じ" },
  ] },
  { n: 3, title: "③ 確かめる", hint: "最終検証と評価", roles: [
    { key: "final_check", label: "最終検証", hint: "完了の前に差分を読むレビュー役（reviewer など）", fallback: "計画と同じ" },
    { key: "evaluate", label: "評価", hint: "完成物を目的に照らして評価する子エージェント", fallback: "計画と同じ" },
  ] },
  { n: 4, title: "④ 行き詰まったとき", hint: "Auto Advisor（開発ワークフロー）を使うときだけ", roles: [
    { key: "advise", label: "相談", hint: "行き詰まったときに助言するモデル", fallback: "既定のモデルと同じ" },
  ] },
];

export function RolesPanel({ value, onChange, editable, onProblem, defaultModel }: {
  value: Workflow; onChange: (next: Workflow) => void; editable: boolean; onProblem?: (message: string | null) => void;
  /** 既定のモデル（役割でモデルを選ばなければ、このモデルで動く）。 */
  defaultModel: { value: string; onChange: (next: string) => void };
}) {
  const roles: Roles = value.roles ?? {};
  const cloud = value.cloud_models ?? [];
  const check = useQuery({
    queryKey: ["opencode-roles-check", JSON.stringify(roles)],
    queryFn: () => api<RoleCheck>("/opencode/roles/check", { method: "POST", json: { roles } }),
    placeholderData: (previous) => previous,
  });
  const error = check.data?.error ?? "";
  useEffect(() => { onProblem?.(error || null); }, [error, onProblem]);
  const field = "mt-1 min-h-11 w-full truncate pr-8 text-ellipsis rounded-xl border border-zinc-300 bg-white px-3 text-sm disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900";
  // 相談は Auto Advisor を使うときだけ働く。使わないときは薄く出す（選んでおくことはできる）
  const adviseUsed = value.auto_advisor !== "off" && Boolean(value.installed?.auto_advisor);
  const models = value.models ?? [];
  const model = defaultModel.value;
  const known = model === "" || model === "auto" || models.some((m) => m.alias === model);
  const set = (key: RoleKey, next: string) => onChange({ ...value, roles: { ...roles, [key]: next } });
  return (
    <SettingsGroup title="役割とモデル" description="役割ごとに使うモデルを選びます。選ばない役割は既定のモデルで動き、同じモデルが続くときは載せ替えません">
      <div className="space-y-1 rounded-xl bg-zinc-50 p-3 dark:bg-zinc-800/50">
        <label className="block text-xs font-semibold">既定のモデル
          <select aria-label="既定のモデル" value={known ? (model || "auto") : model} disabled={!editable}
            onChange={(e) => defaultModel.onChange(e.target.value)} className={`${field} font-normal`}>
            <option value="auto">Models で選んだランタイムに任せる（auto）</option>
            {models.map((m) => <option key={m.alias} value={m.alias}>{m.alias}{m.runtime ? `（${m.runtime}）` : ""}</option>)}
            {!known && <option value={model}>{model}</option>}
          </select>
        </label>
        <p className="text-[11px] text-zinc-500">役割でモデルを選ばなければ、このモデルで動きます。auto は Models の共通設定のランタイムで、必要なら起動します。</p>
      </div>
      {STAGES.map((stage) => (
        <section key={stage.n} aria-label={stage.title}
          className={`space-y-2 rounded-xl bg-zinc-50 p-3 dark:bg-zinc-800/50 ${stage.n === 4 && !adviseUsed ? "opacity-60" : ""}`}>
          <div>
            <h4 className="text-xs font-semibold">{stage.title}</h4>
            <p className="text-[11px] text-zinc-500">{stage.n === 4 && !adviseUsed ? "（相談を使うときだけ）" : stage.hint}</p>
          </div>
          {stage.roles.map((role) => {
            const current = roles[role.key] ?? "";
            const effective = check.data?.effective?.[role.key];
            const stale = current.startsWith("cloud:") && !cloud.some((m) => m.id === current);
            return (
              <label key={role.key} className="block text-xs">{role.label}
                <select aria-label={`${stage.title} ${role.label}のモデル`} value={current} disabled={!editable}
                  onChange={(e) => set(role.key, e.target.value)} className={field}>
                  <option value="">{role.fallback}</option>
                  <option value="auto">auto（Models で選んだランタイム）</option>
                  {(value.models ?? []).map((m) => <option key={m.alias} value={m.alias}>{m.alias}{m.runtime ? `（${m.runtime}）` : ""}</option>)}
                  {cloud.length > 0 && (
                    <optgroup label="外部（フロンティア）— 外部へ送信">
                      {cloud.map((m) => <option key={m.id} value={m.id}>外部｜{m.name}</option>)}
                    </optgroup>
                  )}
                  {stale && <option value={current}>{current}（いまは使えないので既定で動きます）</option>}
                </select>
                <span className="text-[11px] text-zinc-500">
                  {role.hint}{effective ? ` ／ いま使うモデル: ${effective.model}` : ""}
                </span>
                {stale && (
                  <span role="note" className="mt-1 block rounded-lg bg-zinc-100 px-2 py-1.5 text-[11px] leading-relaxed text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
                    この外部モデルはいま使えないので、既定で動きます（Models → Frontier で、モデルと OpenCode からの送信を確かめてください）。
                  </span>
                )}
                {current.startsWith("cloud:") && !stale && (
                  <span role="note" className="mt-1 block rounded-lg bg-amber-50 px-2 py-1.5 text-[11px] leading-relaxed text-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
                    外部へ送信: この役割に渡す内容が外部へ送られます。秘密の会話・特許の流れでは、ローカルのモデルに替わります。
                  </span>
                )}
              </label>
            );
          })}
        </section>
      ))}
      <p className="text-[11px] text-zinc-500">秘密の会話・特許の流れでは、自動でローカルのモデルだけを使います。</p>
      <div aria-live="polite" className="space-y-2">
        {(check.data?.notes ?? []).filter((n) => n.includes("載せ替え") || n.includes("別の目")).map((note) => (
          <p key={note} role="note" className="rounded-xl bg-zinc-100 px-3 py-2 text-xs leading-relaxed text-zinc-700 dark:bg-zinc-800 dark:text-zinc-200">
            お知らせ: {note}
          </p>
        ))}
      </div>
    </SettingsGroup>
  );
}
