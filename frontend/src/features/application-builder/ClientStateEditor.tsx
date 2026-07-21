import { useEffect, useState } from "react";
import type { ApplicationClientState, ApplicationClientStateType } from "../../api/applicationBuilder";

const TYPES: ApplicationClientStateType[] = ["string", "integer", "number", "boolean", "object", "array"];

function defaultValue(type: ApplicationClientStateType): unknown {
  if (type === "string") return "";
  if (type === "integer" || type === "number") return 0;
  if (type === "boolean") return false;
  if (type === "array") return [];
  return {};
}

function stateErrors(states: ApplicationClientState[]): string[] {
  const errors: string[] = [];
  const ids = new Set<string>();
  for (const [index, state] of states.entries()) {
    if (!/^[A-Za-z][A-Za-z0-9_-]{0,127}$/.test(state.id)) errors.push(`State ${index + 1}: IDは英字始まりのidentifierにしてください`);
    else if (ids.has(state.id)) errors.push(`State ID '${state.id}' が重複しています`);
    ids.add(state.id);
    if (state.initialValue === null && state.nullable) continue;
    const valid = state.type === "string" ? typeof state.initialValue === "string"
      : state.type === "integer" ? typeof state.initialValue === "number" && Number.isInteger(state.initialValue)
        : state.type === "number" ? typeof state.initialValue === "number" && Number.isFinite(state.initialValue)
          : state.type === "boolean" ? typeof state.initialValue === "boolean"
            : state.type === "array" ? Array.isArray(state.initialValue)
              : Boolean(state.initialValue) && typeof state.initialValue === "object" && !Array.isArray(state.initialValue);
    if (!valid) errors.push(`State '${state.id || index + 1}' の初期値が${state.type}と一致しません`);
  }
  return errors;
}

export function ClientStateEditor({ states, dirty, saving, onChange, onSave, showSave = true }: { states: ApplicationClientState[]; dirty: boolean; saving: boolean; onChange: (states: ApplicationClientState[]) => void; onSave: () => void; showSave?: boolean }) {
  const errors = stateErrors(states);
  const add = () => {
    const used = new Set(states.map((state) => state.id));
    let index = states.length + 1;
    while (used.has(`state${index}`)) index += 1;
    onChange([...states, { id: `state${index}`, type: "string", initialValue: "", nullable: false }]);
  };
  const update = (index: number, patch: Partial<ApplicationClientState>) => onChange(states.map((state, current) => current === index ? { ...state, ...patch } : state));
  return <section aria-label="Client State Editor" className="mt-4 overflow-hidden rounded-2xl border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
    <div className="flex min-h-14 flex-wrap items-center gap-2 border-b border-zinc-200 px-3 py-2 dark:border-zinc-800"><div className="mr-auto"><h2 className="text-sm font-semibold">Client State</h2><p className="text-[10px] text-zinc-400">生成browser内だけのtyped state。Secretや永続値は保存しません。</p></div><button type="button" onClick={add} disabled={states.length >= 100} className="min-h-11 rounded-lg border border-zinc-300 px-3 text-xs disabled:opacity-30 dark:border-zinc-700">Add state</button>{showSave && <button type="button" onClick={onSave} disabled={!dirty || saving || Boolean(errors.length)} className="min-h-11 rounded-lg bg-accent-600 px-4 text-xs font-semibold text-white disabled:opacity-40">{saving ? "Saving…" : "Save State"}</button>}</div>
    {states.length === 0 ? <p className="p-4 text-xs text-zinc-400">Stateは未定義です。state binding／state-setを生成する前に型と初期値を宣言します。</p> : <div className="grid gap-3 p-3 md:grid-cols-2">{states.map((state, index) => <article key={`${state.id}-${index}`} className="min-w-0 rounded-xl border border-zinc-200 p-3 dark:border-zinc-700"><div className="grid gap-2 sm:grid-cols-2"><label className="text-[10px] text-zinc-400">State ID<input aria-label={`State ${index + 1} ID`} value={state.id} onChange={(event) => update(index, { id: event.target.value })} className="mt-1 min-h-11 w-full rounded-lg border border-zinc-300 bg-transparent px-2 text-xs dark:border-zinc-700" /></label><label className="text-[10px] text-zinc-400">Type<select aria-label={`State ${index + 1} type`} value={state.type} onChange={(event) => { const type = event.target.value as ApplicationClientStateType; update(index, { type, initialValue: defaultValue(type), nullable: false }); }} className="mt-1 min-h-11 w-full rounded-lg border border-zinc-300 bg-transparent px-2 text-xs dark:border-zinc-700">{TYPES.map((type) => <option key={type} value={type}>{type}</option>)}</select></label></div><StateInitialValueField state={state} index={index} onChange={(initialValue) => update(index, { initialValue })} /><label className="mt-2 flex min-h-11 items-center gap-2 rounded-lg bg-zinc-50 px-2 text-xs dark:bg-zinc-800"><input aria-label={`State ${index + 1} nullable`} type="checkbox" checked={Boolean(state.nullable)} onChange={(event) => update(index, { nullable: event.target.checked, ...(event.target.checked ? {} : state.initialValue === null ? { initialValue: defaultValue(state.type) } : {}) })} className="h-4 w-4" />Nullable</label><button type="button" onClick={() => onChange(states.filter((_state, current) => current !== index))} className="mt-2 min-h-11 w-full rounded-lg text-xs text-red-600">Remove state</button></article>)}</div>}
    {errors.length > 0 && <p role="alert" className="mx-3 mb-3 rounded-lg bg-red-50 p-2 text-xs text-red-600 dark:bg-red-950/30 dark:text-red-300">{errors[0]}</p>}
  </section>;
}

