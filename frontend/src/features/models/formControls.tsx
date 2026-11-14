/** Model settings controls shared by llama.cpp, Maya-S and Strata. */
import { useState } from "react";

export const CTX_PRESETS = [2048, 4096, 8192, 16384, 32768, 65536, 131072, 262144].map((v) => ({ v, label: v.toLocaleString() }));

export /** 選択肢（プリセット）から選ぶ + 末尾の「カスタム」で手動入力するハイブリッド入力。 */
function PresetOrCustom({
  value,
  presets,
  placeholder,
  numeric = true,
  onChange,
  ariaLabel,
  disabled = false,
  defaultLabel,
}: {
  ariaLabel?: string;
  /** Shown as a first "既定（…）" choice only when the field has a real server default. */
  defaultLabel?: string;
  disabled?: boolean;
  value: number | string | undefined;
  presets: { v: number | string; label: string }[];
  placeholder?: string;
  numeric?: boolean;
  onChange: (v: number | string | undefined) => void;
}) {
  const isPreset = value !== undefined && value !== "" && presets.some((p) => p.v === value);
  const [custom, setCustom] = useState(!isPreset && value !== undefined && value !== "");
  const sel = "rounded-xl border border-zinc-300 bg-white px-2.5 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900";
  return (
    <div className="flex gap-1.5">
      <select
        aria-label={ariaLabel}
        disabled={disabled}
        value={custom ? "__custom__" : value === undefined || value === "" ? "" : String(value)}
        onChange={(e) => {
          if (e.target.value === "__custom__") { setCustom(true); return; }
          setCustom(false);
          if (e.target.value === "") onChange(undefined);
          else onChange(numeric ? Number(e.target.value) : e.target.value);
        }}
        className={`${sel} ${custom ? "w-28 shrink-0" : "min-w-0 flex-1"}`}
      >
        {(defaultLabel || value === undefined || value === "") && <option value="">{defaultLabel ?? "未設定"}</option>}
        {presets.map((p) => <option key={String(p.v)} value={String(p.v)}>{p.label}</option>)}
        <option value="__custom__">カスタム入力…</option>
      </select>
      {custom && (
        <input
          type={numeric ? "number" : "text"}
          value={value === undefined ? "" : String(value)}
          onChange={(e) => onChange(e.target.value === "" ? undefined : numeric ? Number(e.target.value) : e.target.value)}
          placeholder={placeholder}
          aria-label={ariaLabel ? `${ariaLabel}（カスタム）` : undefined}
          disabled={disabled}
          autoFocus
          className={`${sel} min-w-0 flex-1 font-mono text-xs`}
        />
      )}
    </div>
  );
}

export function Toggle({ label, hint, value, disabled = false, onChange }: { label: string; hint?: string; value: boolean; disabled?: boolean; onChange: (value: boolean) => void }) {
  return <label className="flex min-h-11 items-center justify-between gap-3 rounded-xl border border-zinc-200 px-3 py-2 dark:border-zinc-700">
    <span className="text-xs">{label}{hint && <span className="block text-[11px] font-normal text-zinc-500">{hint}</span>}</span>
    <input type="checkbox" checked={value} disabled={disabled} onChange={(e) => onChange(e.target.checked)} className="h-5 w-5 shrink-0 disabled:opacity-40" />
  </label>;
}

/** モデル個別設定の1グループ。全ランタイムで同じ順・同じ見た目にする:
 * 基本 → 起動と停止 → 思考 → 性能 → 詳細（畳む）。 */
export function SettingsGroup({ title, description, children, collapsible = false, defaultOpen = true }: {
  title: string; description?: string; children: React.ReactNode; collapsible?: boolean; defaultOpen?: boolean;
}) {
  if (collapsible) {
    return (
      <details open={defaultOpen} className="group rounded-xl border border-zinc-200 dark:border-zinc-700">
        <summary className="flex min-h-11 cursor-pointer list-none items-center gap-2 px-3 text-sm font-semibold">
          <span className="flex-1">{title}{description && <span className="block text-[11px] font-normal text-zinc-500">{description}</span>}</span>
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" className="text-zinc-400 transition-transform group-open:rotate-90" aria-hidden><path d="m9 6 6 6-6 6" /></svg>
        </summary>
        <div className="space-y-3 border-t border-zinc-100 px-3 py-3 dark:border-zinc-800">{children}</div>
      </details>
    );
  }
  return (
    <section className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-700">
      <h3 className="text-sm font-semibold">{title}</h3>
      {description && <p className="mt-0.5 text-[11px] text-zinc-500">{description}</p>}
      <div className="mt-3 space-y-3">{children}</div>
    </section>
  );
}

