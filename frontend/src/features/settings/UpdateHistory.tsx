/** ControlDeck 本体の更新の履歴と、自動で更新した後の一度だけの知らせ。
 *
 * 2026-11-20 利用者「自動更新が動いていない気がする。あるのかな？気づいてないだけ？」。
 * 実際は 11/17 以降で自動 38 回・手動 19 回動いていた。通知先が登録されておらず、画面にも前回の更新が
 * 出ないので気づけなかった。そこで:
 * - 設定 → 更新とバックアップに履歴（いつ・どの版・自動か手動か・成否）を出す（UpdateHistoryList）
 * - 自動で更新した後、次に画面を開いたら一度だけ知らせる（AutoUpdateNotice、AppLayout に置く）。
 *   見たかどうかはこのブラウザに覚える（通知先が無くても気づける） */
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../../api/client";
import { useAuth, useToasts } from "../../stores";

export interface UpdateHistoryItem {
  phase: "succeeded" | "failed";
  automatic: boolean;
  release_tag?: string | null;
  old_sha?: string | null;
  new_sha?: string | null;
  target_sha?: string | null;
  error?: string | null;
  finished_at: number;
}

export const updateHistoryKey = ["control-deck-update-history"];

export function useUpdateHistory(enabled = true) {
  return useQuery({
    queryKey: updateHistoryKey,
    queryFn: () => api<{ items: UpdateHistoryItem[] }>("/system/control-deck-update/history"),
    enabled,
    retry: false,
    refetchOnWindowFocus: true,
  });
}

const version = (item: UpdateHistoryItem) =>
  item.release_tag?.replace(/^ControlDeck_/, "") || (item.new_sha || item.target_sha || "").slice(0, 12) || "不明";

const when = (seconds: number) => new Date(seconds * 1000).toLocaleString("ja-JP", {
  month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit",
});

export function UpdateHistoryList() {
  const { data } = useUpdateHistory();
  const [all, setAll] = useState(false);
  const items = data?.items ?? [];
  if (!data) return null;
  const shown = all ? items : items.slice(0, 5);
  return (
    <div className="mt-3" data-update-history>
      <h3 className="text-xs font-semibold text-zinc-600 dark:text-zinc-300">更新の履歴</h3>
      {items.length === 0 ? (
        <p className="mt-1 text-xs text-zinc-500">まだ記録がありません（この版から残します）</p>
      ) : (
        <ul className="mt-1 divide-y divide-zinc-100 text-xs dark:divide-zinc-800">
          {shown.map((item, index) => (
            <li key={`${item.finished_at}-${index}`} className="flex flex-wrap items-baseline gap-x-2 py-1.5">
              <span className="num text-zinc-500">{when(item.finished_at)}</span>
              <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${item.automatic
                ? "bg-accent-50 text-accent-700 dark:bg-accent-600/15 dark:text-accent-300"
                : "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300"}`}>
                {item.automatic ? "自動" : "手動"}
              </span>
              <span className="font-medium">{version(item)}</span>
              {item.phase === "succeeded"
                ? <span className="text-emerald-600 dark:text-emerald-400">成功</span>
                : <span className="min-w-0 break-words text-red-600 dark:text-red-400">失敗{item.error ? `: ${item.error}` : ""}（前の版で動いています）</span>}
            </li>
          ))}
        </ul>
      )}
      {items.length > 5 && (
        <button type="button" onClick={() => setAll(!all)} className="mt-1 min-h-9 text-xs text-accent-600 dark:text-accent-400">
          {all ? "最近の 5 件だけ" : `すべて表示（${items.length} 件）`}
        </button>
      )}
    </div>
  );
}

const SEEN_KEY = "control-deck-auto-update-seen";
/** これより古い自動更新は、初めて開いたブラウザでは知らせない */
const NOTICE_WINDOW_S = 3 * 86400;

function readSeen(): number | null {
  try {
    const value = localStorage.getItem(SEEN_KEY);
    return value === null ? null : Number(value) || 0;
  } catch {
    return null;
  }
}

function writeSeen(value: number) {
  try { localStorage.setItem(SEEN_KEY, String(value)); } catch { /* 覚えられない環境: 毎回は出さない */ }
}

/** 自動で更新した後、次に画面を開いたら一度だけ知らせる（管理者だけ） */
export function AutoUpdateNotice() {
  const canManage = useAuth((s) => s.can("settings.manage"));
  const show = useToasts((s) => s.show);
  const navigate = useNavigate();
  const { data } = useUpdateHistory(canManage);
  useEffect(() => {
    const items = data?.items ?? [];
    if (!items.length) return;
    const latest = Math.max(...items.map((item) => item.finished_at || 0));
    const seen = readSeen() ?? Date.now() / 1000 - NOTICE_WINDOW_S;
    const fresh = items.filter((item) => item.automatic && item.finished_at > seen);
    writeSeen(Math.max(latest, seen));
    if (!fresh.length) return;
    const last = fresh[0];
    const message = last.phase === "succeeded"
      ? `ControlDeck を ${version(last)} に自動で更新しました（${when(last.finished_at)}）`
      : `ControlDeck の自動更新に失敗しました（${version(last)}）。前の版で動いています`;
    show(message, last.phase === "succeeded" ? "info" : "error",
      { label: "履歴", onClick: () => navigate("/settings/system") }, 10_000);
  }, [data, show, navigate]);
  return null;
}
