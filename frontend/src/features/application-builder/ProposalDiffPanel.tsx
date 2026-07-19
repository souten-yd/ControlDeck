import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { applicationBuilderApi, type ApplicationPatchOperation, type ApplicationPatchPreview, type ApplicationProject } from "../../api/applicationBuilder";
import { BottomSheet } from "../../components/ui";
import { useToasts } from "../../stores";

export function ProposalDiffPanel({ project, onClose, initialOperations = [] }: { project: ApplicationProject; onClose: () => void; initialOperations?: ApplicationPatchOperation[] }) {
  const qc = useQueryClient();
  const show = useToasts((state) => state.show);
  const [source, setSource] = useState(() => initialOperations.length ? JSON.stringify(initialOperations, null, 2) : "");
  const [operations, setOperations] = useState<ApplicationPatchOperation[]>(initialOperations);
  const [selected, setSelected] = useState<Set<number>>(() => new Set(initialOperations.map((_item, index) => index)));
  const [parseError, setParseError] = useState("");
  const [preview, setPreview] = useState<ApplicationPatchPreview | null>(null);
  const selectedOperations = useMemo(() => operations.filter((_item, index) => selected.has(index)), [operations, selected]);
  const signature = JSON.stringify(selectedOperations);
  const [previewSignature, setPreviewSignature] = useState("");

  const parseProposal = () => {
    try {
      const value = JSON.parse(source);
      if (!Array.isArray(value) || value.length === 0 || value.length > 200) throw new Error("1〜200件のPatch配列を入力してください");
      const parsed = value.map((item) => validateOperation(item));
      setOperations(parsed); setSelected(new Set(parsed.map((_item, index) => index))); setPreview(null); setPreviewSignature(""); setParseError("");
    } catch (reason) {
      setParseError(reason instanceof Error ? reason.message : "JSON Patchを読み込めません");
    }
  };
  const previewMutation = useMutation({
    mutationFn: () => applicationBuilderApi.previewPatches(project.spec, selectedOperations),
    onSuccess: (result) => { setPreview(result); setPreviewSignature(signature); },
    onError: (reason) => setParseError(reason instanceof Error ? reason.message : "Previewに失敗しました"),
  });
  const applyMutation = useMutation({
    mutationFn: () => applicationBuilderApi.applyPatches(project.id, preview!.baseChecksum, selectedOperations),
    onSuccess: async () => { show("選択した変更を適用しました"); await qc.invalidateQueries({ queryKey: ["application-project", project.id] }); onClose(); },
    onError: (reason) => show(reason instanceof Error ? reason.message : "変更を適用できませんでした", "error"),
  });
  const toggle = (index: number) => {
    setSelected((current) => { const next = new Set(current); if (next.has(index)) next.delete(index); else next.add(index); return next; });
    setPreview(null); setPreviewSignature("");
  };
  const ready = Boolean(preview?.valid && previewSignature === signature && selectedOperations.length);

  return <BottomSheet title="Review Spec Patch" onClose={onClose} wide stable>
    <div className="space-y-4 pb-4">
      <div className="rounded-xl bg-blue-50 p-3 text-xs leading-relaxed text-blue-700 dark:bg-blue-950/30 dark:text-blue-300">構造化されたApplication Spec Patchだけを確認します。自由codeは実行せず、選択した変更を再検証してから原子的に保存します。</div>
      <label className="block text-xs font-medium">JSON Patch proposal<textarea aria-label="JSON Patch proposal" value={source} onChange={(event) => setSource(event.target.value)} rows={6} placeholder={'[{"op":"replace","path":"/pages/0/title","value":"Dashboard"}]'} className="mt-2 w-full rounded-xl border border-zinc-300 bg-transparent p-3 font-mono text-xs outline-none focus:border-accent-500 dark:border-zinc-700" /></label>
      <button onClick={parseProposal} disabled={!source.trim()} className="min-h-11 w-full rounded-xl border border-zinc-300 text-xs font-semibold disabled:opacity-40 dark:border-zinc-700">Load proposal</button>
      {parseError && <p role="alert" className="rounded-xl bg-red-50 p-3 text-xs text-red-600 dark:bg-red-950/30 dark:text-red-300">{parseError}</p>}
      {operations.length > 0 && <section aria-label="Patch operations"><div className="mb-2 flex items-center justify-between"><h3 className="text-xs font-semibold">Changes</h3><span className="num text-[10px] text-zinc-400">{selected.size} / {operations.length}</span></div><div className="space-y-2">{operations.map((operation, index) => <label key={`${operation.op}-${operation.path}-${index}`} className="flex min-h-12 items-start gap-3 rounded-xl border border-zinc-200 p-3 dark:border-zinc-700"><input type="checkbox" checked={selected.has(index)} onChange={() => toggle(index)} className="mt-1 h-5 w-5 shrink-0" /><span className="min-w-0 flex-1"><span className="flex gap-2"><strong className="rounded bg-zinc-100 px-1.5 py-0.5 text-[9px] uppercase dark:bg-zinc-800">{operation.op}</strong><code className="min-w-0 break-all text-[10px]">{operation.path}</code></span>{operation.from && <code className="mt-1 block break-all text-[9px] text-zinc-400">from: {operation.from}</code>}{"value" in operation && <code className="mt-1 line-clamp-2 block break-all text-[9px] text-zinc-400">{compact(operation.value)}</code>}</span></label>)}</div></section>}
      {selectedOperations.length > 0 && <button onClick={() => previewMutation.mutate()} disabled={previewMutation.isPending} className="min-h-11 w-full rounded-xl bg-zinc-900 text-xs font-semibold text-white dark:bg-white dark:text-zinc-900">{previewMutation.isPending ? "Validating…" : "Preview selected"}</button>}
      {preview && <section aria-label="Patch preview" className="space-y-3"><div className="grid grid-cols-2 gap-2"><SpecSummary title="Before" spec={project.spec} /><SpecSummary title="After" spec={preview.patchedSpec} /></div>{preview.diagnostics.length > 0 && <div className="space-y-1">{preview.diagnostics.map((item, index) => <div key={`${item.code}-${index}`} className={`rounded-lg p-2 text-[10px] ${item.severity === "error" ? "bg-red-50 text-red-600 dark:bg-red-950/30 dark:text-red-300" : "bg-amber-50 text-amber-700 dark:bg-amber-950/30"}`}><strong>{item.code}</strong> · {item.message}<code className="mt-1 block break-all opacity-70">{item.path}</code></div>)}</div>}<p className="break-all font-mono text-[9px] text-zinc-400">base {preview.baseChecksum.slice(0, 12)}… → {preview.resultChecksum.slice(0, 12)}…</p></section>}
      <button onClick={() => applyMutation.mutate()} disabled={!ready || applyMutation.isPending} className="min-h-12 w-full rounded-xl bg-accent-600 text-sm font-semibold text-white disabled:opacity-40">{applyMutation.isPending ? "Applying…" : "Apply selected changes"}</button>
    </div>
  </BottomSheet>;
}

