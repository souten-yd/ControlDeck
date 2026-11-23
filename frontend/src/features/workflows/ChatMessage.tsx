/** AssistantChat の発言まわりの表示部品（吹き出し・思考・作業の並び・統計など）。 */
import { lazy, Suspense, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import { IconSpeaker, IconSpeakerOff } from "../../components/icons";
import { NODE_TYPES } from "./nodeTypes";
import { ROLE_LABELS, type RoleKey } from "../opencode/RolesPanel";
import { extractPlanQuestions } from "./planQuestions";
import { PlanQuestionForm } from "./PlanQuestionForm";
import { CodeDoneCard, OpenCodeChanges } from "./OpenCodeChanges";
import { MessageActions, MessageEditor, type MessageAction } from "./MessageActions";
import { OpenCodeActivity, CODE_STEPS_CARD, ToolRow } from "./OpenCodeActivity";
import { MODES, type GenData, type GenStats, type Msg, type Quality } from "./chatState";

// Markdown（表・見出し・コード）と TeX の式。ライブラリは回答を描くときに初めて読む。
const MarkdownMessage = lazy(() => import("./MarkdownMessage"));

/** トークン数の短縮表記（3214 → 3.2K）。 */
function fmtTok(n: number): string {
  if (n < 1000) return String(n);
  if (n < 10000) return `${(n / 1000).toFixed(1)}K`;
  return `${Math.round(n / 1000)}K`;
}

/** 生成統計の左下表示: 思考状態 / tok/s / コンテキスト使用量。
 * ヒント文と同じ行高に収まる枠なしのプレーン表示（入力欄の位置を動かさない）。 */
const PHASE_LABEL: Record<string, string> = {
  loading: "モデル準備中", waiting: "応答待ち", retrieve: "資料を検索中", prefill: "プリフィル中", thinking: "思考中", answer: "生成中", answering: "生成中",
  tool: "道具を実行中", background: "裏のエージェント作業中", retry: "再試行待ち", code: "OpenCode 実行中",
};

export function GenStatsBadge({ stats, busy }: {
  stats: GenStats | null;
  busy: boolean;
}) {
  // いまの段階に入ってからの秒数（プリフィルやモデルの読み込みは速度が出ないので、動いていることを時間で見せる）
  const [startedAt, setStartedAt] = useState(() => Date.now());
  const [, tick] = useState(0);
  const phaseKey = `${stats?.phase ?? ""}:${stats?.detail ?? ""}`;
  useEffect(() => setStartedAt(Date.now()), [phaseKey]);
  useEffect(() => {
    if (!busy) return;
    const timer = window.setInterval(() => tick((n) => n + 1), 1000);
    return () => window.clearInterval(timer);
  }, [busy]);
  const row = "num flex min-w-0 items-center gap-1.5 overflow-hidden whitespace-nowrap";
  const sep = <span className="text-zinc-300 dark:text-zinc-600">·</span>;
  const elapsed = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
  if (!stats) {
    if (!busy) return null;
    return (
      <span className={row} data-gen-phase="sending">
        <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-amber-500" />送信中{sep}{elapsed} 秒
      </span>
    );
  }
  const done = stats.phase === "done" || !busy;
  const label = done ? "完了" : stats.phase === "tool" && stats.detail ? `${stats.detail} を実行中` : PHASE_LABEL[stats.phase] ?? "生成中";
  const dot = done ? "bg-zinc-400" : stats.phase === "thinking" ? "animate-pulse bg-violet-500"
    : ["loading", "waiting", "prefill", "retry"].includes(stats.phase) ? "animate-pulse bg-amber-500" : "animate-pulse bg-emerald-500";
  const used = (stats.promptTokens ?? 0) + stats.genTokens;
  const pct = stats.contextMax ? Math.min(100, (used / stats.contextMax) * 100) : 0;
  return (
    <span className={`${row} ${done ? "opacity-60" : ""}`} data-gen-phase={done ? "done" : stats.phase} title={stats.detail || undefined}>
      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${dot}`} />
      {label}
      {!done && <>{sep}{elapsed} 秒</>}
      {stats.tokPerSec > 0 && <>{sep}{stats.tokPerSec.toFixed(1)} tok/s</>}
      {stats.contextMax ? (
        <>{sep}<span className="flex items-center gap-1.5" title={`コンテキスト ${used.toLocaleString()} / ${stats.contextMax.toLocaleString()} トークン`}>
          <span className="h-1 w-10 shrink-0 overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-700">
            <span className="block h-full rounded-full bg-accent-500 transition-[width] duration-500" style={{ width: `${pct}%` }} />
          </span>
          {fmtTok(used)}/{fmtTok(stats.contextMax)}
        </span></>
      ) : stats.genTokens > 0 ? (
        <>{sep}<span>{fmtTok(stats.genTokens)} tok</span></>
      ) : null}
    </span>
  );
}

/** 思考プロセス（推論モデル・折り畳み）。2026-11-18 利用者の依頼:
 *  見出しのタップで開閉し、開いた欄の中のタップでも閉じる（長い思考の下から上へ戻らずに済む）。
 *  考え始めたら 1 回だけ自動で開き、回答が始まったら自動で閉じる（利用者が触っていれば、どちらもしない）。
 *  閉じたら会話の欄の中だけをスクロールして見出しの位置まで戻す（iPhone の外側の画面は動かさない）。
 *  欄の中のタップは、ダブルクリックや文字の選択の途中では閉じない（コピーできるように）。 */
function ThinkingBlock({ text, thinking, flat = false }: { text: string; thinking: boolean; /** 作業のカードの 1 行として出す */ flat?: boolean }) {
  const [open, setOpen] = useState(thinking);
  const touched = useRef(false);
  const autoOpened = useRef(thinking);
  const head = useRef<HTMLButtonElement>(null);
  const pendingClose = useRef<number | null>(null);
  useEffect(() => {
    if (touched.current) return;
    if (thinking && !autoOpened.current) {
      autoOpened.current = true;
      setOpen(true);
    } else if (!thinking) {
      setOpen(false);   // 回答が始まった（または回答済み）
    }
  }, [thinking]);
  useEffect(() => () => { if (pendingClose.current) window.clearTimeout(pendingClose.current); }, []);
  const scrollBackToHead = () => {
    const button = head.current;
    const scroller = button?.closest<HTMLElement>("[data-assistant-messages]");
    if (!button || !scroller) return;
    const top = button.getBoundingClientRect().top - scroller.getBoundingClientRect().top;
    if (top < 0) scroller.scrollTop += top - 8;
  };
  const close = () => {
    touched.current = true;
    setOpen(false);
    window.requestAnimationFrame(scrollBackToHead);
  };
  const toggle = () => {
    touched.current = true;
    if (open) close();
    else setOpen(true);
  };
  return (
    <div data-thinking className={flat ? "px-3 py-0.5" : "rounded-lg bg-zinc-100/70 px-2.5 py-1.5 dark:bg-zinc-800/70"}>
      <button ref={head} type="button" aria-expanded={open} onClick={toggle}
        className="flex min-h-9 w-full items-center gap-1 text-left text-xs font-medium text-zinc-600 focus:outline-none focus-visible:underline dark:text-zinc-400">
        <span aria-hidden className={`inline-block transition-transform ${open ? "rotate-90" : ""}`}>▸</span>
        💭 思考プロセス{thinking ? "（考え中…）" : ""}
      </button>
      {open && (
        <p data-thinking-body title="タップで閉じる"
          onClick={(event) => {
            // 1 回目のクリックだけで判断しない（ダブルクリックで語を選ぶ途中で閉じない）
            if (pendingClose.current) window.clearTimeout(pendingClose.current);
            pendingClose.current = null;
            if (event.detail !== 1) return;
            pendingClose.current = window.setTimeout(() => {
              pendingClose.current = null;
              if (!window.getSelection()?.toString()) close();
            }, 280);
          }}
          className="mb-1.5 mt-0.5 cursor-pointer whitespace-pre-wrap text-xs leading-relaxed text-zinc-600 dark:text-zinc-300">
          {text}
        </p>
      )}
    </div>
  );
}

/** 作業の間に置ける本文の終わりの位置（0 は置けない）。古い記録は位置が合わないので従来どおり下にまとめる。
 *  後ろに残る分（許可されなかった旨など、並びに載らない追記）は呼び出し側が下に出す。
 *  コードの囲みが区切りをまたぐと、別々に表示して崩れるので、まとめて出す。 */
function inlineTextEnd(msg: Msg): number {
  const segments = msg.timeline ?? [];
  let at = 0;
  for (const [i, seg] of segments.entries()) {
    if (seg.k !== "x") continue;
    if (seg.from !== at || seg.to > msg.content.length) return 0;
    const fences = msg.content.slice(seg.from, seg.to).match(/^ {0,3}(```|~~~)/gm)?.length ?? 0;
    if (i < segments.length - 1 && fences % 2 === 1) return 0;
    at = seg.to;
  }
  return at;
}

