/** Nexarch の AI チーム（T3c）: 「チームリーダーに頼む」の一文の入力と、頼んだ後の時系列のビュー。
 *
 * 普段（エキスパート切）はこれだけ: 依頼文 → 計画（題の一覧。開くと本文）→ 進み具合（仕事ごとに 1 行）→ 質問（その場で答える）→
 * 結果（何をしたか・確認の結果・変更ファイル数・残り・「取り込む」）。要対応（質問・止まった・上限で動けない）は一番上。
 * エキスパートでは、段の割り当て・チームリーダーの実行の記録・MCP の呼び出しの履歴・使用量を足す。 */
import { lazy, Suspense, useState, type ReactNode } from "react";
import {
  answerLeaderQuestion, askLeader, createAutoTeam, landTeam, resumeLeader, stopLeader,
  type PlanItem, type Team, type TeamTask,
} from "../../api/commander";
import { primaryButtonClass, secondaryButtonClass } from "../../components/ui";
import { useToasts } from "../../stores";
import { MaterialAttach } from "./TeamMaterials";

const MarkdownMessage = lazy(() => import("../workflows/MarkdownMessage"));

const inputClass = "min-h-11 w-full rounded-xl border border-zinc-300 bg-white px-3 text-sm dark:border-zinc-700 dark:bg-zinc-900";
const TIER_LABEL = { explore: "調べる", implement: "実装", review: "評価", hard: "難所" } as const;
const SECTION = "mb-3 rounded-xl border border-zinc-200 p-3 dark:border-zinc-700";
const SECTION_TITLE = "mb-1.5 text-xs font-semibold text-zinc-500 dark:text-zinc-400";

/** 仕事 1 件の状態を、段に合わせた一言にする（調べ中・実装中・評価中・統合済み など）。 */
export function taskPhrase(task: TeamTask): string {
  const tier = task.tier ?? "implement";
  if (task.status === "blocked") return "止まった";
  if (task.status === "todo") return "順番待ち";
  if (task.status === "doing") return tier === "explore" ? "調べ中" : tier === "review" ? "評価中" : "実装中";
  if (task.status === "review") return task.checks_result?.status === "running" ? "確認中" : tier === "explore" ? "調べ済み" : tier === "review" ? "評価済み" : "実装済み（統合待ち）";
  if (task.status === "done") return tier === "implement" || tier === "hard" ? "統合済み" : tier === "review" ? "評価済み" : "調べ済み";
  return "";
}

function fmtCost(usd: number | undefined): string {
  return usd ? `約 $${usd.toFixed(2)}` : "";
}

/** 普段の入口: 一文の入力と「頼む」。既定は新しいチームを作る。既存のチームがあれば「このチームへ頼み足す」を選べる。 */
export function LeaderAsk({ team, onAsked }: { team?: Team; onAsked: (id: string) => void }) {
  const show = useToasts((s) => s.show);
  const [text, setText] = useState("");
  const [paths, setPaths] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [addToThis, setAddToThis] = useState(false);
  const running = team?.leader_run?.status === "running";
  const canAdd = Boolean(team) && !running;
  const submit = async () => {
    setBusy(true);
    try {
      const made = team && addToThis ? await askLeader(team.id, text.trim(), paths) : await createAutoTeam(text.trim(), "", "", paths);
      setText("");
      setPaths([]);
      onAsked(made.id);
      show("チームリーダーに頼みました");
    } catch (e) {
      show(e instanceof Error ? e.message : "頼めませんでした", "error");
    }
    setBusy(false);
  };
  return (
    <form data-team-ask className="mb-3 min-w-0" onSubmit={(e) => { e.preventDefault(); if (text.trim() && !busy && !(addToThis && running)) void submit(); }}>
      <div className="flex min-w-0 gap-2">
        <input
          aria-label="チームリーダーに頼む" value={text} maxLength={20000} placeholder="チームリーダーに頼む（一文で）"
          onChange={(e) => setText(e.target.value)} className={`${inputClass} min-w-0 flex-1`}
        />
        <button type="submit" data-team-ask-submit disabled={!text.trim() || busy || (addToThis && running)} className={`${primaryButtonClass} shrink-0`}>頼む</button>
      </div>
      <MaterialAttach paths={paths} onChange={setPaths} />
      {canAdd && (
        <label className="mt-1.5 flex min-h-8 items-center gap-2 text-xs text-zinc-600 dark:text-zinc-300">
          <input type="checkbox" checked={addToThis} onChange={(e) => setAddToThis(e.target.checked)} />
          このチームへ頼み足す（チェックなしは新しいチームを作る）
        </label>
      )}
    </form>
  );
}

