/** 思考（reasoning）のモデル個別設定。llama.cpp / Ollama / 外部endpoint 共通。
 *
 * レベルはプリセットで、選ぶとバジェット入力に対応値が入りそのまま微調整できる。
 * 自動/オフではバジェット欄を隠す（意味を持たないため）。 */
import { THINK_LEVEL_BUDGETS, type ThinkMode } from "../../api/models";

const MODES: Array<{ value: ThinkMode; label: string; hint: string }> = [
  { value: "auto", label: "自動", hint: "モデルの既定に任せる" },
  { value: "off", label: "オフ", hint: "思考しない（最速）" },
  { value: "low", label: "低", hint: "1,024 トークン" },
  { value: "medium", label: "中", hint: "4,096 トークン" },
  { value: "high", label: "高", hint: "16,384 トークン" },
  { value: "xhigh", label: "最高", hint: "32,768 トークン" },
  { value: "custom", label: "カスタム", hint: "トークン数を直接指定" },
];

export function ThinkingControl({ mode, budget, onChange, runtime, disabled }: {
  mode: ThinkMode;
  budget: number;
  onChange: (mode: ThinkMode, budget: number) => void;
  /** バジェット非対応ランタイムでは注記を出す。 */
  runtime?: "llama.cpp" | "ollama" | "external";
  disabled?: boolean;
}) {
  const current = MODES.find((m) => m.value === mode) ?? MODES[0];
  const showBudget = mode === "custom";

  const pick = (next: ThinkMode) => {
    // レベルを選んだらバジェット欄に対応値を入れておき、カスタムへ移った時に
    // その値から微調整を始められるようにする。
    const preset = THINK_LEVEL_BUDGETS[next];
    onChange(next, next === "custom" ? (budget || THINK_LEVEL_BUDGETS[mode] || 4096) : (preset ?? 0));
  };

  return (
    <div className="space-y-2 rounded-xl border border-zinc-200 p-2.5 dark:border-zinc-700">
      <div>
        <p className="text-xs font-medium text-zinc-500">思考（reasoning）</p>
        <p className="mt-0.5 text-[10px] text-zinc-400">
          深く考えるほど品質は上がりますが遅くなります。{current.hint}
        </p>
      </div>
      <div className="grid grid-cols-4 gap-1 sm:grid-cols-7">
        {MODES.map((m) => (
          <button key={m.value} type="button" disabled={disabled}
            onClick={() => pick(m.value)}
            aria-pressed={mode === m.value}
            className={`min-h-11 rounded-lg border px-1 text-[11px] transition disabled:opacity-40 ${
              mode === m.value
                ? "border-accent-500 bg-accent-50 font-semibold text-accent-700 dark:bg-accent-600/15 dark:text-accent-300"
                : "border-zinc-200 text-zinc-500 hover:border-zinc-400 dark:border-zinc-700"
            }`}>
            {m.label}
          </button>
        ))}
      </div>
      {showBudget && (
        <label className="block">
          <span className="mb-1 block text-xs font-medium text-zinc-500">思考トークン上限</span>
          <input type="number" min={0} max={262144} step={256} value={budget || ""}
            disabled={disabled}
            onChange={(e) => onChange("custom", Number(e.target.value) || 0)}
            placeholder="例: 8192（0 で無制限）"
            className="w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900" />
        </label>
      )}
      {runtime === "ollama" && mode !== "auto" && mode !== "off" && (
        <p className="text-[10px] leading-relaxed text-amber-600 dark:text-amber-400">
          Ollama はトークン上限に対応していないため、最も近いレベル（低・中・高）として反映されます。
        </p>
      )}
      {runtime === "llama.cpp" && mode !== "auto" && (
        <p className="text-[10px] leading-relaxed text-zinc-400">
          llama.cpp では起動引数（--reasoning / --reasoning-budget）になるため、保存後の再起動で反映されます。
        </p>
      )}
    </div>
  );
}
