import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { BottomSheet } from "../../components/ui";
import { useToasts } from "../../stores";

interface IntelligenceReport {
  summary: { nodes: number; edges: number; executions_analyzed: number; successes: number; failures: number; linked_projects: number; test_cases: number };
  quality: { score?: number; grade?: string } | number;
  issues: Array<{ code: string; severity: string; message: string; node_id?: string | null }>;
  unknowns: Array<{ node_id?: string; message: string }>;
  node_health: Array<{ node_id: string; type: string; runs: number; failed: number; timed_out: number; failure_rate: number; average_duration_ms: number | null }>;
  latest_execution: { id: number; status: string; error?: string } | null;
  runtime: { gpu?: { name?: string | null; vram_free_bytes?: number | null }; providers?: Array<{ base_url: string; models: string[]; selected?: boolean; available?: boolean }> };
}

interface PatchPreview {
  valid: boolean;
  errors: string[];
  warnings: string[];
  summary?: { operation_count: number; nodes_before: number; nodes_after: number; edges_before: number; edges_after: number };
  quality_before?: { score?: number } | number;
  quality_after?: { score?: number } | number;
}

interface Diagnosis {
  cause: string;
  confidence: number;
  source: "ai" | "deterministic";
  fallback_reason?: string | null;
  evaluation?: { model?: string | null; provider?: string; elapsed_ms?: number };
  options: Array<{ title: string; impact: string; operations: Array<Record<string, unknown>>; preview?: PatchPreview }>;
}

function qualityValue(value: IntelligenceReport["quality"] | PatchPreview["quality_before"]): string {
  if (typeof value === "number") return String(value);
  if (value && typeof value.score === "number") return String(value.score);
  return "N/A";
}

