/** AI アシスタントのモデルの選び手（ふつうのチャットも OpenCode も同じ一覧）。
 *  一覧は種類ごとのまとまり（ローカル・OpenRouter・OpenCode（Zen）・Claude ほか）。普段は名前と一言だけで、
 *  細かい値（モデルの名前・コンテキスト）はエキスパートのときだけ出す。使えない経路のものは並べない（サーバーが絞る）。
 *  2026-11-27 利用者「Models のフロンティアから、AI アシスタントなどでも他のモデルとローカル LLM を選べるように」。 */
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import { useExpert } from "../commander/TeamRoles";

export interface ModelChoice {
  id: string; name: string; hint: string; external: boolean;
  /** ふつうのチャットで使える */ chat: boolean;
  /** OpenCode で使える */ code: boolean;
  detail: string;
}
export interface ModelChoiceGroup { id: string; label: string; models: ModelChoice[] }
export interface ModelChoices { base_url: string; groups: ModelChoiceGroup[]; default_model: string; secret: boolean }

export const MODEL_CHOICES_KEY = "model-choices";

export function useModelChoices(enabled: boolean, conversationId: string | null, secret: boolean) {
  return useQuery({
    queryKey: [MODEL_CHOICES_KEY, conversationId ?? "", secret],
    queryFn: () => api<ModelChoices>(`/workflows/model-choices?secret=${secret ? "true" : "false"}${conversationId ? `&conversation=${encodeURIComponent(conversationId)}` : ""}`),
    staleTime: 30_000,
    enabled,
  });
}

export function findChoice(choices: ModelChoices | undefined, id: string): ModelChoice | undefined {
  for (const group of choices?.groups ?? []) {
    const hit = group.models.find((m) => m.id === id);
    if (hit) return hit;
  }
  return undefined;
}

/** OpenCode の既定のモデル（`provider/モデル`）を、一覧の id（会話のモデル）へ。 */
export function defaultChoiceId(choices: ModelChoices | undefined): string {
  const ref = choices?.default_model ?? "";
  return ref.startsWith("controldeck/") ? ref.slice("controldeck/".length) : ref;
}

export function ModelPicker({ choices, value, onPick, mode }: {
  choices: ModelChoices;
  /** いま選んでいるモデルの id（OpenCode のときは OpenCode で使うもの） */
  value: string;
  onPick: (choice: ModelChoice) => void;
  mode: "chat" | "code";
}) {
  const expert = useExpert();
  const current = findChoice(choices, value);
  return (
    <div className="min-w-0" data-model-picker>
      <select
        value={current ? value : ""}
        aria-label="モデル"
        onChange={(e) => { const hit = findChoice(choices, e.target.value); if (hit) onPick(hit); }}
        className="w-full min-w-0 rounded-lg border border-zinc-300 bg-white px-2 py-1.5 text-base dark:border-zinc-700 dark:bg-zinc-900 sm:text-sm"
      >
        {!current && <option value="">{value || "モデルを選択"}</option>}
        {choices.groups.map((group) => (
          <optgroup key={group.id} label={group.label} data-model-group={group.id}>
            {group.models.map((m) => (
              <option key={m.id} value={m.id} data-model-option={m.id}>
                {m.name} — {m.hint}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
      {current && (
        <p className="mt-1 text-[11px] leading-relaxed text-zinc-500 dark:text-zinc-400" data-model-hint>
          {current.hint}
          {mode === "chat" && !current.chat && "（ふつうのチャットでは使えません。OpenCode に切り替えると使います）"}
          {expert && current.detail && <span className="block font-mono" data-model-detail>{current.detail}</span>}
        </p>
      )}
    </div>
  );
}
