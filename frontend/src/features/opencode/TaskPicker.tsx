/** 作業の種類 — セッション開始で選ぶのはこれだけ。
 *
 * 選ぶと、server が最初の指示の前に手順を付ける（task）。道具を個別に組み合わせる画面は作らない。 */
import { Link } from "react-router-dom";
import type { Workflow } from "./WorkflowExtensions";

export type SessionTask = "" | "ui_verify" | "research" | "review";

const TASKS: { id: SessionTask; label: string; hint: string }[] = [
  { id: "", label: "ふつう", hint: "いつもどおり、指示したことを進めます。" },
  { id: "ui_verify", label: "UI 検証", hint: "実際のブラウザで画面を開き、幅 320〜1280 とダークで撮って問題を報告します。" },
  { id: "research", label: "精密調査", hint: "一次資料を集め、出典と反証を付けて報告します。" },
  { id: "review", label: "レビュー", hint: "ファイルは変えずに差分を読み、重大度順に指摘します。" },
];

export type UiVerifyBlocker = "not_installed" | "no_browser" | "disabled";

/** UI 検証を始められない理由。設定がまだ読めていない・読めないときは止めない。 */
export function uiVerifyBlocker(workflow: Workflow | undefined): UiVerifyBlocker | null {
  if (!workflow) return null;
  if (workflow.installed && !workflow.installed.ui_verify) return "not_installed";
  if (workflow.browser === false) return "no_browser";
  if (workflow.ui_verify === false) return "disabled";
  return null;
}

/** 確かめる URL を最初の指示の先頭に入れる。 */
export function withTarget(url: string, prompt: string): string {
  const target = url.trim();
  if (!target) return prompt;
  return prompt.trim() ? `確かめる URL: ${target}\n${prompt}` : `確かめる URL: ${target}`;
}

export function TaskPicker({ task, onTask, targetUrl, onTargetUrl, blocker, onOpenSettings, inputClass }: {
  task: SessionTask; onTask: (task: SessionTask) => void;
  targetUrl: string; onTargetUrl: (url: string) => void;
  blocker: UiVerifyBlocker | null; onOpenSettings: () => void; inputClass: string;
}) {
  const selected = TASKS.find((item) => item.id === task) ?? TASKS[0];
  const link = "text-accent-700 underline dark:text-accent-400";
  return (
    <div className="space-y-1.5 text-xs text-zinc-500">
      <p id="opencode-task-label">作業の種類</p>
      <div role="radiogroup" aria-labelledby="opencode-task-label" className="flex gap-1 rounded-xl bg-zinc-100 p-1 dark:bg-zinc-800"
        onKeyDown={(event) => {
          if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
          event.preventDefault();
          const index = TASKS.findIndex((item) => item.id === task);
          const next = TASKS[(index + (event.key === "ArrowRight" ? 1 : -1) + TASKS.length) % TASKS.length];
          onTask(next.id);
          event.currentTarget.querySelector<HTMLElement>(`[data-task="${next.id}"]`)?.focus();
        }}>
        {TASKS.map((item) => (
          <button key={item.id} type="button" role="radio" aria-checked={task === item.id} data-task={item.id}
            tabIndex={task === item.id ? 0 : -1} onClick={() => onTask(item.id)}
            className={`min-h-11 min-w-0 flex-1 whitespace-nowrap rounded-lg px-0.5 text-xs font-medium ${task === item.id ? "bg-white text-zinc-900 shadow-sm dark:bg-zinc-900 dark:text-zinc-100" : "text-zinc-500"}`}>
            {item.label}
          </button>
        ))}
      </div>
      <p aria-live="polite">{selected.hint}{task !== "" && " 結果は下の「結果」に出ます。"}</p>
      {task === "ui_verify" && blocker && (
        <p role="status" className="rounded-xl bg-amber-50 px-3 py-2 text-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
          {blocker === "not_installed" && <>UI 検証の道具（Playwright MCP）がまだ入っていません。<Link to="/settings/features#feature-playwright-mcp" className={link}>設定 → オプション機能で導入</Link></>}
          {blocker === "no_browser" && <>このPCに Google Chrome が見つかりません。Chrome を入れてから始めてください。<Link to="/settings/features#feature-playwright-mcp" className={link}>設定 → オプション機能</Link></>}
          {blocker === "disabled" && <>UI 検証がオフになっています。
            <button type="button" onClick={onOpenSettings} className={`min-h-11 ${link}`}>OpenCode の設定でオンにする</button></>}
        </p>
      )}
      {task === "ui_verify" && !blocker && (
        <label className="block">確かめる URL（任意）
          <input type="url" inputMode="url" autoCapitalize="off" autoCorrect="off" spellCheck={false}
            value={targetUrl} onChange={(event) => onTargetUrl(event.target.value)}
            placeholder="http://localhost:5173/" className={`${inputClass} mt-1 min-h-11 font-mono`} />
          <span className="mt-0.5 block text-[11px]">空なら AI がプロジェクトから決めます。届くのは localhost だけです（設定で変えられます）。</span>
        </label>
      )}
    </div>
  );
}