/** 道具の羅列を畳む 1 段の件数 */
const FOLD_STEPS = 8;

/** OpenCode の回答を届いた順に並べる。続く思考と道具は 1 枚のカードに、本文は作業の前後の位置に出す
 *  （2026-11-18 利用者「チャットウィンドウの視認性を向上させて」。段階ごとの文が 1 段落につながっていた）。
 *  許可の確認は最後に出す。 */
function CodeTimeline({ msg, textEnd, onAnswer }: {
  msg: Msg; textEnd: number; onAnswer: (requestId: string, decision: "once" | "always" | "reject") => Promise<void>;
}) {
  const segments = msg.timeline ?? [];
  const tools = new Map((msg.tools ?? []).map((t) => [t.id, t]));
  const thinking = msg.thinking ?? "";
  const lastThinking = segments.reduce((last, seg, i) => (seg.k === "t" ? i : last), -1);
  const blocks: ReactNode[] = [];
  let steps: ReactNode[] = [];
  let stepsFrom = 0;   // カードの鍵は最初の項目の位置（本文が続いても作り直さず、開いた行を保つ）
  const addStep = (i: number, step: ReactNode) => {
    if (!steps.length) stepsFrom = i;
    steps.push(step);
  };
  // 長い道具の羅列は FOLD_STEPS 件ごとの段に畳む（開いているのは、いま動いている最後の段だけ）。
  // 2026-11-23 利用者の通し: 道具の呼び出しが長く、いま何をしているか一目で分からない
  const flushSteps = (live = false) => {
    if (steps.length <= FOLD_STEPS) {
      if (steps.length) blocks.push(<div key={`steps-${stepsFrom}`} data-code-steps className={CODE_STEPS_CARD}>{steps}</div>);
    } else {
      for (let at = 0; at < steps.length; at += FOLD_STEPS) {
        const chunk = steps.slice(at, at + FOLD_STEPS);
        const last = at + FOLD_STEPS >= steps.length;
        if (last && live) {
          blocks.push(<div key={`steps-${stepsFrom}-${at}`} data-code-steps className={CODE_STEPS_CARD}>{chunk}</div>);
        } else {
          blocks.push(
            <details key={`steps-${stepsFrom}-${at}`} data-code-steps-folded className={CODE_STEPS_CARD}>
              <summary className="flex min-h-11 cursor-pointer items-center px-3 text-xs text-zinc-600 dark:text-zinc-300">
                作業 {at + 1}〜{at + chunk.length} 件目（{chunk.length} 件・タップで開く）
              </summary>
              <div className="divide-y divide-zinc-100 dark:divide-zinc-800">{chunk}</div>
            </details>,
          );
        }
      }
    }
    steps = [];
  };
  segments.forEach((seg, i) => {
    if (seg.k === "u") {
      const tool = tools.get(seg.id);
      if (tool) addStep(i, <ToolRow key={tool.id} tool={tool} />);
      return;
    }
    if (seg.k === "n") {
      addStep(i, <p key={`steer-${i}`} data-code-steer className="px-3 py-2 text-xs leading-relaxed text-zinc-700 dark:text-zinc-200">
        <span className="font-semibold text-accent-700 dark:text-accent-300">↪ 途中で伝えた</span> <span className="whitespace-pre-wrap">{seg.text}</span></p>);
      return;
    }
    if (seg.k === "x") {
      const text = msg.content.slice(seg.from, seg.to);
      if (!textEnd || !text.trim()) return;
      flushSteps();
      blocks.push(
        <div key={`text-${i}`} data-code-text>
          <Suspense fallback={<p className="whitespace-pre-wrap leading-relaxed">{text}</p>}>
            <MarkdownMessage text={text} />
          </Suspense>
        </div>,
      );
      return;
    }
    const text = thinking.slice(seg.from, seg.to);
    if (!text.trim()) return;
    // 考えている途中なのは、最後の思考で、その後ろに道具も本文も無いときだけ
    const active = Boolean(msg.streaming) && i === lastThinking && i === segments.length - 1;
    addStep(i, <ThinkingBlock key={`think-${i}`} text={text} thinking={active} flat />);
  });
  flushSteps(Boolean(msg.streaming));
  // 並びに載っていない道具（古い記録・途中から購読）も落とさない
  const listed = new Set(segments.flatMap((seg) => (seg.k === "u" ? [seg.id] : [])));
  const rest = (msg.tools ?? []).filter((t) => !listed.has(t.id));
  return (
    <>
      {blocks}
      {rest.length > 0 && <OpenCodeActivity tools={rest} permissions={[]} onAnswer={onAnswer} />}
      {(msg.permissions?.length ?? 0) > 0 && <OpenCodeActivity tools={[]} permissions={msg.permissions} onAnswer={onAnswer} />}
    </>
  );
}

