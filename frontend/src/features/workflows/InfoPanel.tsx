/** エディタの情報パネル — 実行状況（ライブ）/ 履歴 / バージョン。
 *
 * 計算はすべてサーバー側。永続イベントを購読して表示・操作する。
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { useAuth, useToasts } from "../../stores";
import { IconX } from "../../components/icons";
import { NODE_TYPES } from "./nodeTypes";
import { ApprovalResponseFields, missingRequiredFormFields, type ApprovalFormSchema } from "./ApprovalResponseFields";

interface ExecSummary {
  id: number;
  status: string;
  trigger_type: string;
  started_at: string;
  finished_at: string | null;
  error: string;
}
interface NodeEntry {
  status: string;
  name?: string;
  type?: string;
  error?: string;
  attempts?: number;
  started_at?: string;
  finished_at?: string;
  output?: Record<string, unknown>;
  approval?: { message?: string; approver?: string };
}
interface LiveExec {
  id: number;
  status: string;
  running: boolean;
  started_at: string;
  finished_at: string | null;
  error: string;
  context: Record<string, NodeEntry>;
  pending_approvals: Array<{ node_id: string; interaction_type?: "approval" | "form"; message: string; approver: string; expires_at?: string | null; form_schema?: ApprovalFormSchema }>;
  total_tokens: number;
}
interface VersionRow { id: number; name: string; note: string; created_at: string; node_count: number }

const STATUS_STYLE: Record<string, string> = {
  SUCCEEDED: "text-emerald-600 dark:text-emerald-400",
  FAILED: "text-red-600 dark:text-red-400",
  TIMED_OUT: "text-amber-600 dark:text-amber-400",
  RUNNING: "text-accent-600 dark:text-accent-400",
  RETRYING: "text-amber-600 dark:text-amber-400",
  WAITING: "text-amber-600 dark:text-amber-400",
  WAITING_APPROVAL: "text-amber-600 dark:text-amber-400",
  WAITING_FORM: "text-amber-600 dark:text-amber-400",
  CANCELED: "text-zinc-400",
  SKIPPED: "text-zinc-400",
  PENDING: "text-zinc-400",
};
const STATUS_LABEL: Record<string, string> = {
  SUCCEEDED: "成功", FAILED: "失敗", TIMED_OUT: "時間切れ", RUNNING: "実行中",
  RETRYING: "リトライ中", WAITING: "入力待ち", WAITING_APPROVAL: "承認待ち", WAITING_FORM: "フォーム待ち",
  CANCELED: "中止", SKIPPED: "スキップ", PENDING: "待機", QUEUED: "待機",
};

function parseTs(s?: string | null): number | null {
  if (!s) return null;
  const t = Date.parse(s.endsWith("Z") || s.includes("+") ? s : s + "Z");
  return Number.isNaN(t) ? null : t;
}

function fmtDur(ms: number): string {
  if (ms < 1000) return `${Math.max(0, Math.round(ms))}ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}秒`;
  return `${Math.floor(s / 60)}分${Math.round(s % 60)}秒`;
}

export function InfoPanel({
  workflowId,
  nodeNames,
  onStatuses,
  onClose,
}: {
  workflowId: number;
  /** キャンバス上のノード ID → 表示名（コンテキストに無いノードの補完用） */
  nodeNames: Record<string, { name: string; type: string }>;
  /** ライブ実行のノード状態をエディタへ通知（キャンバス点灯用） */
  onStatuses: (statuses: Record<string, string>) => void;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<"live" | "history" | "versions">("live");
  const [detailId, setDetailId] = useState<number | null>(null);
  const show = useToasts((s) => s.show);
  const can = useAuth((s) => s.can);
  const qc = useQueryClient();
  const [now, setNow] = useState(Date.now());
  const [streamState, setStreamState] = useState<"idle" | "connecting" | "live" | "reconnecting">("idle");
  const lastSequence = useRef<Record<number, number>>({});
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const { data: executions } = useQuery({
    queryKey: ["executions", workflowId],
    queryFn: () => api<ExecSummary[]>(`/workflow-executions?workflow_id=${workflowId}`),
    // 外部スケジュールによる新規実行の発見用。実行中の更新はSSEが担当する。
    refetchInterval: streamState === "live" ? false : 15_000,
  });
  const latest = executions?.[0];
  const targetId = tab === "live" ? (latest?.id ?? null) : detailId;
  const targetRunning = tab === "live" && latest && ["RUNNING", "WAITING", "QUEUED"].includes(latest.status);

  const { data: live } = useQuery({
    queryKey: ["exec-live", targetId],
    queryFn: () => api<LiveExec>(`/workflow-executions/${targetId}/live`),
    enabled: targetId !== null,
    // SSE切断中だけ低頻度でフォールバックする。
    refetchInterval: targetRunning && streamState === "reconnecting" ? 3000 : false,
  });

  useEffect(() => {
    if (!targetId || !targetRunning || tab !== "live") {
      setStreamState("idle");
      return;
    }
    let active = true;
    let refreshTimer: number | undefined;
    setStreamState("connecting");
    const cursor = lastSequence.current[targetId] ?? 0;
    const source = new EventSource(
      `/api/v1/workflow-executions/${targetId}/stream?after_sequence=${cursor}`,
      { withCredentials: true },
    );
    source.onopen = () => active && setStreamState("live");
    source.onerror = () => active && setStreamState("reconnecting");
    const onEvent = (raw: Event) => {
      if (!(raw instanceof MessageEvent)) return;
      try {
        const event = JSON.parse(raw.data) as { sequence?: number; type?: string };
        if (typeof event.sequence === "number") lastSequence.current[targetId] = event.sequence;
        window.clearTimeout(refreshTimer);
        refreshTimer = window.setTimeout(() => {
          void qc.invalidateQueries({ queryKey: ["exec-live", targetId] });
          void qc.invalidateQueries({ queryKey: ["executions", workflowId] });
        }, 60);
        if (event.type === "stream.closed") {
          source.close();
          setStreamState("idle");
        }
      } catch {
        // 壊れた単一イベントは無視し、次のシーケンスを待つ。
      }
    };
    source.addEventListener("workflow", onEvent);
    return () => {
      active = false;
      window.clearTimeout(refreshTimer);
      source.removeEventListener("workflow", onEvent);
      source.close();
    };
  }, [targetId, targetRunning, tab, qc, workflowId]);

  // キャンバス点灯: ライブタブの対象実行のノード状態を親へ
  useEffect(() => {
    if (tab !== "live" || !live) return;
    const map: Record<string, string> = {};
    for (const [nid, e] of Object.entries(live.context)) map[nid] = e.status;
    onStatuses(map);
  }, [live, tab, onStatuses]);

  const cancel = useMutation({
    mutationFn: (execId: number) => api(`/workflow-executions/${execId}/cancel`, { method: "POST" }),
    onSuccess: () => { show("強制停止しました"); qc.invalidateQueries({ queryKey: ["executions", workflowId] }); },
    onError: (e) => show(e instanceof Error ? e.message : "停止に失敗しました", "error"),
  });
  const approve = useMutation({
    mutationFn: (p: { execId: number; nodeId: string; approve: boolean; response: Record<string, unknown> }) =>
      api(`/workflow-executions/${p.execId}/approve`, { method: "POST", json: { node_id: p.nodeId, approve: p.approve, response: p.response } }),
    onSuccess: (_d, p) => show(p.approve ? "承認しました" : "却下しました"),
    onError: (e) => show(e instanceof Error ? e.message : "操作に失敗しました", "error"),
  });

  const { data: versions } = useQuery({
    queryKey: ["wf-versions", workflowId],
    queryFn: () => api<VersionRow[]>(`/workflows/${workflowId}/versions`),
    enabled: tab === "versions",
  });
  const restore = useMutation({
    mutationFn: (versionId: number) =>
      api(`/workflows/${workflowId}/versions/${versionId}/restore`, { method: "POST" }),
    onSuccess: () => {
      show("復元しました。エディタを再読込します");
      qc.invalidateQueries({ queryKey: ["workflow", workflowId] });
      qc.invalidateQueries({ queryKey: ["wf-versions", workflowId] });
    },
    onError: (e) => show(e instanceof Error ? e.message : "復元に失敗しました", "error"),
  });

  const detail = live && targetId !== null ? live : null;

  return (
    <div aria-label="実行デバッグパネル" className="absolute inset-x-2 bottom-2 z-20 flex h-[min(46%,26rem)] flex-col rounded-2xl border border-zinc-200 bg-white shadow-2xl dark:border-zinc-700 dark:bg-zinc-900 sm:inset-x-4">
      <div className="flex items-center gap-1 border-b border-zinc-200 px-3 py-2 dark:border-zinc-800">
        {([["live", "実行状況"], ["history", "履歴"], ["versions", "バージョン"]] as const).map(([key, label]) => (
          <button
            key={key}
            onClick={() => { setTab(key); setDetailId(null); }}
            className={`rounded-lg px-2.5 py-1.5 text-xs font-medium ${
              tab === key ? "bg-accent-50 text-accent-700 dark:bg-accent-600/15 dark:text-accent-400" : "text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"
            }`}
          >
            {label}
          </button>
        ))}
        {tab === "live" && targetRunning && (
          <span
            role="status"
            className="ml-1 inline-flex items-center gap-1.5 text-[10px] text-zinc-400"
            title={streamState === "live" ? "実行イベントをリアルタイム受信中" : "再接続中は低頻度で状態を確認します"}
          >
            <span className={`h-1.5 w-1.5 rounded-full ${streamState === "live" ? "bg-emerald-500" : "animate-pulse bg-amber-500"}`} />
            {streamState === "live" ? "ライブ" : "接続中"}
          </span>
        )}
        <button onClick={onClose} aria-label="閉じる" className="ml-auto rounded-lg p-1.5 text-zinc-400 hover:bg-zinc-100 dark:hover:bg-zinc-800">
          <IconX />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {tab === "versions" ? (
          !versions || versions.length === 0 ? (
            <p className="py-8 text-center text-xs text-zinc-400">保存すると以前の定義がここに残ります（20 世代）</p>
          ) : (
            <ul className="space-y-2">
              {versions.map((v) => (
                <li key={v.id} className="flex items-center gap-2 rounded-xl border border-zinc-200 px-3 py-2 dark:border-zinc-700">
                  <div className="min-w-0 flex-1">
                    <p className="text-xs font-medium">
                      {new Date(parseTs(v.created_at) ?? 0).toLocaleString("ja-JP")}
                    </p>
                    <p className="text-[11px] text-zinc-400">ノード {v.node_count} 個{v.note && ` · ${v.note}`}</p>
                  </div>
                  {can("workflows.edit") && (
                    <button
                      onClick={() => restore.mutate(v.id)}
                      disabled={restore.isPending}
                      className="shrink-0 rounded-lg bg-zinc-100 px-2.5 py-1 text-[11px] font-medium hover:bg-zinc-200 disabled:opacity-40 dark:bg-zinc-800"
                    >
                      この版に戻す
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )
        ) : tab === "history" && detailId === null ? (
          !executions || executions.length === 0 ? (
            <p className="py-8 text-center text-xs text-zinc-400">実行履歴はありません</p>
          ) : (
            <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
              {executions.map((ex) => {
                const st = parseTs(ex.started_at);
                const fin = parseTs(ex.finished_at);
                return (
                  <li key={ex.id}>
                    <button onClick={() => setDetailId(ex.id)} className="flex w-full items-center gap-2 py-2 text-left">
                      <span className={`w-14 shrink-0 text-[11px] font-medium ${STATUS_STYLE[ex.status] ?? "text-zinc-400"}`}>
                        {STATUS_LABEL[ex.status] ?? ex.status}
                      </span>
                      <span className="num min-w-0 flex-1 truncate text-[11px] text-zinc-400">
                        {st ? new Date(st).toLocaleString("ja-JP") : ""}
                        {st && fin ? ` · ${fmtDur(fin - st)}` : ""}
                        {" · "}
                        {{ manual: "手動", schedule: "定期", chat: "チャット", webhook: "Webhook", event: "イベント", "system:gpu": "GPU監視", "system:vram": "VRAM監視", "system:disk": "ディスク監視", "system:systemd": "アプリ監視", "system:llama_server": "LLM監視", "system:file": "ファイル監視", subflow: "サブフロー", "chat-build": "自動ビルド" }[ex.trigger_type] ?? ex.trigger_type}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )
        ) : detail === null ? (
          <p className="py-8 text-center text-xs text-zinc-400">
            {tab === "live" ? "まだ実行がありません。「実行」を押すとここにライブ状況が表示されます" : "読み込み中..."}
          </p>
        ) : (
          <ExecDetail
            live={detail}
            now={now}
            nodeNames={nodeNames}
            onBack={tab === "history" ? () => setDetailId(null) : undefined}
            onCancel={can("workflows.run") ? () => cancel.mutate(detail.id) : undefined}
            onApprove={can("workflows.run") ? (nodeId, ok, response) => approve.mutate({ execId: detail.id, nodeId, approve: ok, response }) : undefined}
          />
        )}
      </div>
    </div>
  );
}

function ExecDetail({
  live, now, nodeNames, onBack, onCancel, onApprove,
}: {
  live: LiveExec;
  now: number;
  nodeNames: Record<string, { name: string; type: string }>;
  onBack?: () => void;
  onCancel?: () => void;
  onApprove?: (nodeId: string, approve: boolean, response: Record<string, unknown>) => void;
}) {
  const [approvalResponses, setApprovalResponses] = useState<Record<string, Record<string, unknown>>>({});
  const running = live.running || ["RUNNING", "WAITING"].includes(live.status);
  const started = parseTs(live.started_at);
  const finished = parseTs(live.finished_at);
  const elapsed = started ? (finished ?? now) - started : 0;
  const entries = useMemo(() => Object.entries(live.context), [live.context]);

  return (
    <div className="space-y-2.5">
      {onBack && (
        <button onClick={onBack} className="text-xs text-accent-600 dark:text-accent-400">← 履歴一覧へ</button>
      )}
      {/* サマリー */}
      <div className="rounded-xl bg-zinc-50 p-3 dark:bg-zinc-800/60">
        <div className="flex items-center gap-2">
          <span className={`text-sm font-semibold ${STATUS_STYLE[live.status] ?? ""}`}>
            {STATUS_LABEL[live.status] ?? live.status}
          </span>
          <span className="num text-[11px] text-zinc-400">#{live.id}</span>
          <span className="num ml-auto text-xs text-zinc-500">
            {running && <span className="mr-1 inline-block h-2 w-2 animate-pulse rounded-full bg-accent-500 align-middle" />}
            経過 {fmtDur(elapsed)}
          </span>
        </div>
        {live.total_tokens > 0 && (
          <p className="num mt-1 text-[11px] text-zinc-400">LLM 使用トークン合計: {live.total_tokens.toLocaleString()}</p>
        )}
        {live.error && (
          <p className="mt-1.5 whitespace-pre-wrap rounded-lg bg-red-50 px-2.5 py-1.5 text-[11px] text-red-600 dark:bg-red-950/40 dark:text-red-400">
            {live.error}
          </p>
        )}
        {running && onCancel && (
          <button
            onClick={onCancel}
            className="mt-2 w-full rounded-lg bg-red-50 py-1.5 text-xs font-medium text-red-600 hover:bg-red-100 dark:bg-red-950/40 dark:text-red-400"
          >
            ⏹ 強制停止
          </button>
        )}
      </div>

      {/* 承認待ち */}
      {live.pending_approvals.map((approval) => {
        const nid = approval.node_id;
        const isForm = approval.interaction_type === "form";
        const response = approvalResponses[nid] ?? {};
        const missing = missingRequiredFormFields(approval.form_schema, response);
        return (
          <div key={nid} className="rounded-xl border border-amber-300 bg-amber-50 p-3 dark:border-amber-700 dark:bg-amber-950/40">
            <p className="text-xs font-medium text-amber-800 dark:text-amber-300">
              {isForm ? "▤" : "✋"} 「{live.context[nid]?.name ?? nodeNames[nid]?.name ?? nid}」が{isForm ? "入力" : "承認"}を待っています
            </p>
            <p className="mt-1 whitespace-pre-wrap text-xs text-amber-700 dark:text-amber-200">{approval.message}</p>
            {approval.approver && <p className="mt-1 text-[10px] text-amber-600 dark:text-amber-400">{isForm ? "担当者" : "承認者"}: {approval.approver}</p>}
            {approval.expires_at && <p className="mt-1 text-[10px] text-amber-600 dark:text-amber-400">期限: {new Date(approval.expires_at).toLocaleString("ja-JP")}</p>}
            <ApprovalResponseFields idPrefix={`debug-interaction-${nid}`} schema={approval.form_schema} value={response} onChange={(next) => setApprovalResponses((current) => ({ ...current, [nid]: next }))} />
            {onApprove && (
              <div className="mt-2 flex gap-2">
                <button disabled={missing.length > 0} onClick={() => onApprove(nid, true, response)} className="flex-1 rounded-lg bg-emerald-600 py-1.5 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-40">{isForm ? "送信して続行" : "承認して続行"}</button>
                <button onClick={() => onApprove(nid, false, response)} className="flex-1 rounded-lg bg-zinc-200 py-1.5 text-xs font-medium text-zinc-700 hover:bg-zinc-300 dark:bg-zinc-700 dark:text-zinc-200">{isForm ? "キャンセル" : "却下"}</button>
              </div>
            )}
          </div>
        );
      })}

      {/* ノードごとの状況 */}
      {entries.length === 0 && <p className="py-4 text-center text-xs text-zinc-400">ノードの記録はまだありません</p>}
      {entries.map(([nid, e]) => {
        const meta = NODE_TYPES[e.type ?? nodeNames[nid]?.type ?? ""];
        const st = parseTs(e.started_at);
        const fin = parseTs(e.finished_at);
        const dur = st ? (fin ?? (["RUNNING", "RETRYING", "WAITING_APPROVAL", "WAITING_FORM"].includes(e.status) ? now : st)) - st : null;
        const outPreview =
          e.output !== undefined ? JSON.stringify(e.output, null, 0) : "";
        return (
          <div key={nid} className={`rounded-xl border p-2.5 ${
            e.status === "RUNNING" ? "border-accent-400" : "border-zinc-200 dark:border-zinc-800"
          }`}>
            <div className="flex items-center gap-2">
              <span className="grid h-6 w-6 shrink-0 place-items-center rounded-md text-xs" style={{ backgroundColor: `${meta?.color ?? "#888"}1a`, color: meta?.color }}>
                {meta?.icon ?? "●"}
              </span>
              <span className="min-w-0 flex-1 truncate text-xs font-medium">
                {e.name ?? nodeNames[nid]?.name ?? nid}
              </span>
              {typeof e.attempts === "number" && e.attempts > 1 && (
                <span className="shrink-0 rounded bg-amber-100 px-1 text-[10px] text-amber-700 dark:bg-amber-900/60 dark:text-amber-300">{e.attempts}回目</span>
              )}
              {dur !== null && <span className="num shrink-0 text-[10px] text-zinc-400">{fmtDur(dur)}</span>}
              <span className={`shrink-0 text-[11px] font-medium ${STATUS_STYLE[e.status] ?? "text-zinc-400"}`}>
                {e.status === "RUNNING" && <span className="mr-0.5 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-accent-500 align-middle" />}
                {STATUS_LABEL[e.status] ?? e.status}
              </span>
            </div>
            {e.error && <p className="mt-1 text-[11px] text-red-500">{e.error}</p>}
            {outPreview && e.status !== "SKIPPED" && (
              <details className="mt-1">
                <summary className="cursor-pointer truncate font-mono text-[10px] text-zinc-400">
                  {outPreview.slice(0, 90)}{outPreview.length > 90 ? "…" : ""}
                </summary>
                <pre className="mt-1 max-h-40 overflow-auto rounded bg-zinc-50 p-2 font-mono text-[10px] dark:bg-zinc-950">
                  {JSON.stringify(e.output, null, 1)}
                </pre>
              </details>
            )}
          </div>
        );
      })}
    </div>
  );
}
