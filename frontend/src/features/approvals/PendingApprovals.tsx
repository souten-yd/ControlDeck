/** L2 の承認待ち（docs/POLICY-autonomous-development.md）。
 *
 * 承認が要るときに気づかないと、自律開発がそこで止まる。だから 3 か所から届くようにする:
 * - どの画面でも上部に「承認 N」（ApprovalsCenter、AppLayout に置く）
 * - まだこのブラウザで見ていない依頼が来たら、ポップアップで開く
 * - 通知からは `/?approvals=1` で直接開く
 * 中身は差分ではなく、平易な要約と「なぜ承認が要るか」。承認すると ControlDeck が PR を
 * マージする（依頼の後に PR が変わっていればマージしない）。管理者にだけ見える。 */
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { BottomSheet, ConfirmDialog } from "../../components/ui";
import { useAuth, useToasts } from "../../stores";

interface Approval {
  id: string; repo: string; pr: number; head_sha: string; title: string; summary: string;
  risks: string[]; status: string; created_at: number; result: string;
}

const queryKey = ["approvals"];
const SEEN_KEY = "control-deck-approvals-seen";

function useApprovals() {
  const canManage = useAuth((s) => s.can("settings.manage"));
  const { data } = useQuery({
    queryKey,
    queryFn: () => api<{ approvals: Approval[] }>("/system/approvals"),
    enabled: canManage,
    refetchInterval: 30_000,
    refetchOnWindowFocus: true,
  });
  return { canManage, items: data?.approvals ?? [] };
}

function readSeen(): string[] {
  try {
    const value = JSON.parse(localStorage.getItem(SEEN_KEY) || "[]");
    return Array.isArray(value) ? value.filter((v) => typeof v === "string") : [];
  } catch {
    return [];
  }
}

function markSeen(ids: string[]) {
  try {
    localStorage.setItem(SEEN_KEY, JSON.stringify([...new Set([...readSeen(), ...ids])].slice(-200)));
  } catch {
    /* 見た記録が残らなくても、次にまた開くだけ */
  }
}

function ApprovalCards({ items }: { items: Approval[] }) {
  const show = useToasts((s) => s.show);
  const qc = useQueryClient();
  const [rejecting, setRejecting] = useState<Approval | null>(null);
  const decide = useMutation({
    mutationFn: ({ id, action }: { id: string; action: "approve" | "reject" }) =>
      api<Approval>(`/system/approvals/${id}/${action}`, { method: "POST", json: {} }),
    onSuccess: (record) => {
      qc.invalidateQueries({ queryKey });
      show(record.status === "merged" ? "承認しました。変更を取り込みました"
        : record.status === "rejected" ? "却下しました" : record.result, record.status === "failed" ? "error" : "info");
    },
    onError: (error) => show(error instanceof Error ? error.message : "処理できませんでした", "error"),
  });
  return (
    <>
      <ul className="space-y-2">
        {items.map((item) => (
          <li key={item.id} className="rounded-xl border border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-900">
            <p className="text-sm font-medium">{item.title}</p>
            <p className="mt-1 whitespace-pre-wrap text-xs leading-relaxed text-zinc-600 dark:text-zinc-300">{item.summary}</p>
            {item.risks.length > 0 && (
              <div className="mt-2 rounded-lg bg-amber-50 px-2 py-1.5 dark:bg-amber-950/40">
                <p className="text-[11px] font-medium text-amber-900 dark:text-amber-200">承認が要る理由</p>
                <ul className="text-[11px] text-amber-800 dark:text-amber-300">
                  {item.risks.map((risk, index) => <li key={index}>・{risk}</li>)}
                </ul>
              </div>
            )}
            <p className="mt-1 text-[11px] text-zinc-400">
              <a href={`https://github.com/${item.repo}/pull/${item.pr}`} target="_blank" rel="noopener noreferrer" className="underline">
                {item.repo} #{item.pr}
              </a>{item.status === "merging" ? " · 取り込み中…" : ""}
            </p>
            <div className="mt-2 flex gap-2">
              <button type="button" disabled={decide.isPending || item.status !== "pending"}
                onClick={() => decide.mutate({ id: item.id, action: "approve" })}
                className="min-h-11 flex-1 rounded-xl bg-accent-600 px-4 text-sm font-semibold text-white disabled:opacity-40">
                {decide.isPending && decide.variables?.id === item.id && decide.variables.action === "approve" ? "取り込み中…" : "承認する"}
              </button>
              <button type="button" disabled={decide.isPending || item.status !== "pending"}
                onClick={() => setRejecting(item)}
                className="min-h-11 rounded-xl border border-zinc-300 px-4 text-sm disabled:opacity-40 dark:border-zinc-700">
                却下
              </button>
            </div>
          </li>
        ))}
      </ul>
      {rejecting && <ConfirmDialog
        title="この変更を却下しますか？"
        message={`「${rejecting.title}」は取り込まれません。PR は残ります。`}
        confirmLabel="却下する"
        onConfirm={() => { const target = rejecting; setRejecting(null); decide.mutate({ id: target.id, action: "reject" }); }}
        onClose={() => setRejecting(null)}
      />}
    </>
  );
}

/** どの画面でも上部に出す入口と、新しい依頼のポップアップ。 */
export function ApprovalsCenter() {
  const { canManage, items } = useApprovals();
  const [params, setParams] = useSearchParams();
  const [open, setOpen] = useState(false);
  const pending = items.filter((item) => item.status === "pending" || item.status === "merging");

  useEffect(() => {
    if (!canManage) return;
    if (params.get("approvals") === "1") {
      setOpen(true);
      const next = new URLSearchParams(params);
      next.delete("approvals");
      setParams(next, { replace: true });
      return;
    }
    const seen = readSeen();
    if (pending.some((item) => !seen.includes(item.id))) setOpen(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canManage, pending.map((item) => item.id).join(","), params.get("approvals")]);

  const close = () => {
    markSeen(pending.map((item) => item.id));
    setOpen(false);
  };
  if (!canManage) return null;
  return (
    <>
      {pending.length > 0 && (
        <button type="button" onClick={() => setOpen(true)} aria-label={`承認待ち ${pending.length} 件を開く`}
          className="flex min-h-9 items-center gap-1.5 rounded-full bg-amber-100 px-3 text-xs font-semibold text-amber-900 hover:bg-amber-200 dark:bg-amber-900/50 dark:text-amber-100">
          <span className="h-2 w-2 animate-pulse rounded-full bg-amber-500" aria-hidden />
          承認 {pending.length}
        </button>
      )}
      {open && pending.length > 0 && (
        <BottomSheet title={`承認待ち（${pending.length}）`} onClose={close} sideOnDesktop>
          <p className="mb-2 text-xs text-zinc-500">
            安全の境界や利用者データに関わる変更です。要約と理由を見て判断してください。承認すると取り込みます。
          </p>
          <ApprovalCards items={pending} />
        </BottomSheet>
      )}
    </>
  );
}

/** ホーム画面の一覧（ポップアップを閉じた後もここから処理できる）。 */
export function PendingApprovals() {
  const { canManage, items } = useApprovals();
  if (!canManage || items.length === 0) return null;
  return (
    <section aria-label="承認待ち" className="space-y-2 rounded-2xl border border-amber-300 bg-amber-50 p-3 dark:border-amber-700/60 dark:bg-amber-950/30 md:p-4">
      <h2 className="text-sm font-semibold text-amber-900 dark:text-amber-200">承認待ち（{items.length}）</h2>
      <ApprovalCards items={items} />
    </section>
  );
}