function Question({ team, qid, question, options }: { team: Team; qid: string; question: string; options: string[] }) {
  const show = useToasts((s) => s.show);
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(false);
  const send = async (value: string) => {
    setBusy(true);
    try {
      await answerLeaderQuestion(team.id, qid, value);
      setAnswer("");
      show("答えました");
    } catch (e) {
      show(e instanceof Error ? e.message : "答えられませんでした", "error");
    }
    setBusy(false);
  };
  return (
    <div data-team-question className="rounded-xl bg-amber-50 p-3 dark:bg-amber-950/30">
      <p className="break-words text-sm font-semibold text-amber-900 dark:text-amber-200">チームリーダーからの質問</p>
      <p className="mt-1 whitespace-pre-wrap break-words text-sm">{question}</p>
      {options.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-2">
          {options.map((o) => <button key={o} type="button" disabled={busy} onClick={() => void send(o)} className={secondaryButtonClass}>{o}</button>)}
        </div>
      )}
      <form className="mt-2 flex min-w-0 gap-2" onSubmit={(e) => { e.preventDefault(); if (answer.trim() && !busy) void send(answer.trim()); }}>
        <input aria-label="質問への答え" value={answer} maxLength={2000} onChange={(e) => setAnswer(e.target.value)} className={`${inputClass} min-w-0 flex-1`} />
        <button type="submit" disabled={!answer.trim() || busy} className={`${primaryButtonClass} shrink-0`}>答える</button>
      </form>
    </div>
  );
}

function PlanSection({ team, expert }: { team: Team; expert: boolean }) {
  const plan = team.plan;
  if (!plan || plan.items.length === 0) return null;
  const itemState = (item: PlanItem) => {
    const task = team.tasks.find((t) => t.id === item.task_id);
    return task ? taskPhrase(task) : "未着手";
  };
  return (
    <section data-team-plan className={SECTION}>
      <h3 className={SECTION_TITLE}>計画（{plan.items.length} 件）</h3>
      <ul className="grid gap-1">
        {plan.items.map((item) => (
          <li key={item.id} className="flex min-w-0 items-baseline gap-2 text-sm">
            <span className="shrink-0 rounded-full bg-accent-50 px-2 py-0.5 text-[10px] font-semibold text-accent-700 dark:bg-accent-900/30 dark:text-accent-300">{TIER_LABEL[item.tier]}</span>
            <span className="min-w-0 flex-1 break-words">{item.title}</span>
            <span className="shrink-0 text-xs text-zinc-500">{itemState(item)}</span>
            {expert && item.depends_on.length > 0 && <span className="shrink-0 text-[10px] text-zinc-400">← {item.depends_on.join(", ")}</span>}
          </li>
        ))}
      </ul>
      {plan.markdown && (
        <details className="mt-2 text-xs">
          <summary className="cursor-pointer text-zinc-500">計画の本文</summary>
          <div className="mt-1 max-h-72 overflow-auto rounded-lg bg-zinc-50 p-2 dark:bg-zinc-900"><Suspense fallback={<pre className="whitespace-pre-wrap text-xs">{plan.markdown}</pre>}><MarkdownMessage text={plan.markdown} /></Suspense></div>
        </details>
      )}
    </section>
  );
}