/** OpenCode の役割ごとに実際に使われるモデル（既存の role_info。設定 → オプション機能で変える）。開いたときだけ読む。 */
export function RoleModels() {
  const [open, setOpen] = useState(false);
  const { data } = useQuery({
    queryKey: ["opencode-role-info"],
    queryFn: () => api<{ role_info?: { effective?: Partial<Record<RoleKey, { model: string; explicit: boolean }>> } }>("/opencode/workflow"),
    enabled: open, staleTime: 30_000,
  });
  const effective = data?.role_info?.effective ?? {};
  return (
    <details data-role-models open={open} onToggle={(e) => setOpen(e.currentTarget.open)} className="min-w-0 rounded-lg border border-zinc-200 bg-white px-2.5 py-2 text-xs dark:border-zinc-700 dark:bg-zinc-900 sm:col-span-2">
      <summary className="cursor-pointer select-none font-medium text-zinc-600 dark:text-zinc-300">OpenCode の役割ごとのモデル</summary>
      <ul className="mt-1.5 grid gap-x-4 gap-y-0.5 sm:grid-cols-2">
        {ROLE_LABELS.filter(([key]) => effective[key]).map(([key, label]) => (
          <li key={key} className="flex min-w-0 gap-1.5"><span className="shrink-0 text-zinc-500">{label}</span>
            <span title={effective[key]!.model} className="min-w-0 truncate font-mono">{effective[key]!.model}{effective[key]!.explicit ? "" : "（既定）"}</span></li>
        ))}
      </ul>
    </details>
  );
}

