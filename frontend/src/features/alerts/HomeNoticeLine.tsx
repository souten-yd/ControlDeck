/** Home の一番上の 1 行: 利用者が知っておくべきことのうち、未解決で最も重要な 1 件。
 *
 * 2026-11-23 利用者「ホーム画面に最終的な通知の内容とか、ユーザー側で把握しておかないといけない情報を一行で。
 * 解決されたら消して次に優先度の高い内容を。重要度によって インフォメーション・アテンション・ワーニング・エラーの
 * 題名をつけて、色も変えて」。元は backend の /home-notices（警報・失敗したアプリ・更新の失敗・承認待ち）。
 * 色だけに頼らない: 札の文字（エラー・警告・注意・情報）と形の違うアイコンを必ず付ける。何も無ければ行ごと出さない。 */
import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { Marquee } from "../../components/Marquee";

export type NoticeLevel = "error" | "warning" | "attention" | "info";
export interface HomeNotice { key: string; level: NoticeLevel; title: string; message: string; to: string; at: number }

const LEVELS: Record<NoticeLevel, { label: string; badge: string; row: string }> = {
  error: {
    label: "エラー",
    badge: "bg-red-600 text-white dark:bg-red-500",
    row: "border-red-300 bg-red-50 text-red-900 dark:border-red-800 dark:bg-red-950/40 dark:text-red-100",
  },
  warning: {
    label: "警告",
    badge: "bg-orange-600 text-white dark:bg-orange-500 dark:text-zinc-950",
    row: "border-orange-300 bg-orange-50 text-orange-950 dark:border-orange-800 dark:bg-orange-950/40 dark:text-orange-100",
  },
  attention: {
    label: "注意",
    badge: "bg-yellow-400 text-zinc-900 dark:bg-yellow-400",
    row: "border-yellow-300 bg-yellow-50 text-yellow-950 dark:border-yellow-800 dark:bg-yellow-950/30 dark:text-yellow-100",
  },
  info: {
    label: "情報",
    badge: "bg-sky-600 text-white dark:bg-sky-500 dark:text-zinc-950",
    row: "border-sky-300 bg-sky-50 text-sky-950 dark:border-sky-800 dark:bg-sky-950/30 dark:text-sky-100",
  },
};

/** 札ごとに形の違うアイコン（エラー=×の丸 / 警告=三角 / 注意=!の丸 / 情報=iの丸）。 */
function LevelIcon({ level }: { level: NoticeLevel }) {
  const common = { viewBox: "0 0 24 24", width: 12, height: 12, fill: "none", stroke: "currentColor", strokeWidth: 2.4, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };
  if (level === "error") return <svg {...common}><circle cx="12" cy="12" r="9" /><path d="m9 9 6 6M15 9l-6 6" /></svg>;
  if (level === "warning") return <svg {...common}><path d="M12 3 2.5 20h19z" /><path d="M12 10v5M12 17.5v.01" /></svg>;
  if (level === "attention") return <svg {...common}><circle cx="12" cy="12" r="9" /><path d="M12 7.5v6M12 16.5v.01" /></svg>;
  return <svg {...common}><circle cx="12" cy="12" r="9" /><path d="M12 11v6M12 7.5v.01" /></svg>;
}

export function HomeNoticeLine() {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["home-notices"],
    queryFn: () => api<{ items: HomeNotice[]; total: number }>("/home-notices"),
    refetchInterval: 15_000,
    refetchOnWindowFocus: true,
  });
  const dismiss = useMutation({
    mutationFn: (key: string) => api("/home-notices/dismiss", { method: "POST", json: { key } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["home-notices"] }),
  });
  const [key, setKey] = useState<string>();
  const list = data?.items ?? [];
  const idx = Math.max(0, list.findIndex((n) => n.key === key));
  const top = list[idx];
  if (!top) return null;
  const style = LEVELS[top.level];
  const items = list.map((n) => ({
    key: n.key,
    node: <>
      <span className="font-medium">{n.title}</span>
      {n.message && n.message !== n.title && <span className="opacity-80"> — {n.message}</span>}
    </>,
  }));
  return (
    <div role={top.level === "error" ? "alert" : "status"} data-testid="home-notice" data-level={top.level}
      className={`flex min-w-0 items-center gap-1 rounded-xl border pl-2 text-xs ${style.row}`}>
      <Link to={top.to} className="flex min-h-9 min-w-0 flex-1 items-center gap-2 py-1 hover:opacity-80">
        <span className={`inline-flex shrink-0 items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-bold leading-none ${style.badge}`}>
          <LevelIcon level={top.level} />{style.label}
        </span>
        <Marquee items={items} activeKey={top.key} onChange={setKey} className="min-w-0 flex-1" />
        {list.length > 1 && <span data-testid="home-notice-pos" className="num shrink-0 text-[10px] opacity-75">{idx + 1}/{list.length}</span>}
      </Link>
      <button type="button" onClick={() => dismiss.mutate(top.key)} disabled={dismiss.isPending}
        aria-label="いま見えている件を閉じる（既読にする）" title="いま見えている件を閉じる（既読）"
        className="grid h-9 w-9 shrink-0 place-items-center rounded-lg opacity-70 hover:bg-black/5 hover:opacity-100 dark:hover:bg-white/10">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" aria-hidden><path d="M6 6l12 12M18 6 6 18" /></svg>
      </button>
    </div>
  );
}
