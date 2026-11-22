// 取り込んだ PR のマージ（管理者だけ）。押すと前提の確認の結果（関門・区分・マージできるか）を出し、確認 → マージ → 結果。
// L2（認証・権限・秘密・外部送信・移行・更新と隔離の仕組み）は、強い警告と確認の文言の入力が要る。Release はしない。
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../../api/client";
import { ConfirmDialog } from "../../components/ui";
import { useToasts } from "../../stores";

export type MergePreview = {
  ready: boolean; problems: string[]; tier: "L0" | "L1" | "L2" | null; phrase: string; file_count: number;
  guarded: { path: string; reasons: string[] }[];
  check: { passed: boolean; at: number | null };
  pr: { number: number; url: string; state: string; mergeable: string; head: string };
  merged: { at: number; by: string; pr_url: string; tier: string } | null;
};

export const L2_PHRASE = "L2 を理解した";
const TIER_TEXT: Record<string, string> = {
  L0: "L0（文書・テスト・CodeDEV のアプリ）", L1: "L1（通常の変更。関門を通れば自動で進められます）", L2: "L2（利用者の承認が要る変更）",
};

export function WorkspaceMergeButton({ name, className, merged = false }: { name: string; className: string; merged?: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      {/* マージ済みでも、開いている間は結果を見せるため dialog は残す */}
      {!merged && <button type="button" data-testid="workspace-merge-open" onClick={() => setOpen(true)} className={className}>マージ</button>}
      {open && <WorkspaceMergeDialog name={name} onClose={() => setOpen(false)} />}
    </>
  );
}

function WorkspaceMergeDialog({ name, onClose }: { name: string; onClose: () => void }) {
  const base = `/opencode/workspaces/${encodeURIComponent(name)}/merge`;
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const [phrase, setPhrase] = useState("");
  const [working, setWorking] = useState(false);
  const [done, setDone] = useState(false);
  const [failure, setFailure] = useState("");
  const { data, error, isFetching, refetch } = useQuery({
    queryKey: ["workspace-merge", name], queryFn: () => api<MergePreview>(base), staleTime: 0, gcTime: 0, retry: false, refetchOnWindowFocus: false,
  });
  const l2 = data?.tier === "L2";
  const phraseOk = !l2 || phrase.trim() === (data?.phrase || L2_PHRASE);
  const ready = Boolean(data?.ready) && phraseOk && !done;

  const merge = async () => {
    if (working) return;
    setWorking(true);
    setFailure("");
    try {
      await api(base, { method: "POST", json: { confirm: true, l2_phrase: l2 ? phrase.trim() : "" } });
      setDone(true);
      show("マージしました");
      await qc.invalidateQueries({ queryKey: ["workspace-changes", name] });
    } catch (e) {
      setFailure(e instanceof ApiError || e instanceof Error ? e.message : "マージできませんでした");
      await refetch();
    } finally {
      setWorking(false);
    }
  };

  const loadError = error instanceof Error ? error.message : "";
  return (
    <ConfirmDialog title="マージ" danger={l2} confirmLabel="マージする" busy={working}
      message={done ? "" : `PR #${data?.pr.number ?? ""} を squash でマージし、ブランチを消します（Release はしません）。`}
      disabled={!ready} onConfirm={() => void merge()} onClose={onClose}>
      <div data-testid="workspace-merge" className="mt-3 space-y-2 text-xs text-zinc-700 dark:text-zinc-300">
        {isFetching && !data && <p>前提を確かめています…</p>}
        {loadError && <p data-testid="workspace-merge-error" className="text-red-700 dark:text-red-400">{loadError}</p>}
        {data && (
          <ul className="space-y-1">
            <li>PR: <span className="break-all">#{data.pr.number}（{data.pr.state === "OPEN" ? "開いています" : data.pr.state}・{data.pr.mergeable === "MERGEABLE" ? "マージできます" : data.pr.mergeable}）</span></li>
            <li className={data.check.passed ? "text-emerald-700 dark:text-emerald-400" : "text-red-700 dark:text-red-400"}>関門: {data.check.passed ? "合格" : "不合格・未実施"}</li>
            <li data-testid="workspace-merge-tier">区分: {data.tier ? TIER_TEXT[data.tier] : "決められませんでした"}（{data.file_count} ファイル）</li>
          </ul>
        )}
        {data && data.problems.length > 0 && (
          <ul data-testid="workspace-merge-problems" className="list-disc space-y-0.5 pl-4 text-red-700 dark:text-red-400">
            {data.problems.map((p) => <li key={p}>{p}</li>)}
          </ul>
        )}
        {l2 && !done && (
          <div data-testid="workspace-merge-l2" className="space-y-2 rounded-lg border-2 border-red-500 bg-red-50 p-2 dark:bg-red-950/40">
            <p className="font-semibold text-red-800 dark:text-red-300">この変更は L2（認証・権限・秘密・外部送信・移行・更新と隔離の仕組み）に当たります。</p>
            <ul className="list-disc pl-4 font-mono text-[11px] text-red-900 dark:text-red-200">
              {data!.guarded.slice(0, 8).map((g) => <li key={g.path} className="break-all">{g.path}</li>)}
              {data!.guarded.length > 8 && <li>ほか {data!.guarded.length - 8} 件</li>}
            </ul>
            <label className="block text-red-900 dark:text-red-200">
              内容を確かめた上でマージするなら「{data!.phrase || L2_PHRASE}」と入力
              <input value={phrase} onChange={(e) => setPhrase(e.target.value)} data-testid="workspace-merge-phrase"
                className="mt-1 min-h-11 w-full rounded-lg border border-red-400 bg-white px-2 text-sm text-zinc-900 dark:bg-zinc-900 dark:text-zinc-100" />
            </label>
          </div>
        )}
        {failure && <p data-testid="workspace-merge-failure" className="text-red-700 dark:text-red-400">{failure}</p>}
        {done && (
          <p data-testid="workspace-merge-done" className="text-emerald-700 dark:text-emerald-400">
            マージしました。次の Release で本番に入ります。今すぐ出すなら Release を作ってください（このマージでは本番は変わりません）。
          </p>
        )}
      </div>
    </ConfirmDialog>
  );
}