export function MessageBubble({
  msg,
  onRegister,
  onAutoBuild,
  onOpen,
  onReference,
  onAnswerPermission,
  onBuildFromPlan,
  onProceedRecommended,
  editing = false,
  actionsDisabled = false,
  onAction,
  onResend,
  onCancelEdit,
  onRetry,
  resendDisabled = false,
  conversationId = "",
  canRevertChanges = false,
  onChangesReverted,
}: {
  conversationId?: string;
  /** 最新の OpenCode の回で、作業が止まっているときだけ */
  canRevertChanges?: boolean;
  onChangesReverted?: (messageId: string) => void;
  /** 失敗・中断した回答をやり直す（直前の自分の発言を送り直す）。生成中は渡さない。 */
  onRetry?: () => void;
  /** プランの回答の後で、そのプランをビルドで実装する */
  onBuildFromPlan?: () => void;
  /** 本文の「決めてもらいたいこと」への答えを送る（build=true はビルドへ切り替える。違えば同じ会話・同じ agent） */
  onProceedRecommended?: (text: string, build: boolean) => void;
  resendDisabled?: boolean;
  editing?: boolean;
  actionsDisabled?: boolean;
  onAction: (action: MessageAction) => void;
  onResend: (text: string) => void;
  onCancelEdit: () => void;
  msg: Msg;
  onRegister: (g: GenData) => void;
  onAutoBuild: (g: GenData, useExisting: boolean) => void;
  onOpen: (id: number) => void;
  onReference: (referenceId: string) => void;
  onAnswerPermission: (messageId: string, requestId: string, decision: "once" | "always" | "reject") => Promise<void>;
}) {
  if (msg.role === "user") {
    return (
      <div className="flex min-w-0 justify-end">
        <div className="flex min-w-0 max-w-[85%] flex-col items-end gap-1.5">
          {msg.archives && msg.archives.length > 0 && (
            <div className="flex flex-wrap justify-end gap-1.5" aria-label="送った圧縮ファイル">
              {msg.archives.map((name) => (
                <span key={name} className="flex max-w-full items-center gap-1 rounded-full border border-zinc-200 bg-white px-2.5 py-1 text-[11px] text-zinc-600 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300">
                  <span aria-hidden>📦</span><span className="min-w-0 truncate">{name}</span>
                </span>
              ))}
            </div>
          )}
          {msg.images && msg.images.length > 0 && (
            <div className="flex flex-wrap justify-end gap-1.5">
              {msg.images.map((src) => (
                <a key={src} href={src} target="_blank" rel="noopener noreferrer">
                  <img src={src} alt="添付した画像" loading="lazy"
                    className="max-h-40 max-w-[12rem] rounded-xl border border-zinc-200 object-contain dark:border-zinc-700" />
                </a>
              ))}
            </div>
          )}
          {editing ? (
            <MessageEditor initial={msg.content} disabled={resendDisabled} onSubmit={onResend} onCancel={onCancelEdit} />
          ) : (
            <div className="min-w-0 whitespace-pre-wrap break-words rounded-2xl rounded-br-md bg-accent-600 px-3.5 py-2.5 text-[15px] leading-6 text-white [overflow-wrap:anywhere]">
              {msg.content}
            </div>
          )}
          {!editing && (
            <MessageActions align="end" disabled={actionsDisabled}
              actions={msg.messageId ? ["copy", "edit", "fork", "delete"] : ["copy"]} onSelect={onAction} />
          )}
        </div>
      </div>
    );
  }
  const recommendations = onProceedRecommended && !msg.streaming ? extractPlanQuestions(msg.content) : [];
  const unsettled = !msg.streaming && (msg.persistStatus === "failed" || msg.persistStatus === "canceled" || msg.persistStatus === "interrupted")
    ? msg.persistStatus : null;
  const textEnd = msg.timeline?.length && msg.messageId ? inlineTextEnd(msg) : 0;
  const restText = msg.content.slice(textEnd);
  const assistantActions: MessageAction[] = (msg.messageId && !msg.streaming ? ["copy", "fork", "delete"] as MessageAction[] : ["copy"] as MessageAction[])
    .filter((action) => action !== "copy" || !!msg.content);
  // 中断・止めた・失敗した OpenCode の回だけ「続きから」を出す（同じセッションで残りを続けさせる）。
  // 再開し終えた回（resumed）には出さない — 続け直すときは新しい回の「続きから」を使う
  if (!msg.streaming && msg.messageId && msg.answeredBy === "code" && unsettled && !msg.resumed) {
    assistantActions.splice(1, 0, "resume");
  }
  // 自動保存した回には「この時点に戻す」（コードの保存の時点がある回だけ）
  if (!msg.streaming && msg.messageId && msg.checkpoint) assistantActions.splice(assistantActions.length - 1, 0, "restore");
  return (
    <div data-assistant-message={msg.persistStatus ?? ""} className="flex min-w-0 flex-col items-start gap-0.5">
    <div className="flex w-full min-w-0 justify-start">
      <div className="w-full min-w-0 space-y-2 break-words px-1 text-[15px] leading-7 [overflow-wrap:anywhere]">
        {msg.autoHandoff !== undefined && (
          <p data-auto-handoff className="inline-flex max-w-full items-center gap-1 rounded-full border border-emerald-300 bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold leading-5 text-emerald-800 dark:border-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-200">
            <span aria-hidden>🛠️</span><span className="truncate">自動で OpenCode に任せました{msg.autoHandoff ? `：${msg.autoHandoff}` : ""}</span>
          </p>
        )}
        {msg.confidentialAnswer && (
          <p data-secret-notice role="status" className="text-[11px] text-zinc-500 dark:text-zinc-400">
            🔒 外部への調査は止めています（秘密の会話）
          </p>
        )}
        {msg.agent === "plan" && (
          <p data-plan-answer className="inline-flex items-center gap-1 rounded-full border border-sky-300 bg-sky-50 px-2 py-0.5 text-[11px] font-semibold leading-5 text-sky-800 dark:border-sky-700 dark:bg-sky-950/50 dark:text-sky-200">
            <span aria-hidden>📋</span>プラン（ファイルは変えていません）
          </p>
        )}
        {msg.compaction && (
          <p className="text-[11px] text-zinc-500 dark:text-zinc-400" role="status">
            🗜️ 会話が長くなったため、古いやり取り{msg.compaction.folded_messages ? `（${msg.compaction.folded_messages}件）` : ""}を要約して続けました
            {msg.compaction.tokens_before && msg.compaction.tokens_after
              ? `：約${msg.compaction.tokens_before.toLocaleString()} → ${msg.compaction.tokens_after.toLocaleString()}トークン`
              : ""}
          </p>
        )}
        {msg.connectionState === "reconnecting" && (
          <p className="text-[11px] font-medium text-amber-600 dark:text-amber-400" role="status">接続を復旧しています。生成はサーバー側で継続中です…</p>
        )}
        {(msg.plan || msg.research || (msg.progress && msg.progress.length > 0)) && (
          /* 機能選択判断は選択結果のみ表示し、理由・計画・統計はタップで展開する */
          <details className="w-fit max-w-full rounded-xl border border-accent-200 bg-accent-50/50 px-3 py-1.5 open:w-full dark:border-accent-900 dark:bg-accent-950/20">
            <summary className="cursor-pointer text-xs font-semibold text-accent-700 dark:text-accent-300">
              {msg.progress?.[0]?.phase === "opencode" ? "⌨️ OpenCode" : `🧭 ${MODES.find((m) => m.id === msg.plan?.mode)?.label ?? "調査"}`}{msg.streaming ? "（実行中）" : ""}
            </summary>
            {msg.plan?.reason && (
              <p className="mt-2 text-[11px] text-zinc-600 dark:text-zinc-300">判断理由: {msg.plan.reason}</p>
            )}
            {msg.plan?.steps && msg.plan.steps.length > 0 && (
              <ol className="mt-2 space-y-1 text-[11px] text-zinc-600 dark:text-zinc-300">
                {msg.plan.steps.map((step, index) => <li key={`${step.tool}-${index}`}>{index + 1}. {step.tool === "web" ? "Web" : "学術"}: {step.query}</li>)}
              </ol>
            )}
            {msg.progress && msg.progress.length > 0 && (
              <p className="mt-2 text-[11px] text-zinc-500">最新: {msg.progress[msg.progress.length - 1].label}</p>
            )}
            {msg.research && (
              <div className="mt-2 grid grid-cols-2 gap-1 text-[11px] text-zinc-600 dark:text-zinc-300 sm:grid-cols-4">
                <span>探索 {msg.research.rounds} round</span>
                <span>検索 {msg.research.search_calls} 回</span>
                <span>発見 {msg.research.sources_discovered} 件</span>
                <span>採用 {msg.research.sources_selected} 件</span>
                {msg.research.repositories_inspected > 0 && <span>GitHub {msg.research.repositories_inspected} repo</span>}
                {msg.research.coverage?.coverage_score !== undefined && <span>coverage {msg.research.coverage.coverage_score}%</span>}
                {msg.research.citation_metrics?.citation_coverage !== undefined && <span>引用段落 {Math.round(msg.research.citation_metrics.citation_coverage * 100)}%</span>}
                {msg.research.citation_metrics?.section_count !== undefined && <span>完結章 {msg.research.citation_metrics.completed_sections}/{msg.research.citation_metrics.section_count}</span>}
                {msg.research.context_profile?.requested_tokens && (
                  <span>CTX {Math.round(msg.research.context_profile.requested_tokens / 1024)}K {msg.research.context_profile.applied ? "適用" : "未適用"}</span>
                )}
              </div>
            )}
            {(msg.research?.citation_metrics?.possibly_truncated_sections?.length ?? 0) > 0 && (
              <p className="mt-2 text-[11px] text-amber-600 dark:text-amber-400">未完結の可能性: {msg.research!.citation_metrics!.possibly_truncated_sections!.join("、")}</p>
            )}
          </details>
        )}
        {msg.timeline?.length && msg.messageId ? (
          // OpenCode: 届いた順に「思考 → 道具 → 思考 → …」。思考はひとまとまりごとに別の窓
          <CodeTimeline msg={msg} textEnd={textEnd} onAnswer={(requestId, decision) => onAnswerPermission(msg.messageId!, requestId, decision)} />
        ) : (
          <>
            {/* OpenCode: 道具の実行と許可の確認（タップで出力を開く・許可はボタンで答える） */}
            {msg.messageId && (
              <OpenCodeActivity tools={msg.tools} permissions={msg.permissions}
                onAnswer={(requestId, decision) => onAnswerPermission(msg.messageId!, requestId, decision)} />
            )}
            {/* 思考トレース（推論モデル・折り畳み） */}
            {msg.thinking && (
              <ThinkingBlock text={msg.thinking} thinking={Boolean(!msg.content && msg.streaming)} />
            )}
          </>
        )}
        {msg.streaming && !msg.content && !msg.thinking && !msg.build && (
          <p role="status" aria-label="回答を待っています" className="flex h-7 items-center gap-1">
            {[0, 1, 2].map((dot) => (
              <span key={dot} className="h-1.5 w-1.5 animate-pulse rounded-full bg-zinc-400" style={{ animationDelay: `${dot * 180}ms` }} />
            ))}
          </p>
        )}
        {restText && (
          <div>
            <Suspense fallback={<p className="whitespace-pre-wrap leading-relaxed">{restText}</p>}>
              <MarkdownMessage text={restText} />
            </Suspense>
            {msg.streaming && <span className="ml-0.5 inline-block h-3.5 w-1.5 animate-pulse bg-accent-500 align-middle" />}
          </div>
        )}
        {/* 本文を作業の間に出した後も、続いていることが分かるように（道具の後で長く考えている間など） */}
        {msg.streaming && msg.content && !restText && <span aria-hidden data-streaming-cursor className="block h-3.5 w-1.5 animate-pulse bg-accent-500" />}

        {!msg.streaming && msg.messageId && msg.answeredBy === "code" && !unsettled && (
          <CodeDoneCard content={msg.content} todos={msg.todos} changeCount={msg.changes?.length ?? 0} />
        )}
        {!msg.streaming && msg.messageId && conversationId && (msg.changes?.length ?? 0) > 0 && (
          <OpenCodeChanges conversationId={conversationId} messageId={msg.messageId} changes={msg.changes!} reverted={msg.reverted}
            canRevert={canRevertChanges} onReverted={() => onChangesReverted?.(msg.messageId!)} />
        )}

        {/* 失敗・停止・中断: 本文が無くても、何が起きたかと理由を必ず出す。 */}
        {unsettled && <UnsettledNotice status={unsettled} error={msg.error} partial={!!msg.content} onRetry={onRetry} />}

        {/* 出典リスト（既定は最小化。タップで開閉） */}
        {msg.sources && msg.sources.length > 0 && (
          <details className="rounded-xl border border-zinc-200 bg-white px-2.5 py-2 dark:border-zinc-700 dark:bg-zinc-900">
            <summary className="cursor-pointer text-[11px] font-semibold text-zinc-500 dark:text-zinc-400">
              📚 参照文献（{msg.sources.length} 件）
            </summary>
            <ol className="mt-1.5 space-y-1">
              {msg.sources.map((s, i) => (
                <li key={s.reference_id ?? i} className="flex min-w-0 items-center gap-1.5 text-xs">
                  {s.reference_id && (
                    <span className="shrink-0 rounded-md bg-accent-50 px-1.5 py-0.5 font-mono font-semibold text-accent-700 dark:bg-accent-950/50 dark:text-accent-300">
                      [{s.reference_id}]
                    </span>
                  )}
                  <span className="min-w-0 flex-1 truncate">
                    <a href={s.url} target="_blank" rel="noreferrer" className="text-accent-600 hover:underline dark:text-accent-400">
                      {s.title || s.url}
                    </a>
                    {s.source && <span className="ml-1 text-zinc-400">({s.source})</span>}
                  </span>
                  {s.reference_id && (
                    <button
                      type="button"
                      onClick={() => onReference(s.reference_id!)}
                      className="h-9 shrink-0 rounded-md px-1.5 font-medium text-zinc-500 hover:bg-zinc-100 hover:text-accent-700 focus:outline-none focus:ring-2 focus:ring-accent-500/40 dark:hover:bg-zinc-800 dark:hover:text-accent-300"
                      aria-label={`${s.reference_id}を入力欄で参照`}
                    >
                      参照
                    </button>
                  )}
                </li>
              ))}
            </ol>
          </details>
        )}

        {/* 生成プレビュー */}
        {msg.gen && (
          <div className="rounded-xl border border-zinc-200 bg-white p-3 dark:border-zinc-700 dark:bg-zinc-900">
            <p className="text-sm font-medium">⚙️ {msg.gen.name}</p>
            <div className="mt-2 flex flex-wrap items-center gap-1">
              {msg.gen.definition.nodes.map((n, i) => {
                const def = NODE_TYPES[n.type];
                return (
                  <span key={n.id ?? i} className="flex items-center gap-1">
                    {i > 0 && <span className="text-zinc-300 dark:text-zinc-600">→</span>}
                    <span className="inline-flex items-center gap-1 rounded-md bg-zinc-100 px-1.5 py-0.5 text-[11px] dark:bg-zinc-800">
                      {def?.icon ?? "▢"} {n.name || def?.label || n.type}
                    </span>
                  </span>
                );
              })}
            </div>
            {msg.gen.quality && <QualityBadge q={msg.gen.quality} />}
            {msg.gen.warnings.length > 0 && (
              <p className="mt-2 whitespace-pre-wrap rounded-lg bg-amber-50 p-2 text-xs text-amber-700 dark:bg-amber-500/10 dark:text-amber-300">
                {msg.gen.warnings.join("\n")}
              </p>
            )}
            <div className="mt-2.5 flex flex-wrap gap-1.5">
              {msg.gen.valid && (
                <button onClick={() => onRegister(msg.gen!)} className="rounded-lg bg-accent-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-accent-700">
                  登録してエディタで開く
                </button>
              )}
              <button
                onClick={() => onAutoBuild(msg.gen!, msg.gen!.valid)}
                className="rounded-lg border border-accent-300 px-3 py-1.5 text-xs font-medium text-accent-700 hover:bg-accent-50 dark:border-accent-700 dark:text-accent-400 dark:hover:bg-accent-600/10"
              >
                🚀 自動ビルド（登録 → 動作確認 → 自動修正）
              </button>
            </div>
          </div>
        )}

        {/* ビルドログ */}
        {msg.build && (
          <div className="rounded-xl border border-zinc-200 bg-white p-3 dark:border-zinc-700 dark:bg-zinc-900">
            <ul className="space-y-1 text-xs leading-relaxed">
              {msg.build.lines.map((l, i) => (
                <li key={i} className="whitespace-pre-wrap">
                  {l}
                </li>
              ))}
            </ul>
            {!msg.build.done && <p className="mt-1.5 animate-pulse text-[11px] text-zinc-400">実行中...</p>}
            {msg.build.quality && <QualityBadge q={msg.build.quality} />}
            {msg.build.done && msg.build.workflowId != null && (
              <button
                onClick={() => onOpen(msg.build!.workflowId!)}
                className="mt-2 rounded-lg bg-accent-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-accent-700"
              >
                エディタで開く
              </button>
            )}
          </div>
        )}
      </div>
    </div>
    {recommendations.length > 0 && <PlanQuestionForm questions={recommendations} isPlan={msg.agent === "plan"} onSubmit={onProceedRecommended!} />}
    {onBuildFromPlan && recommendations.length === 0 && (
      <button type="button" data-build-from-plan onClick={onBuildFromPlan}
        className="mt-1 inline-flex min-h-10 items-center gap-1.5 rounded-xl bg-accent-600 px-3.5 text-sm font-semibold text-white shadow-sm hover:bg-accent-700 [@media(pointer:coarse)]:min-h-11">
        <span aria-hidden>🛠️</span>このプランで作る（ビルドに切り替えて送る）
      </button>
    )}
    {!msg.streaming && assistantActions.length > 0 && (msg.content || msg.messageId) && <MessageActions align="start" disabled={actionsDisabled} actions={assistantActions} onSelect={onAction} />}
    </div>
  );
}

