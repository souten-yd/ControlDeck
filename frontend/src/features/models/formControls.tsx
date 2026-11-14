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
}: {
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
        value={custom ? "__custom__" : value === undefined || value === "" ? "" : String(value)}
        onChange={(e) => {
          if (e.target.value === "__custom__") { setCustom(true); return; }
          setCustom(false);
          if (e.target.value === "") onChange(undefined);
          else onChange(numeric ? Number(e.target.value) : e.target.value);
        }}
        className={`${sel} ${custom ? "w-28 shrink-0" : "min-w-0 flex-1"}`}
      >
        <option value="">既定</option>
        {presets.map((p) => <option key={String(p.v)} value={String(p.v)}>{p.label}</option>)}
        <option value="__custom__">カスタム入力…</option>
      </select>
      {custom && (
        <input
          type={numeric ? "number" : "text"}
          value={value === undefined ? "" : String(value)}
          onChange={(e) => onChange(e.target.value === "" ? undefined : numeric ? Number(e.target.value) : e.target.value)}
          placeholder={placeholder}
          autoFocus
          className={`${sel} min-w-0 flex-1 font-mono text-xs`}
        />
      )}
    </div>
  );
}

export function Toggle({ label, hint, value, disabled = false, onChange }: { label: string; hint?: string; value: boolean; disabled?: boolean; onChange: (value: boolean) => void }) {
  return <label className="flex items-center justify-between gap-3 rounded-xl border border-zinc-200 px-3 py-2 dark:border-zinc-700">
    <span className="text-xs">{label}{hint && <span className="block text-[10px] font-normal text-zinc-400">{hint}</span>}</span>
    <input type="checkbox" checked={value} disabled={disabled} onChange={(e) => onChange(e.target.checked)} className="h-4 w-4 shrink-0 disabled:opacity-40" />
  </label>;
}
