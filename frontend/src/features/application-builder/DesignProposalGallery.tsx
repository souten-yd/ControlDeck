import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { applicationBuilderApi, type ApplicationDesignProposal, type ApplicationPatchOperation, type ApplicationProject } from "../../api/applicationBuilder";
import { BottomSheet } from "../../components/ui";

type Scope = "application" | "page" | "component" | "mobile";
type Mode = "preserve" | "balanced" | "redesign";

export function DesignProposalGallery({ project, selectedComponentId, onReview, onClose }: {
  project: ApplicationProject;
  selectedComponentId: string | null;
  onReview: (patches: ApplicationPatchOperation[]) => void;
  onClose: () => void;
}) {
  const endpoints = useQuery({ queryKey: ["llm-endpoints"], queryFn: applicationBuilderApi.llmEndpoints });
  const options = useMemo(() => (endpoints.data ?? []).flatMap((endpoint) => endpoint.models.map((model) => ({ ...endpoint, model }))), [endpoints.data]);
  const preferred = options.find((item) => item.selected) ?? options[0];
  const [endpointKey, setEndpointKey] = useState("");
  const [instruction, setInstruction] = useState("");
  const [scope, setScope] = useState<Scope>(selectedComponentId ? "component" : "application");
  const [mode, setMode] = useState<Mode>("balanced");
  useEffect(() => { if (!endpointKey && preferred) setEndpointKey(JSON.stringify([preferred.base_url, preferred.model])); }, [endpointKey, preferred]);
  const selectedEndpoint = options.find((item) => JSON.stringify([item.base_url, item.model]) === endpointKey);
  const generate = useMutation({
    mutationFn: () => applicationBuilderApi.designProposals(project.id, {
      instruction: instruction.trim(), scope, mode,
      ...(scope === "component" && selectedComponentId ? { target_id: selectedComponentId } : {}),
      base_url: selectedEndpoint!.base_url, model: selectedEndpoint!.model,
    }),
  });
  const canGenerate = instruction.trim().length >= 3 && Boolean(selectedEndpoint) && (scope !== "component" || selectedComponentId);

  return <BottomSheet title="AI Design Proposals" onClose={onClose} wide stable><div className="space-y-4 pb-4">
    <div className="rounded-xl bg-violet-50 p-3 text-xs leading-relaxed text-violet-700 dark:bg-violet-950/30 dark:text-violet-300">選択したローカルLLMを必要時に起動・ロードし、自由codeではなくSimple／Balanced／DenseのApplication Spec Patchを3案生成します。自動適用はしません。</div>
    <label className="block text-xs font-medium">Design request<textarea aria-label="AI design request" value={instruction} onChange={(event) => setInstruction(event.target.value)} rows={4} placeholder="iPhoneで主要操作を見つけやすくし、情報量は維持する" className="mt-1 w-full rounded-xl border border-zinc-300 bg-transparent p-3 text-sm dark:border-zinc-700" /></label>
    <div className="grid gap-3 sm:grid-cols-3"><label className="text-xs text-zinc-500">Scope<select aria-label="Design scope" value={scope} onChange={(event) => setScope(event.target.value as Scope)} className="mt-1 min-h-11 w-full rounded-xl border border-zinc-300 bg-transparent px-3 dark:border-zinc-700"><option value="application">Whole application</option><option value="mobile">Mobile layout</option><option value="component" disabled={!selectedComponentId}>Selected component</option></select></label><label className="text-xs text-zinc-500">Mode<select aria-label="Redesign mode" value={mode} onChange={(event) => setMode(event.target.value as Mode)} className="mt-1 min-h-11 w-full rounded-xl border border-zinc-300 bg-transparent px-3 dark:border-zinc-700"><option value="preserve">Preserve</option><option value="balanced">Balanced</option><option value="redesign">Redesign</option></select></label><label className="text-xs text-zinc-500">Model<select aria-label="Design model" value={endpointKey} onChange={(event) => setEndpointKey(event.target.value)} className="mt-1 min-h-11 w-full rounded-xl border border-zinc-300 bg-transparent px-3 dark:border-zinc-700"><option value="">Select model</option>{options.map((item) => <option key={`${item.base_url}-${item.model}`} value={JSON.stringify([item.base_url, item.model])}>{item.model} · {new URL(item.base_url).port || new URL(item.base_url).hostname}</option>)}</select></label></div>
    {options.length === 0 && !endpoints.isLoading && <p role="alert" className="rounded-xl bg-amber-50 p-3 text-xs text-amber-700 dark:bg-amber-950/30">利用可能なLLM modelがありません。ModelsでLM Studio／Ollama／llama.cpp endpointを確認してください。</p>}
    <button onClick={() => generate.mutate()} disabled={!canGenerate || generate.isPending} className="min-h-12 w-full rounded-xl bg-violet-600 text-sm font-semibold text-white disabled:opacity-40">{generate.isPending ? "Generating 3 proposals…" : "Generate 3 proposals"}</button>
    {generate.error && <p role="alert" className="rounded-xl bg-red-50 p-3 text-xs text-red-600 dark:bg-red-950/30 dark:text-red-300">{generate.error instanceof Error ? generate.error.message : "設計案を生成できませんでした"}</p>}
    {generate.data && <section aria-label="Design proposals" className="grid gap-3 lg:grid-cols-3">{generate.data.proposals.map((proposal) => <ProposalCard key={proposal.id} proposal={proposal} onReview={() => onReview(proposal.patches)} />)}</section>}
  </div></BottomSheet>;
}

function ProposalCard({ proposal, onReview }: { proposal: ApplicationDesignProposal; onReview: () => void }) {
  return <article className="flex min-w-0 flex-col rounded-2xl border border-zinc-200 p-4 dark:border-zinc-700"><span className="text-[10px] font-semibold uppercase tracking-wider text-violet-500">{proposal.direction}</span><h3 className="mt-1 text-sm font-semibold">{proposal.title}</h3><p className="mt-2 text-xs leading-relaxed text-zinc-500">{proposal.summary}</p><p className="mt-3 text-[10px] text-zinc-400">{proposal.patches.length} changes · {proposal.preview.valid ? "Valid" : "Needs review"}</p>{proposal.rationale.length > 0 && <ul className="mt-2 list-disc space-y-1 pl-4 text-[10px] text-zinc-500">{proposal.rationale.map((item) => <li key={item}>{item}</li>)}</ul>}{proposal.preview.diagnostics.length > 0 && <p className="mt-2 text-[10px] text-amber-600">{proposal.preview.diagnostics[0].message}</p>}<button onClick={onReview} className="mt-auto min-h-11 rounded-xl border border-violet-300 px-3 text-xs font-semibold text-violet-700 dark:border-violet-800 dark:text-violet-300">Review this proposal</button></article>;
}