/** 最後まで出なかった回答の知らせ。失敗は赤（重大なエラー）、停止・中断は控えめな色。 */
function UnsettledNotice({ status, error, partial, onRetry }: {
  status: "failed" | "canceled" | "interrupted"; error?: string; partial: boolean; onRetry?: () => void;
}) {
  const failed = status === "failed";
  const title = failed
    ? (partial ? "回答の途中で失敗しました" : "回答を生成できませんでした")
    : status === "canceled" ? "回答を停止しました" : "回答が中断されました";
  const detail = error?.trim()
    || (status === "interrupted" ? "サーバーの再起動などで生成が途切れました。" : failed ? "原因は記録されていません。" : "");
  return (
    <div
      role={failed ? "alert" : "status"}
      data-assistant-unsettled={status}
      className={`rounded-2xl border px-3.5 py-3 text-sm leading-6 ${failed
        ? "border-red-200 bg-red-50 text-red-800 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-200"
        : "border-zinc-200 bg-zinc-100/70 text-zinc-700 dark:border-zinc-700 dark:bg-zinc-800/60 dark:text-zinc-200"}`}
    >
      <p className="font-semibold">{failed ? "⚠️ " : status === "canceled" ? "⏹ " : "⏸ "}{title}</p>
      {detail && <p className="mt-0.5 whitespace-pre-wrap break-words text-xs leading-relaxed opacity-90 [overflow-wrap:anywhere]">{detail}</p>}
      {onRetry && (
        <button type="button" onClick={onRetry}
          className={`mt-2 inline-flex min-h-11 items-center gap-1.5 rounded-xl border px-3.5 text-sm font-medium transition focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500/40 ${failed
            ? "border-red-300 bg-white text-red-700 hover:bg-red-100 dark:border-red-800 dark:bg-red-950/40 dark:text-red-200 dark:hover:bg-red-900/40"
            : "border-zinc-300 bg-white text-zinc-800 hover:bg-zinc-50 dark:border-zinc-600 dark:bg-zinc-900 dark:text-zinc-100 dark:hover:bg-zinc-800"}`}>
          <span aria-hidden>↻</span>再試行
        </button>
      )}
    </div>
  );
}