function validateOperation(value: unknown): ApplicationPatchOperation {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("各Patchはobjectにしてください");
  const item = value as Record<string, unknown>;
  if (!["add", "remove", "replace", "move"].includes(String(item.op))) throw new Error(`未対応のoperationです: ${String(item.op)}`);
  if (typeof item.path !== "string" || !item.path.startsWith("/")) throw new Error("pathは絶対JSON Pointerにしてください");
  if (item.op === "move" && typeof item.from !== "string") throw new Error("moveにはfromが必要です");
  return { op: item.op as ApplicationPatchOperation["op"], path: item.path, ...(typeof item.from === "string" ? { from: item.from } : {}), ...("value" in item ? { value: item.value } : {}) };
}

function compact(value: unknown) { const text = JSON.stringify(value) ?? String(value); return text.length > 180 ? `${text.slice(0, 177)}…` : text; }
function SpecSummary({ title, spec }: { title: string; spec: Record<string, unknown> }) { const pages = Array.isArray(spec.pages) ? spec.pages : []; const count = countComponents(pages); return <div className="rounded-xl bg-zinc-50 p-3 dark:bg-zinc-950"><span className="text-[10px] text-zinc-400">{title}</span><strong className="num mt-1 block text-sm">{pages.length} pages</strong><span className="num text-[10px] text-zinc-500">{count} components</span></div>; }
function countComponents(value: unknown): number { if (Array.isArray(value)) return value.reduce<number>((sum, item) => sum + countComponents(item), 0); if (!value || typeof value !== "object") return 0; const item = value as Record<string, unknown>; const own = typeof item.id === "string" && typeof item.type === "string" ? 1 : 0; return own + Object.values(item).reduce<number>((sum, child) => sum + countComponents(child), 0); }
