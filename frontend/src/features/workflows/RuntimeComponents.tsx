import type { TriggerInputDef } from "./nodeTypes";

export interface RuntimeOutput {
  type: string;
  value: unknown;
  title?: string;
  description?: string;
  filename?: string;
  mime_type?: string;
  downloadable?: boolean;
}

export function initialRuntimeValues(inputs: TriggerInputDef[]): Record<string, unknown> {
  return Object.fromEntries(inputs.filter((input) => input.key).map((input) => [
    input.key, input.default ?? (input.type === "boolean" ? false : input.type === "multi_select" || input.type === "file_list" ? [] : ""),
  ]));
}

export function RuntimeField({ input, value, onChange }: { input: TriggerInputDef; value: unknown; onChange: (value: unknown) => void }) {
  const id = `runtime-input-${input.key}`;
  const cls = "min-h-11 w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm outline-none focus:border-accent-500 focus:ring-2 focus:ring-accent-500/20 dark:border-zinc-700 dark:bg-zinc-950";
  const options = (input.options || "").split(/[,\n]/).map((item) => item.trim()).filter(Boolean);
  const parseStructured = (text: string) => { try { return text.trim() ? JSON.parse(text) : {}; } catch { return text; } };
  return (
    <label htmlFor={id} className="block">
      <span className="mb-1 block text-xs font-medium">{input.label || input.key}{input.required ? " *" : ""}</span>
      {input.description && <span className="mb-1 block text-[11px] leading-relaxed text-zinc-500 dark:text-zinc-400">{input.description}</span>}
      {input.type === "paragraph" ? <textarea id={id} rows={4} value={String(value ?? "")} maxLength={input.maxLength} placeholder={input.placeholder} onChange={(event) => onChange(event.target.value)} className={cls} />
        : input.type === "number" ? <input id={id} type="number" value={String(value ?? "")} placeholder={input.placeholder} onChange={(event) => onChange(event.target.value === "" ? "" : Number(event.target.value))} className={cls} />
          : input.type === "boolean" ? <span className="flex min-h-11 items-center"><input id={id} type="checkbox" checked={Boolean(value)} onChange={(event) => onChange(event.target.checked)} className="h-5 w-5 accent-accent-600" /></span>
            : input.type === "select" ? <select id={id} value={String(value ?? "")} onChange={(event) => onChange(event.target.value)} className={cls}><option value="">選択してください</option>{options.map((option) => <option key={option}>{option}</option>)}</select>
              : input.type === "multi_select" ? <select id={id} multiple value={Array.isArray(value) ? value.map(String) : []} onChange={(event) => onChange(Array.from(event.target.selectedOptions, (option) => option.value))} className={`${cls} min-h-28`}>{options.map((option) => <option key={option}>{option}</option>)}</select>
                : input.type === "json" || input.type === "key_value" ? <textarea id={id} rows={5} value={typeof value === "string" ? value : JSON.stringify(value ?? {}, null, 2)} placeholder={input.placeholder || "{}"} onChange={(event) => onChange(parseStructured(event.target.value))} className={`${cls} font-mono text-xs`} />
                  : input.type === "file" || input.type === "file_list" ? <input id={id} type="file" multiple={input.type === "file_list"} onChange={(event) => onChange(input.type === "file_list" ? Array.from(event.target.files ?? [], (file) => file.name) : event.target.files?.[0]?.name ?? "")} className={`${cls} file:mr-2 file:rounded-lg file:border-0 file:bg-zinc-100 file:px-2 file:py-1 dark:file:bg-zinc-800`} />
                    : <input id={id} type={input.type === "date" ? "date" : input.type === "datetime" ? "datetime-local" : input.type === "secret_reference" ? "password" : "text"} value={String(value ?? "")} maxLength={input.maxLength} placeholder={input.placeholder} onChange={(event) => onChange(event.target.value)} className={cls} />}
      {input.sample !== undefined && <span className="mt-1 block text-[10px] text-zinc-400">例: {typeof input.sample === "string" ? input.sample : JSON.stringify(input.sample)}</span>}
    </label>
  );
}

function stringify(value: unknown): string {
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

export function RuntimeOutputView({ output }: { output: RuntimeOutput }) {
  const value = output.value;
  if ((output.type === "image" || output.type === "file") && typeof value === "string" && /^https?:|^data:|^\//.test(value)) {
    if (output.type === "image") return <img src={value} alt={output.title || "出力画像"} className="mt-2 max-h-80 max-w-full rounded-xl object-contain" />;
    return <a href={value} className="mt-2 inline-flex min-h-11 items-center text-xs font-medium text-accent-600 underline">{output.filename || value}</a>;
  }
  if (output.type === "link" && typeof value === "string") return <a href={value} target="_blank" rel="noreferrer" className="mt-2 block min-h-11 break-all py-2 text-xs text-accent-600 underline">{value}</a>;
  if (output.type === "table" && Array.isArray(value) && value.every((row) => row && typeof row === "object" && !Array.isArray(row))) {
    const columns = Array.from(new Set(value.flatMap((row) => Object.keys(row as Record<string, unknown>))));
    return <div className="mt-2 overflow-auto rounded-lg border border-zinc-200 dark:border-zinc-800"><table className="min-w-full text-left text-[11px]"><thead className="bg-zinc-50 dark:bg-zinc-900"><tr>{columns.map((column) => <th key={column} className="border-b p-2">{column}</th>)}</tr></thead><tbody>{value.map((row, index) => <tr key={index}>{columns.map((column) => <td key={column} className="border-b border-zinc-100 p-2 dark:border-zinc-800">{stringify((row as Record<string, unknown>)[column])}</td>)}</tr>)}</tbody></table></div>;
  }
  if ((output.type === "audio" || output.type === "video") && typeof value === "string") return output.type === "audio" ? <audio className="mt-2 w-full" controls src={value} /> : <video className="mt-2 max-h-80 w-full" controls src={value} />;
  return <pre className={`mt-2 max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-xl bg-zinc-50 p-3 text-xs leading-relaxed dark:bg-zinc-950 ${output.type === "code" || output.type.startsWith("json") ? "font-mono" : ""}`}>{stringify(value)}</pre>;
}
