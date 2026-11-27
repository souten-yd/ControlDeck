/** Models の「フロンティアモデル」にある「OpenCode のモデル」。OpenCode が使う既定のモデルを選ぶ唯一の場所。
 *  選べるのは OpenCode で使えるモデル（ローカル・OpenRouter・OpenCode（Zen）・ゲートウェイで使えるクラウド）。
 *  AI アシスタントのモデルの札も同じ一覧を読み、会話でモデルを選んでいなければこの既定で OpenCode を始める。
 *  OpenCode の機能が無効なときは（API が無いので）出さない。 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { useToasts } from "../../stores";
import { useExpert } from "../commander/TeamRoles";
import { MODEL_CHOICES_KEY, type ModelChoices } from "../workflows/ModelPicker";

const field = "min-h-11 w-full min-w-0 rounded-xl border border-zinc-300 bg-white px-3 text-sm disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900";
const NONE = "";

export function OpenCodeModelPanel() {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const expert = useExpert();
  const current = useQuery({
    queryKey: ["opencode-default-model"],
    queryFn: () => api<{ default_model: string }>("/opencode/default-model"),
    retry: false,
  });
  const choices = useQuery({
    queryKey: [MODEL_CHOICES_KEY, "", false],
    queryFn: () => api<ModelChoices>("/workflows/model-choices"),
    staleTime: 30_000,
  });
  const save = useMutation({
    mutationFn: (value: string) => api<{ default_model: string }>("/opencode/default-model", { method: "PUT", json: { default_model: value } }),
    onSuccess: (r) => {
      qc.setQueryData(["opencode-default-model"], r);
      void qc.invalidateQueries({ queryKey: [MODEL_CHOICES_KEY] });
      void qc.invalidateQueries({ queryKey: ["opencode-status"] });
      show(r.default_model ? "OpenCode のモデルを変えました。次の会話・次の起動から使います" : "OpenCode のモデルを、作業場の順番どおりに戻しました");
    },
    onError: (e) => show(e instanceof Error ? e.message : "保存できませんでした", "error"),
  });
  // OpenCode の機能が無効（API が無い）・読めないときは出さない
  if (current.isError || !current.data || !choices.data) return null;
  const value = current.data.default_model;
  // 一覧の id（会話のモデル）と OpenCode の参照（provider/モデル）の対応: Zen はそのまま、他は controldeck/
  const refOf = (id: string) => (id.startsWith("opencode-go/") || id.startsWith("opencode/") ? id : `controldeck/${id}`);
  const groups = choices.data.groups
    .map((g) => ({ ...g, models: g.models.filter((m) => m.code) }))
    .filter((g) => g.models.length > 0);
  const known = value === NONE || groups.some((g) => g.models.some((m) => refOf(m.id) === value));
  return (
    <section aria-label="OpenCode のモデル" data-opencode-model className="space-y-1.5 rounded-xl bg-zinc-50 px-3 py-2.5 dark:bg-zinc-800/60">
      <label className="block text-xs font-semibold">OpenCode のモデル
        <select aria-label="OpenCode のモデル" value={value} disabled={save.isPending}
          onChange={(e) => save.mutate(e.target.value)} className={`${field} mt-1 font-normal`}>
          <option value={NONE}>指定しない（作業場の順番どおり: GLM-5.3 → 無料 → ローカル）</option>
          {groups.map((g) => (
            <optgroup key={g.id} label={g.label}>
              {g.models.map((m) => <option key={m.id} value={refOf(m.id)}>{m.name} — {m.hint}</option>)}
            </optgroup>
          ))}
          {!known && <option value={value}>{value}（いまは使えません）</option>}
        </select>
      </label>
      <p className="text-[11px] leading-relaxed text-zinc-600 dark:text-zinc-300">
        OpenCode が使うモデルです。AI アシスタントの会話でモデルを選んでいれば、そのモデルが先です。選んでいない会話は、ここで選んだモデルで OpenCode を始めます。
      </p>
      {expert && <p className="font-mono text-[11px] text-zinc-500" data-opencode-model-ref>{value || "（指定なし）"}</p>}
    </section>
  );
}