function ResultSection({ team, expert, onChanged }: { team: Team; expert: boolean; onChanged: () => void }) {
  const show = useToasts((s) => s.show);
  const [busy, setBusy] = useState(false);
  const result = team.result;
  if (!result) return null;
  const land = async () => {
    setBusy(true);
    try {
      await landTeam(team.id);
      onChanged();
      show("元のブランチへ取り込みました");
    } catch (e) {
      show(e instanceof Error ? e.message : "取り込めませんでした", "error");
    }
    setBusy(false);
  };
  const checks = result.checks ? { passed: "合格", failed: "不合格", error: "実行できず" }[result.checks.status] : "確認なし";
  return (
    <section data-team-result={result.outcome} className={`${SECTION} ${result.outcome === "done" ? "border-emerald-300 dark:border-emerald-800" : "border-red-300 dark:border-red-800"}`}>
      <h3 className={SECTION_TITLE}>結果（{result.outcome === "done" ? "完了" : "止まりました"}）</h3>
      {/* チームリーダーの報告は Markdown で書かれる（## や ** がそのまま見えていた） */}
      <Suspense fallback={<p className="whitespace-pre-wrap break-words text-sm">{result.summary}</p>}><MarkdownMessage text={result.summary} /></Suspense>
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        <dt className="text-zinc-500">確認の結果</dt><dd data-team-result-checks>{checks}</dd>
        <dt className="text-zinc-500">変更ファイル</dt>
        <dd data-team-result-changes>{result.changes ? `${result.changes.count} 件（+${result.changes.added} / -${result.changes.removed}）` : "なし"}</dd>
        <dt className="text-zinc-500">残り</dt><dd>{result.remaining.length ? result.remaining.join(" / ") : "なし"}</dd>
        {(expert || result.usage.cost_usd > 0) && <><dt className="text-zinc-500">使用量</dt>
          <dd>{result.usage.input_tokens.toLocaleString()} 入力・{result.usage.output_tokens.toLocaleString()} 出力 {fmtCost(result.usage.cost_usd)}</dd></>}
      </dl>
      {result.changes && result.changes.files.length > 0 && (
        <details className="mt-2 text-xs"><summary className="cursor-pointer text-zinc-500">変更ファイルの一覧</summary>
          <ul className="mt-1 grid gap-0.5 font-mono">{result.changes.files.map((f) => <li key={f} className="break-all">{f}</li>)}</ul></details>
      )}
      {result.can_land && (
        <div className="mt-3">
          <button type="button" data-team-land disabled={busy} onClick={() => void land()} className={`w-full ${primaryButtonClass}`}>取り込む</button>
          <p className="mt-1 text-[11px] text-zinc-500">ブランチ {result.team_branch} を {result.base_branch} へ merge します（作業場所に未コミットの変更があると断ります）。</p>
        </div>
      )}
      {result.landed && <p data-team-landed className="mt-2 text-xs font-semibold text-emerald-700 dark:text-emerald-400">元のブランチへ取り込み済みです。</p>}
    </section>
  );
}

/** 切のときは「詳しく」で畳み、入のときはそのまま並べる（依頼の全文・計画・進み具合・時系列）。 */
function Fold({ expert, children }: { expert: boolean; children: ReactNode }) {
  if (expert) return <>{children}</>;
  return (
    <details data-team-more className="group mb-3">
      <summary data-team-more-toggle className="flex min-h-11 cursor-pointer list-none items-center gap-1 rounded-xl px-1 text-sm font-semibold text-accent-700 dark:text-accent-300 [&::-webkit-details-marker]:hidden">
        詳しく
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round" aria-hidden className="h-4 w-4 transition-transform group-open:rotate-180"><path d="m6 9 6 6 6-6" /></svg>
      </summary>
      <div className="mt-2">{children}</div>
    </details>
  );
}

