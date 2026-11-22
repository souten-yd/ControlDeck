import { useId, useState } from "react";
import { buildPlanAnswer, initialPicks, unansweredNumbers, type PlanPick, type PlanQuestion } from "./planQuestions";

const BTN = "inline-flex min-h-10 items-center justify-center gap-1.5 rounded-xl px-3.5 text-sm font-semibold [@media(pointer:coarse)]:min-h-11";

/** プランの「決めてもらいたいこと」への答えの画面（選択肢・推奨・自由入力）。
 *  build=true はビルドへ切り替えて送る。プランの回でないときは「この答えで進める」だけ（同じ agent で送る）。 */
export function PlanQuestionForm({ questions, isPlan, onSubmit }: {
  questions: PlanQuestion[];
  isPlan: boolean;
  onSubmit: (text: string, build: boolean) => void;
}) {
  const uid = useId();
  const [picks, setPicks] = useState<PlanPick[]>(() => initialPicks(questions));
  const [extra, setExtra] = useState("");
  const [sent, setSent] = useState(false);
  if (sent) return null;
  const missing = unansweredNumbers(questions, picks);
  const patch = (i: number, p: Partial<PlanPick>) => setPicks((cur) => cur.map((x, k) => (k === i ? { ...x, ...p } : x)));
  const toggle = (i: number, j: number, multi: boolean) => {
    const cur = picks[i].picked;
    patch(i, { picked: multi ? (cur.includes(j) ? cur.filter((x) => x !== j) : [...cur, j]) : [j], other: "" });
  };
  const send = (build: boolean, replan: boolean) => { setSent(true); onSubmit(buildPlanAnswer(questions, picks, extra, replan), build); };
  const field = "min-h-10 w-full min-w-0 rounded-lg border border-zinc-300 bg-white px-2.5 text-sm text-zinc-900 dark:border-zinc-600 dark:bg-zinc-900 dark:text-zinc-100";
  return (
    <div data-plan-recommend className="mt-1 min-w-0 max-w-full space-y-3 rounded-xl border border-accent-200 bg-accent-50/60 p-2.5 text-sm dark:border-accent-700/50 dark:bg-accent-600/10">
      {questions.map((q, i) => (
        <fieldset key={q.n} data-plan-question={q.n} className="min-w-0 space-y-1">
          <legend className="mb-1 max-w-full break-words font-semibold text-zinc-900 dark:text-zinc-100">
            {q.n}. {q.question}{q.multi && <span className="ml-1 text-xs font-normal text-zinc-500 dark:text-zinc-400">（複数可）</span>}
          </legend>
          {q.options.map((o, j) => (
            <label key={j} data-plan-option className="flex min-h-11 min-w-0 cursor-pointer items-start gap-2 rounded-lg border border-zinc-200 bg-white/70 px-2.5 py-2 has-[:checked]:border-accent-500 dark:border-zinc-700 dark:bg-zinc-900/60">
              <input type={q.multi ? "checkbox" : "radio"} name={`${uid}-${q.n}`} checked={picks[i].picked.includes(j)}
                onChange={() => toggle(i, j, q.multi)} className="mt-0.5 size-4 shrink-0 accent-accent-600" />
              <span className="min-w-0 break-words">
                <span className="text-zinc-900 dark:text-zinc-100">{o.label}</span>
                {o.recommended && <span data-plan-recommended-badge className="ml-1.5 rounded-full bg-accent-600 px-1.5 py-0.5 text-[10px] font-semibold text-white">推奨</span>}
                {o.note && <span className="block text-xs text-zinc-500 dark:text-zinc-400">{o.note}</span>}
              </span>
            </label>
          ))}
          <input type="text" data-plan-other value={picks[i].other} onChange={(e) => patch(i, { other: e.target.value })}
            placeholder={q.options.length ? "その他（自由に書く）" : "答えを書く"} aria-label={`${q.n} の自由入力`} className={field} />
        </fieldset>
      ))}
      <input type="text" data-plan-extra value={extra} onChange={(e) => setExtra(e.target.value)}
        placeholder="全体への補足（任意）" aria-label="全体への補足" className={field} />
      {missing.length > 0 && <p data-plan-missing className="text-xs text-red-600 dark:text-red-400">答えが要ります: {missing.join("・")} 番</p>}
      <div className="flex flex-wrap gap-2">
        <button type="button" data-proceed-recommended disabled={missing.length > 0} onClick={() => send(isPlan, false)}
          className={`${BTN} bg-accent-600 text-white shadow-sm hover:bg-accent-700 disabled:cursor-not-allowed disabled:opacity-50`}>
          <span aria-hidden>{isPlan ? "🛠️" : "✅"}</span>{isPlan ? "この答えで作る" : "この答えで進める"}
        </button>
        {isPlan && (
          <button type="button" data-answer-replan disabled={missing.length > 0} onClick={() => send(false, true)}
            className={`${BTN} border border-accent-300 bg-white text-accent-700 hover:bg-accent-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-accent-700 dark:bg-zinc-900 dark:text-accent-300`}>
            答えてプランを直す
          </button>
        )}
      </div>
    </div>
  );
}