export function ProjectIntelligencePanel({
  workflowId, dirty, getRevision, onEnsureSaved, onApplied, onClose,
}: {
  workflowId: number;
  dirty: boolean;
  getRevision: () => string | null;
  onEnsureSaved: () => Promise<boolean>;
  onApplied: () => Promise<void>;
  onClose: () => void;
}) {
  const show = useToasts((state) => state.show);
  const qc = useQueryClient();
  const [instruction, setInstruction] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [diagnosis, setDiagnosis] = useState<Diagnosis | null>(null);
  const [working, setWorking] = useState<string | null>(null);
  const report = useQuery({
    queryKey: ["workflow-intelligence", workflowId],
    queryFn: () => api<IntelligenceReport>(`/workflows/${workflowId}/intelligence`),
  });
  const endpoints = (report.data?.runtime.providers ?? []).filter((item) => item.available && item.models.length > 0);
  const selectedEndpoint = endpoints.find((item) => item.base_url === baseUrl);
  const runDiagnosis = async (useAi: boolean) => {
    if (dirty && !await onEnsureSaved()) return;
    setWorking("diagnose");
    try {
      const result = await api<Diagnosis>(`/workflows/${workflowId}/intelligence/diagnose`, {
        method: "POST", json: { instruction, base_url: useAi ? baseUrl : "", model: useAi ? model : "", use_ai: useAi },
      });
      setDiagnosis(result);
    } catch (error) {
      show(error instanceof Error ? error.message : "診断に失敗しました", "error");
    } finally {
      setWorking(null);
    }
  };
  const applyOption = async (index: number) => {
    const option = diagnosis?.options[index];
    if (!option || option.preview?.valid === false) return;
    setWorking(`apply-${index}`);
    try {
      await api(`/workflows/${workflowId}/intelligence/patch-apply`, {
        method: "POST", json: { patch_version: 1, operations: option.operations, expected_updated_at: getRevision() },
      });
      await Promise.all([onApplied(), qc.invalidateQueries({ queryKey: ["workflow-intelligence", workflowId] })]);
      setDiagnosis(null);
      show("選択した修正案を適用しました");
    } catch (error) {
      show(error instanceof Error ? error.message : "修正案を適用できませんでした", "error");
    } finally {
      setWorking(null);
    }
  };
  const createTests = async () => {
    if (dirty && !await onEnsureSaved()) return;
    setWorking("tests");
    try {
      const result = await api<{ test_cases: Array<{ id: number }> }>(`/workflows/${workflowId}/intelligence/auto-tests`, { method: "POST" });
      await qc.invalidateQueries({ queryKey: ["workflow-test-cases", workflowId] });
      show(`Baselineテスト ${result.test_cases.length}件を準備しました`);
    } catch (error) {
      show(error instanceof Error ? error.message : "テスト生成に失敗しました", "error");
    } finally {
      setWorking(null);
    }
  };
  const data = report.data;
  return <BottomSheet title="Project Intelligence" onClose={onClose} wide>
    {report.isLoading ? <p className="py-8 text-center text-sm text-zinc-400">分析中…</p> : report.isError || !data ? (
      <p role="alert" className="rounded-xl bg-red-50 p-3 text-sm text-red-700 dark:bg-red-950/40 dark:text-red-300">Project Intelligenceを読み込めませんでした</p>
    ) : <div className="space-y-5 pb-[env(safe-area-inset-bottom)]">
      <section aria-label="プロジェクト概要" className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {[["Quality", qualityValue(data.quality)], ["Recent", `${data.summary.successes}/${data.summary.executions_analyzed}成功`], ["Issues", String(data.issues.length)], ["Tests", String(data.summary.test_cases)]].map(([label, value]) => (
          <div key={label} className="rounded-xl bg-zinc-50 p-3 dark:bg-zinc-800/60"><p className="text-[10px] uppercase tracking-wide text-zinc-400">{label}</p><p className="num mt-1 text-sm font-semibold">{value}</p></div>
        ))}
      </section>
      <section>
        <div className="mb-2 flex items-center justify-between gap-2"><h3 className="text-sm font-semibold">検出事項</h3><button type="button" onClick={() => void createTests()} disabled={working !== null} className="min-h-11 rounded-xl border border-zinc-300 px-3 text-xs font-semibold disabled:opacity-50 dark:border-zinc-700">Baselineテスト</button></div>
        {data.issues.length + data.unknowns.length === 0 ? <p className="rounded-xl bg-emerald-50 p-3 text-xs text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-300">静的な問題は見つかりませんでした</p> : <ul className="space-y-2">
          {[...data.issues, ...data.unknowns.map((item) => ({ ...item, code: "UNKNOWN", severity: "info" }))].slice(0, 8).map((item, index) => <li key={`${item.code}-${index}`} className="rounded-xl border border-zinc-200 p-3 text-xs dark:border-zinc-700"><span className="mr-2 font-mono text-[10px] text-zinc-400">{item.code}</span>{item.message}</li>)}
        </ul>}
      </section>
      <section className="space-y-3 border-t border-zinc-200 pt-4 dark:border-zinc-800">
        <div><h3 className="text-sm font-semibold">失敗を診断</h3><p className="mt-1 text-xs text-zinc-400">実行ログとruntimeを秘匿化して分析し、変更は選択した案だけ適用します。</p></div>
        <textarea value={instruction} onChange={(event) => setInstruction(event.target.value)} rows={2} maxLength={4000} placeholder="例: timeoutを増やさず根本原因を直して" aria-label="AIへの再検討指示" className="w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-900" />
        <div className="grid gap-2 sm:grid-cols-2">
          <select aria-label="診断endpoint" value={baseUrl} onChange={(event) => { setBaseUrl(event.target.value); setModel(""); }} className="min-h-11 rounded-xl border border-zinc-300 bg-white px-3 text-sm dark:border-zinc-700 dark:bg-zinc-900"><option value="">AIを選択</option>{endpoints.map((item) => <option key={item.base_url} value={item.base_url}>{item.base_url}</option>)}</select>
          <select aria-label="診断model" value={model} onChange={(event) => setModel(event.target.value)} disabled={!selectedEndpoint} className="min-h-11 rounded-xl border border-zinc-300 bg-white px-3 text-sm disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-900"><option value="">modelを選択</option>{selectedEndpoint?.models.map((item) => <option key={item} value={item}>{item}</option>)}</select>
        </div>
        <div className="grid grid-cols-2 gap-2"><button type="button" onClick={() => void runDiagnosis(false)} disabled={working !== null} className="min-h-11 rounded-xl border border-zinc-300 px-3 text-xs font-semibold disabled:opacity-50 dark:border-zinc-700">ローカル診断</button><button type="button" onClick={() => void runDiagnosis(true)} disabled={working !== null || !baseUrl || !model} className="min-h-11 rounded-xl bg-accent-600 px-3 text-xs font-semibold text-white disabled:opacity-50">{working === "diagnose" ? "診断中…" : "AIで再検討"}</button></div>
      </section>
      {diagnosis && <section aria-live="polite" className="space-y-3 border-t border-zinc-200 pt-4 dark:border-zinc-800">
        <div><span className="rounded-full bg-zinc-100 px-2 py-1 text-[10px] font-semibold dark:bg-zinc-800">{diagnosis.source === "ai" ? `AI · ${diagnosis.evaluation?.model}` : "Deterministic fallback"}</span><p className="mt-2 text-sm">{diagnosis.cause}</p><p className="mt-1 text-xs text-zinc-400">確信度 {Math.round(diagnosis.confidence * 100)}%</p></div>
        {diagnosis.options.map((option, index) => <article key={`${option.title}-${index}`} className="rounded-2xl border border-zinc-200 p-3 dark:border-zinc-700"><h4 className="text-sm font-semibold">{option.title}</h4><p className="mt-1 text-xs text-zinc-500">{option.impact}</p><p className="mt-2 text-[11px] text-zinc-400">変更 {option.operations.length}件 · Quality {qualityValue(option.preview?.quality_before)} → {qualityValue(option.preview?.quality_after)}{option.preview?.warnings.length ? ` · 警告 ${option.preview.warnings.length}` : ""}</p>{option.preview?.errors.map((error) => <p key={error} className="mt-1 text-xs text-red-600">{error}</p>)}<details className="mt-2"><summary className="cursor-pointer text-xs text-zinc-500">操作差分を確認</summary><pre className="mt-2 max-h-44 overflow-auto rounded-xl bg-zinc-950 p-3 text-[10px] text-zinc-100">{JSON.stringify(option.operations, null, 2)}</pre></details><button type="button" onClick={() => void applyOption(index)} disabled={working !== null || option.preview?.valid === false || option.operations.length === 0} className="mt-3 min-h-11 w-full rounded-xl bg-accent-600 px-3 text-xs font-semibold text-white disabled:opacity-40">{working === `apply-${index}` ? "適用中…" : option.operations.length ? "この案を適用" : "変更なし"}</button></article>)}
      </section>}
    </div>}
  </BottomSheet>;
}