export function LeaderTimeline({ team, expert, onChanged, onHandoff }: { team: Team; expert: boolean; onChanged: () => void; onHandoff: (taskId: number) => void }) {
  const show = useToasts((s) => s.show);
  const run = team.leader_run;
  const pending = (team.questions ?? []).filter((q) => q.answer === null);
  const managed = team.tasks.filter((t) => t.leader_managed);
  const stuck = expert ? [] : team.tasks.filter((t) => t.status === "blocked");  // 板が見えるエキスパートでは板のカードに出る
  const request = team.requests?.[team.requests.length - 1]?.text ?? run?.goal ?? "";
  const failed = run?.status === "failed" || run?.status === "stopped" || run?.status === "blocked";
  const act = async (fn: () => Promise<unknown>, done: string) => {
    try { await fn(); onChanged(); show(done); } catch (e) { show(e instanceof Error ? e.message : "操作できませんでした", "error"); }
  };
  if (!run && !team.plan) return null;
  const rows = (team.timeline ?? []).filter((r) => expert || ["progress", "ask", "answer", "finish", "decision", "handoff", "resume", "stop", "integrate", "land"].includes(r.kind));
  return (
    <div data-team-timeline>
      {(pending.length > 0 || failed || stuck.length > 0) && (
        <section data-team-attention role="alert" className="mb-3 grid gap-2">
          {pending.map((q) => <Question key={q.id} team={team} qid={q.id} question={q.question} options={q.options} />)}
          {failed && (
            <div className="rounded-xl bg-red-50 p-3 text-sm dark:bg-red-950/40">
              <p className="break-words font-semibold text-red-700 dark:text-red-300">
                チームリーダーが{run?.status === "stopped" ? "止められました" : run?.status === "blocked" ? "完成できないと報告しました" : "止まりました"}
                {run?.reason ? `（${run.reason}）` : ""}
              </p>
              <button type="button" data-team-resume onClick={() => void act(() => resumeLeader(team.id), "再開しました")} className={`mt-2 ${secondaryButtonClass}`}>
                ここまでの状態から再開する
              </button>
            </div>
          )}
          {stuck.map((t) => (
            <div key={t.id} data-team-stuck={t.id} className="rounded-xl bg-red-50 p-3 text-sm dark:bg-red-950/40">
              <p className="break-words font-semibold text-red-700 dark:text-red-300">
                仕事「{t.title}」が止まっています（{t.blocked_reason === "no_handoff" ? "上限で止まり、引き継ぎ先がありません" : "確認が通らず、自動で直せませんでした"}）
              </p>
              {t.blocked_reason === "no_handoff" && (
                <button type="button" data-team-handoff-open={t.id} onClick={() => onHandoff(t.id)} className={`mt-2 ${primaryButtonClass}`}>引き継ぎ先を選ぶ</button>
              )}
            </div>
          ))}
        </section>
      )}
      {run?.status === "running" && (
        <p data-team-leader-run="running" role="status" className="mb-3 flex items-center justify-between gap-2 rounded-xl bg-accent-50 px-3 py-2 text-xs font-semibold text-accent-800 dark:bg-accent-950/40 dark:text-accent-200">
          <span>チームリーダーが進めています…{expert && run.endpoint ? `（${run.endpoint}）` : ""}</span>
          <button type="button" onClick={() => void act(() => stopLeader(team.id), "止めました")} className="shrink-0 text-xs font-semibold text-red-600 underline dark:text-red-400">止める</button>
        </p>
      )}
      <Fold expert={expert}>
        {request && (
          <section data-team-request className={SECTION}>
            <h3 className={SECTION_TITLE}>依頼</h3>
            <p className="whitespace-pre-wrap break-words text-sm">{request}</p>
          </section>
        )}
        <PlanSection team={team} expert={expert} />
        {(managed.length > 0 || rows.length > 0) && (
          <section data-team-progress className={SECTION}>
            <h3 className={SECTION_TITLE}>進み具合</h3>
            {managed.length > 0 && (
              <ul className="mb-2 grid gap-1">
                {managed.map((t) => (
                  <li key={t.id} data-team-progress-task={t.id} className="flex min-w-0 items-baseline gap-2 text-sm">
                    <span className="shrink-0 rounded-full bg-zinc-100 px-2 py-0.5 text-[10px] font-semibold text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">{TIER_LABEL[t.tier ?? "implement"]}</span>
                    <span className="min-w-0 flex-1 break-words">{t.title}
                      {/* 担当した（担当している）モデル。引き継ぎがあれば最後の担い手（利用者 2026-11-28） */}
                      {t.model_label && <span data-team-progress-model className="ml-1.5 whitespace-nowrap text-[11px] text-zinc-500 dark:text-zinc-400">· {t.model_label}</span>}
                    </span>
                    <span className="shrink-0 text-xs font-semibold text-zinc-600 dark:text-zinc-300">{taskPhrase(t)}</span>
                  </li>
                ))}
              </ul>
            )}
            <ol className="grid gap-0.5 text-xs text-zinc-600 dark:text-zinc-300">
              {rows.slice(-12).map((r, i) => (
                <li key={`${r.at}-${i}`} className="flex min-w-0 gap-2">
                  <time className="shrink-0 text-zinc-400">{new Date(r.at * 1000).toLocaleTimeString("ja-JP", { hour: "2-digit", minute: "2-digit" })}</time>
                  <span className="min-w-0 break-words">{r.text}</span>
                </li>
              ))}
            </ol>
          </section>
        )}
      </Fold>
      <ResultSection team={team} expert={expert} onChanged={onChanged} />
      {expert && <ExpertDetails team={team} />}
    </div>
  );
}