/** 生成ワークフローの品質スコア表示（0-100 + 内訳 + 検証結果）。 */
function QualityBadge({ q }: { q: Quality }) {
  const color =
    q.score >= 85 ? "text-emerald-600 dark:text-emerald-400" :
    q.score >= 60 ? "text-amber-600 dark:text-amber-400" : "text-red-600 dark:text-red-400";
  const bar =
    q.score >= 85 ? "bg-emerald-500" : q.score >= 60 ? "bg-amber-500" : "bg-red-500";
  return (
    <div className="mt-2 rounded-lg border border-zinc-200 p-2 dark:border-zinc-700">
      <div className="flex items-center gap-2">
        <span className={`num text-sm font-bold ${color}`}>{q.score}</span>
        <span className="text-[11px] text-zinc-500">/100 · {q.label}</span>
        <div className="ml-auto h-1.5 w-24 overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-700">
          <div className={`h-full rounded-full ${bar}`} style={{ width: `${q.score}%` }} />
        </div>
      </div>
      <details className="mt-1">
        <summary className="cursor-pointer text-[10px] text-zinc-400">内訳と検証結果</summary>
        <ul className="mt-1 space-y-0.5 text-[10px] text-zinc-500">
          {Object.entries(q.breakdown).map(([k, v]) => (
            <li key={k} className="flex justify-between"><span>{k}</span><span className="num">{v}</span></li>
          ))}
          {q.errors.map((e, i) => <li key={`e${i}`} className="text-red-500">⚠️ {e}</li>)}
          {q.warnings.map((w, i) => <li key={`w${i}`} className="text-amber-500">・{w}</li>)}
        </ul>
      </details>
    </div>
  );
}

/** 回答の読み上げ（TTS 返答）の切り替え。入力欄の右下に置く（2026-11-19 利用者「＋の中ではなく右下に。会話モードでも変えたい」） */
export function SpeakerToggle({ on, speaking, onToggle }: { on: boolean; speaking: boolean; onToggle: () => void }) {
  const label = on ? "回答の読み上げをやめる" : "回答を読み上げる";
  return (
    <button type="button" onClick={onToggle} aria-pressed={on} aria-label={label} title={label} data-speaker-toggle={on ? "on" : "off"} data-speaking={speaking ? "true" : "false"}
      className={`grid h-11 w-11 shrink-0 place-items-center rounded-full text-lg transition focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500/40 ${on
        ? "bg-accent-50 text-accent-700 dark:bg-accent-600/15 dark:text-accent-300" : "text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800"}`}>
      {/* マイク（IconMic）と同じ線のアイコンにする（以前は絵文字で系統が違った。2026-11-20 利用者の依頼） */}
      {/* 読み上げ中も点滅させない（2026-11-20 利用者「チカチカしてやめてほしい」）。読み上げ中かは data-speaking で分かる */}
      {on ? <IconSpeaker /> : <IconSpeakerOff />}
    </button>
  );
}