function StateInitialValueField({ state, index, onChange }: { state: ApplicationClientState; index: number; onChange: (value: unknown) => void }) {
  const jsonType = state.type === "object" || state.type === "array";
  const [draft, setDraft] = useState(jsonType ? JSON.stringify(state.initialValue, null, 2) : String(state.initialValue ?? ""));
  const [error, setError] = useState("");
  useEffect(() => { setDraft(jsonType ? JSON.stringify(state.initialValue, null, 2) : String(state.initialValue ?? "")); setError(""); }, [jsonType, state.initialValue]);
  if (state.nullable && state.initialValue === null) return <div className="mt-2"><button type="button" onClick={() => onChange(defaultValue(state.type))} className="min-h-11 w-full rounded-lg border border-zinc-300 text-xs dark:border-zinc-700">Set non-null initial value</button></div>;
  if (state.type === "boolean") return <label className="mt-2 flex min-h-11 items-center gap-2 text-xs"><input aria-label={`State ${index + 1} initial value`} type="checkbox" checked={Boolean(state.initialValue)} onChange={(event) => onChange(event.target.checked)} className="h-4 w-4" />Initial value</label>;
  const commit = () => {
    if (jsonType) {
      try {
        const value = JSON.parse(draft);
        if ((state.type === "array") !== Array.isArray(value) || (state.type === "object" && (!value || typeof value !== "object" || Array.isArray(value)))) throw new Error();
        onChange(value); setError("");
      } catch { setError(`${state.type} JSONを入力してください`); }
      return;
    }
    if (state.type === "integer" || state.type === "number") {
      const value = Number(draft);
      if (!Number.isFinite(value) || (state.type === "integer" && !Number.isInteger(value))) { setError(`${state.type}を入力してください`); return; }
      onChange(value); setError(""); return;
    }
    onChange(draft); setError("");
  };
  return <label className="mt-2 block text-[10px] text-zinc-400">Initial value{jsonType ? <textarea aria-label={`State ${index + 1} initial value`} value={draft} onChange={(event) => setDraft(event.target.value)} onBlur={commit} rows={4} className="mt-1 w-full rounded-lg border border-zinc-300 bg-transparent p-2 font-mono text-xs dark:border-zinc-700" /> : <input aria-label={`State ${index + 1} initial value`} type={state.type === "string" ? "text" : "number"} value={draft} onChange={(event) => setDraft(event.target.value)} onBlur={commit} className="mt-1 min-h-11 w-full rounded-lg border border-zinc-300 bg-transparent px-2 text-xs dark:border-zinc-700" />}{error && <span role="alert" className="mt-1 block text-red-600">{error}</span>}{state.nullable && <button type="button" onClick={() => onChange(null)} className="mt-1 min-h-10 rounded-lg border border-zinc-300 px-3 text-xs dark:border-zinc-700">Use null</button>}</label>;
}
