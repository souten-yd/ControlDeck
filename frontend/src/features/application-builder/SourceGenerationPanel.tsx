import { useEffect, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { applicationBuilderApi, type ApplicationProject, type CapabilityCatalog } from "../../api/applicationBuilder";
import { useToasts } from "../../stores";
import { BuildPanel } from "./BuildPanel";

type Target = { id: string; framework: string; platforms: string[] };

export function SourceGenerationPanel({ project, capabilities }: { project: ApplicationProject; capabilities?: CapabilityCatalog }) {
  const show = useToasts((state) => state.show);
  const targets = (Array.isArray(project.spec.targets) ? project.spec.targets : []).filter((item): item is Target => {
    if (!item || typeof item !== "object") return false;
    const target = item as Record<string, unknown>;
    return ["csharp-console", "aspnet-blazor"].includes(String(target.framework)) && typeof target.id === "string" && Array.isArray(target.platforms);
  });
  const [selected, setSelected] = useState(targets[0]?.id ?? "");
  const targetId = targets.some((item) => item.id === selected) ? selected : targets[0]?.id ?? "";
  const selectedTarget = targets.find((item) => item.id === targetId);
  const targetLabel = selectedTarget?.framework === "aspnet-blazor" ? "ASP.NET API" : "C# Console";
  const preview = useMutation({ mutationFn: () => applicationBuilderApi.sourcePreview(project.id, targetId) });
  const download = useMutation({
    mutationFn: () => applicationBuilderApi.downloadSource(project.id, targetId),
    onSuccess: ({ blob, filename, checksum }) => {
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url; anchor.download = filename;
      document.body.append(anchor); anchor.click(); anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
      show(`決定的Source ZIPを生成しました · ${checksum.slice(0, 12)}`);
    },
    onError: (reason) => show(reason instanceof Error ? reason.message : "Source生成に失敗しました", "error"),
  });
  useEffect(() => { preview.reset(); }, [project.updated_at, targetId]);

  return <div className="space-y-4"><section aria-label="Source Generator" className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
    <div className="flex items-start gap-3"><div className="min-w-0 flex-1"><h2 className="text-sm font-semibold">Deterministic Source Generator · B2.5/E7</h2><p className="mt-1 text-xs leading-relaxed text-zinc-500">保存済みSpec／Workflow snapshotからportable C# runtime、ASP.NET API／Blazor GUI／typed query、値と名前を埋め込まないSecret alias、境界付きHTTP／file node、自己test、checksum manifestをメモリ内生成します。PreviewとZIP生成自体は副作用を持ちません。</p></div><span className="rounded-full bg-emerald-50 px-2 py-1 text-[10px] font-semibold text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-300">{targetLabel}</span></div>
    {targets.length === 0 ? <p className="mt-3 rounded-xl bg-zinc-50 p-3 text-xs text-zinc-500 dark:bg-zinc-950">Platform AdvisorでC# ConsoleまたはASP.NET Core targetを保存すると生成前検査を開始できます。</p> : <>
      {targets.length > 1 && <label className="mt-3 block text-[10px] text-zinc-400">Source target<select aria-label="Source target" value={targetId} onChange={(event) => setSelected(event.target.value)} className="mt-1 min-h-11 w-full rounded-lg border border-zinc-300 bg-transparent px-3 text-xs dark:border-zinc-700">{targets.map((item) => <option key={item.id} value={item.id}>{item.id} · {item.framework} · {item.platforms.join("/")}</option>)}</select></label>}
      <button type="button" onClick={() => preview.mutate()} disabled={preview.isPending} className="mt-3 min-h-11 w-full rounded-xl border border-zinc-300 text-xs font-semibold disabled:opacity-40 dark:border-zinc-700">{preview.isPending ? "Generating preview…" : "Preview generated source"}</button>
    </>}
    {preview.error && <p role="alert" className="mt-2 rounded-lg bg-red-50 p-2 text-xs text-red-700 dark:bg-red-950/30 dark:text-red-300">{preview.error instanceof Error ? preview.error.message : "生成前検査に失敗しました"}</p>}
    {preview.data && <section aria-label="Source generation preview" className="mt-4 space-y-3">
      <div className={`rounded-xl p-3 text-xs font-semibold ${preview.data.ready ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-300" : "bg-amber-50 text-amber-700 dark:bg-amber-950/30"}`}>{preview.data.ready ? `Ready · ${preview.data.files?.length ?? 0} files · ${preview.data.archiveBytes ?? 0} bytes` : "Generation blocked"}</div>
      {preview.data.ready && <><dl className="grid gap-2 text-[10px] sm:grid-cols-2"><Checksum label="Source checksum" value={preview.data.sourceChecksum} /><Checksum label="Archive checksum" value={preview.data.archiveChecksum} /><Checksum label="Spec checksum" value={preview.data.manifest?.input.specChecksum} /><Checksum label="Workflow checksum" value={preview.data.manifest?.input.workflowChecksum} /></dl><div className="max-h-52 overflow-auto rounded-xl bg-zinc-50 p-2 dark:bg-zinc-950">{preview.data.files?.map((file) => <div key={file.path} className="flex gap-2 border-b border-zinc-200 py-1 text-[10px] last:border-0 dark:border-zinc-800"><span className="w-16 shrink-0 uppercase text-zinc-400">{file.kind}</span><code className="min-w-0 break-all">{file.path}</code><span className="ml-auto shrink-0 tabular-nums text-zinc-400">{file.bytes} B</span></div>)}</div><button type="button" onClick={() => download.mutate()} disabled={download.isPending} className="min-h-11 w-full rounded-xl bg-emerald-600 text-xs font-semibold text-white disabled:opacity-40">{download.isPending ? "Generating ZIP…" : "Generate source ZIP"}</button></>}
      {preview.data.diagnostics.map((item, index) => <div key={`${item.code}-${index}`} className={`rounded-lg p-2 text-[10px] ${item.severity === "error" ? "bg-red-50 text-red-700 dark:bg-red-950/30 dark:text-red-300" : "bg-amber-50 text-amber-700 dark:bg-amber-950/30"}`}><strong>{item.code}</strong> · {item.message}<code className="mt-1 block break-all opacity-70">{item.path}</code></div>)}
      <p className="text-[10px] leading-relaxed text-zinc-400">生成はexecutor、network、subprocess、file write、Secret解決を行いません。Extensionsはuser-owned、Generatedはmanifest checksum管理です。</p>
    </section>}
  </section><BuildPanel project={project} targetId={targetId} capabilities={capabilities} /></div>;
}

function Checksum({ label, value }: { label: string; value?: string }) {
  return <div className="min-w-0 rounded-lg bg-zinc-50 p-2 dark:bg-zinc-950"><dt className="text-zinc-400">{label}</dt><dd className="mt-1 break-all font-mono">{value ?? "N/A"}</dd></div>;
}
