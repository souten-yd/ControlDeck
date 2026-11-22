// 自動保存の時点へ戻す（Claude Code の /rewind と同じ考え: コードだけ / 会話だけ / 両方）。
// 会話の発言の「この時点に戻す」と、作業場の「保存の時点」の一覧が使う。
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { ConfirmDialog } from "../../components/ui";
import { useToasts } from "../../stores";

export type RestoreChoice = "code" | "chat" | "both";

const CHOICES: { id: RestoreChoice; label: string; hint: string }[] = [
  { id: "code", label: "コードだけ戻す", hint: "作業場のファイルをこの時点の状態に戻します。会話はそのまま続きます。" },
  { id: "chat", label: "会話だけ戻す（ここから分岐）", hint: "ここまでの会話を新しい会話に写して続けます。ファイルは変わりません。" },
  { id: "both", label: "両方戻す", hint: "ファイルをこの時点に戻し、ここから分岐した会話で続けます。" },
];

/** 発言の「この時点に戻す」で開く。選んだ中身の実行は呼び出し側（onConfirm）が行う。 */
export function RestoreDialog({ busy, onConfirm, onClose }: {
  busy: boolean; onConfirm: (choice: RestoreChoice) => void; onClose: () => void;
}) {
  const [choice, setChoice] = useState<RestoreChoice>("code");
  return (
    <ConfirmDialog title="この時点に戻す" danger={false} confirmLabel="戻す" busy={busy}
      message="この回の終わりの状態へ戻します。戻し方を選んでください。" onConfirm={() => onConfirm(choice)} onClose={onClose}>
      <div role="radiogroup" aria-label="戻し方" className="mt-3 space-y-1.5">
        {CHOICES.map((c) => (
          <label key={c.id} className={`flex min-h-11 cursor-pointer items-start gap-2 rounded-lg border px-3 py-2 text-sm ${choice === c.id ? "border-accent-500 bg-accent-50 dark:bg-accent-950/30" : "border-zinc-200 dark:border-zinc-700"}`}>
            <input type="radio" name="restore-choice" checked={choice === c.id} onChange={() => setChoice(c.id)} className="mt-1" />
            <span className="min-w-0">
              <span className="block font-medium">{c.label}</span>
              <span className="block text-xs text-zinc-500 dark:text-zinc-400">{c.hint}</span>
            </span>
          </label>
        ))}
      </div>
      {choice !== "chat" && (
        <p className="mt-2 text-xs text-zinc-500 dark:text-zinc-400">戻す前の状態も保存されるので、あとで戻せます。</p>
      )}
    </ConfirmDialog>
  );
}

/** コードを保存の時点へ戻す（作業場の API）。失敗は例外で返る。 */
export function restoreCode(workspace: string, sha: string) {
  return api(`/opencode/workspaces/${encodeURIComponent(workspace)}/restore`, { method: "POST", json: { sha } });
}

type Checkpoint = { sha: string; at: string; subject: string; count: number; conversation_id: string; kind: "save" | "before_restore" };

function when(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString("ja-JP", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** 作業場の「保存の時点」の一覧。初めは 5 件、「もっと見る」で全部。 */
export function WorkspaceCheckpoints({ name, busy, onRestored }: { name: string; busy: boolean; onRestored: () => void }) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const [all, setAll] = useState(false);
  const [target, setTarget] = useState<Checkpoint | null>(null);
  const [working, setWorking] = useState(false);
  const key = ["workspace-checkpoints", name];
  const { data } = useQuery({
    queryKey: key, staleTime: 15_000,
    queryFn: async () => (await api<{ checkpoints: Checkpoint[] }>(`/opencode/workspaces/${encodeURIComponent(name)}/checkpoints`)).checkpoints ?? [],
  });
  if (!data || data.length === 0) return null;
  const rows = all ? data : data.slice(0, 5);
  const restore = async () => {
    if (!target) return;
    setWorking(true);
    try {
      await restoreCode(name, target.sha);
      show("コードを戻しました");
      await qc.invalidateQueries({ queryKey: key });
      onRestored();
    } catch (e) {
      show(e instanceof Error ? e.message : "戻せませんでした", "error");
    } finally {
      setWorking(false);
      setTarget(null);
    }
  };
  return (
    <div data-testid="workspace-checkpoints" className="min-w-0">
      <p className="text-zinc-700 dark:text-zinc-200">保存の時点</p>
      <ul className="mt-1 space-y-1">
        {rows.map((c, i) => (
          <li key={`${c.kind}-${c.sha}-${i}`} className="flex min-w-0 items-center gap-2">
            <span className="min-w-0 flex-1 truncate text-zinc-600 dark:text-zinc-300" title={c.subject}>
              {when(c.at)} {c.kind === "before_restore" ? "戻す前の状態" : c.count ? `保存 #${c.count}` : c.subject}
            </span>
            <button type="button" disabled={busy || (i === 0 && c.kind === "save")} onClick={() => setTarget(c)}
              className="min-h-11 shrink-0 rounded-lg border border-zinc-300 px-2 text-xs text-zinc-700 hover:bg-zinc-100 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800">
              {c.kind === "before_restore" ? "戻す前に戻す" : "この時点に戻す"}
            </button>
          </li>
        ))}
      </ul>
      {data.length > 5 && (
        <button type="button" onClick={() => setAll((v) => !v)} className="min-h-11 text-zinc-600 underline-offset-2 hover:underline dark:text-zinc-300">
          {all ? "閉じる" : `もっと見る（あと ${data.length - 5} 件）`}
        </button>
      )}
      {target && (
        <ConfirmDialog title={target.kind === "before_restore" ? "戻す前に戻す" : "この時点に戻す"} danger={false} confirmLabel="戻す" busy={working}
          message="作業場のコードをこの時点の状態に戻します。会話は変わりません。戻す前の状態も保存されるので、あとで戻せます。"
          onConfirm={() => void restore()} onClose={() => setTarget(null)} />
      )}
    </div>
  );
}
