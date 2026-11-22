/** 役割とモデル。計画・開発・調査・簡易検証・最終検証・評価に別々のモデルを選ぶ。
 * ローカルのモデルは GPU で同時に 1 つしか動かせないので、同時に動きうる役割（同じ段）のローカルは 1 つだけ。
 * 外部のモデルは自由に混ぜられ、段をまたぐ載せ替えは順番に行われる。決まりの検査はサーバー（roles.py）が持ち、
 * ここは保存せずに確かめる API（/opencode/roles/check）の結果を、その場で出す。 */
import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { api } from "../../api/client";
import { SettingsGroup } from "../models/formControls";
import type { Workflow } from "./WorkflowExtensions";

export type RoleKey = "plan" | "build" | "research" | "quick_check" | "final_check" | "evaluate";
export type Roles = Partial<Record<RoleKey, string>>;

interface RoleCheck {
  error: string;
  notes: string[];
  effective: Partial<Record<RoleKey, { model: string; explicit: boolean; stage: number }>>;
}

const STAGES: { n: number; title: string; hint: string; roles: { key: RoleKey; label: string; hint: string; fallback: string }[] }[] = [
  { n: 1, title: "① 計画", hint: "計画と指示書を作る（OpenCode の plan）", roles: [
    { key: "plan", label: "計画", hint: "計画・指示書を作るモデル", fallback: "セッションと同じモデル（既定）" },
  ] },
  { n: 2, title: "② 作る", hint: "開発と、その間に呼ぶ子エージェント。同時に動きうるので、ローカルは同じモデルにします", roles: [
    { key: "build", label: "開発", hint: "実装するモデル（build）", fallback: "セッションと同じモデル（既定）" },
    { key: "research", label: "調査", hint: "コードや資料を読む・探す子エージェント", fallback: "開発と同じ（既定）" },
    { key: "quick_check", label: "簡易検証", hint: "変更の直後にテストや関門を流す子エージェント", fallback: "開発と同じ（既定）" },
  ] },
  { n: 3, title: "③ 確かめる", hint: "最終検証と評価。同時に動きうるので、ローカルは同じモデルにします", roles: [
    { key: "final_check", label: "最終検証", hint: "完了の前に差分を読むレビュー役（reviewer など）", fallback: "計画と同じ（既定）" },
    { key: "evaluate", label: "評価", hint: "完成物を目的に照らして評価する子エージェント", fallback: "計画と同じ（既定）" },
  ] },
];

export function RolesPanel({ value, onChange, editable, onProblem }: {
  value: Workflow; onChange: (next: Workflow) => void; editable: boolean; onProblem?: (message: string | null) => void;
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
  const set = (key: RoleKey, next: string) => onChange({ ...value, roles: { ...roles, [key]: next } });
  return (
    <SettingsGroup title="役割とモデル" description="段の順に動きます。ローカルのモデルは GPU で同時に 1 つだけなので、同じ段のローカルは同じモデルにします（外部のモデルは自由）">
      {STAGES.map((stage) => (
        <section key={stage.n} aria-label={stage.title} className="space-y-2 rounded-xl bg-zinc-50 p-3 dark:bg-zinc-800/50">
          <div>
            <h4 className="text-xs font-semibold">{stage.title}</h4>
            <p className="text-[11px] text-zinc-500">{stage.hint}</p>
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
                  {stale && <option value={current}>{current}（いまは使えません）</option>}
                </select>
                <span className="text-[11px] text-zinc-500">
                  {role.hint}{effective ? ` ／ いま使うモデル: ${effective.model}` : ""}
                </span>
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
      <div aria-live="polite" className="space-y-2">
        {error && (
          <p role="alert" className="rounded-xl border border-red-300 bg-red-50 px-3 py-2 text-xs font-medium leading-relaxed text-zinc-900 dark:border-red-800 dark:bg-red-950/60 dark:text-red-100">
            保存できません。{error}
          </p>
        )}
        {!error && (check.data?.notes ?? []).filter((n) => n.includes("別の目")).map((note) => (
          <p key={note} role="note" className="rounded-xl bg-amber-50 px-3 py-2 text-xs leading-relaxed text-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
            注意: {note}。最終検証か計画に別のモデルを選ぶと、別の目で確かめられます。
          </p>
        ))}
      </div>
    </SettingsGroup>
  );
}