/** エキスパートだけ: 段の割り当て・チームリーダーの実行の記録・MCP の呼び出しの履歴・使用量・git。 */
function ExpertDetails({ team }: { team: Team }) {
  const calls = team.mcp_log ?? [];
  const usage = team.usage_total;
  return (
    <details data-team-expert-details className="mb-3 rounded-xl border border-zinc-200 p-3 text-xs dark:border-zinc-700">
      <summary className="cursor-pointer font-semibold text-zinc-600 dark:text-zinc-300">チームリーダーの詳細（エキスパート）</summary>
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
        <dt className="text-zinc-500">自律</dt><dd>{team.autonomy === "ask" ? "聞いてから進める" : "聞かずに最善で進める（判断は記録）"}</dd>
        <dt className="text-zinc-500">Web の調査</dt><dd>{team.web_research === false ? "禁止" : "許可"}</dd>
        <dt className="text-zinc-500">git</dt>
        <dd>{team.git?.enabled ? `${team.git.team_branch}（元: ${team.git.base_branch}${team.git.origin_was_git ? "" : "・Nexarch が git init"}）` : team.git?.reason || "未使用"}</dd>
        {usage && <><dt className="text-zinc-500">使用量の合計</dt><dd>{usage.input_tokens.toLocaleString()} 入力・{usage.output_tokens.toLocaleString()} 出力 {fmtCost(usage.cost_usd)}</dd></>}
      </dl>
      {(team.leader_runs ?? []).length > 0 && (
        <>
          <p className="mt-2 font-semibold text-zinc-500">チームリーダーの実行の記録</p>
          <ul className="grid gap-0.5">{team.leader_runs!.map((r, i) => <li key={i} className="break-words">{r.status}・{r.endpoint}{r.seconds ? `・${r.seconds}秒` : ""}{r.reason ? `（${r.reason}）` : ""}</li>)}</ul>
        </>
      )}
      <p className="mt-2 font-semibold text-zinc-500">MCP の呼び出しの履歴（道具の名前と成否だけ）</p>
      <ul data-team-mcp-log className="grid max-h-48 gap-0.5 overflow-auto font-mono">
        {calls.length === 0 && <li className="text-zinc-400">まだありません</li>}
        {calls.slice(-30).map((c, i) => <li key={i} className={c.ok ? "" : "text-red-600 dark:text-red-400"}>{c.tool} {c.ok ? "✓" : "✗"} {c.seconds}s</li>)}
      </ul>
    </details>
  );
}