/** シート下端に固定する操作列。スクロールしても保存ボタンに届く。 */
export function SheetActions({ children, note }: { children: React.ReactNode; note?: React.ReactNode }) {
  return (
    // No negative bottom margin: a sticky box stays inside its parent, so -mb-6 made it ride
    // 24px up over the last setting at the end of the scroll. The sheet drops its own bottom
    // padding when it has this bar (see BottomSheet).
    <div className="sheet-actions sticky bottom-0 z-10 -mx-5 mt-4 border-t border-zinc-200 bg-white/95 px-5 pb-4 pt-3 backdrop-blur dark:border-zinc-800 dark:bg-zinc-900/95">
      {note && <p className="mb-2 text-[11px] text-amber-700 dark:text-amber-300">{note}</p>}
      <div className="flex flex-wrap gap-2">{children}</div>
    </div>
  );
}

/** 個別設定の先頭に出す状態の要約。どのランタイムでも同じ並び。 */
export function ModelStatusSummary({ running, rows, lockedNote, action }: {
  running: boolean; rows: [string, React.ReactNode][];
  /** Why some fields are locked; said once here, next to the way out. */
  lockedNote?: string; action?: React.ReactNode;
}) {
  return (
    <div className="rounded-xl bg-zinc-50 p-3 text-xs dark:bg-zinc-800/60">
      <p className="mb-1.5 flex items-center gap-2 text-sm font-medium">
        <span className={`h-2.5 w-2.5 rounded-full ${running ? "bg-emerald-500" : "bg-zinc-300 dark:bg-zinc-600"}`} />
        {running ? "稼働中" : "停止中"}
      </p>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
        {rows.map(([label, value]) => <div key={label} className="contents"><dt className="text-zinc-500">{label}</dt><dd className="min-w-0 break-all">{value}</dd></div>)}
      </dl>
      {(lockedNote || action) && <div className="mt-2 flex flex-wrap items-center gap-2">
        {lockedNote && <p className="min-w-0 flex-1 text-[11px] text-amber-700 dark:text-amber-300">{lockedNote}</p>}
        {action}
      </div>}
    </div>
  );
}

/** The file name of a model path; the full path stays in the tooltip. */
export function FileName({ path }: { path: string }) {
  return <span className="font-mono" title={path}>{path.split("/").pop() || path}</span>;
}

/** Monospace text with a copy button (endpoints people paste elsewhere). */
export function CopyText({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return <span className="inline-flex flex-wrap items-center gap-2">
    <span className="font-mono">{text}</span>
    <button type="button" onClick={() => { void navigator.clipboard?.writeText(text).then(() => { setCopied(true); window.setTimeout(() => setCopied(false), 1500); }); }}
      className="rounded-md border border-zinc-300 px-2 py-0.5 text-[11px] text-zinc-600 dark:border-zinc-600 dark:text-zinc-300">{copied ? "コピーしました" : "コピー"}</button>
  </span>;
}

/** The on-demand start every runtime has, shown the same way everywhere. */
export function LifecycleAlwaysOn() {
  return <div className="flex min-h-11 items-center justify-between gap-3 rounded-xl border border-dashed border-zinc-200 px-3 py-2 dark:border-zinc-700">
    <span className="text-xs">使われたときに自動で起動<span className="block text-[11px] text-zinc-500">チャットやゲートウェイから呼ばれたら起動します</span></span>
    <span className="shrink-0 text-[11px] text-zinc-500">常に有効</span>
  </div>;
}

export const primaryButton = "min-h-11 flex-1 rounded-xl bg-accent-600 px-4 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-40";
export const secondaryButton = "min-h-11 flex-1 rounded-xl border border-zinc-300 px-4 text-sm font-medium hover:bg-zinc-50 disabled:opacity-40 dark:border-zinc-700 dark:hover:bg-zinc-800";
