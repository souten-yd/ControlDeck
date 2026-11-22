/** モデルの載せ替えの待ち（調停役 /models/swap-queue）を 1 行で出す。
 *
 * 別のモデルの要求が、載っているモデルの実行中が終わるのを待っているときだけ出る。待っている要求は断られない。
 * 320px で切れないよう 1 行を truncate し、全文は title に置く。 */
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";

export interface SwapQueue {
  loaded: string[];
  running: Record<string, number>;
  swapping: string | null;
  queue: Array<{ alias: string; waiting: number; oldest_wait_seconds: number; needs_swap: boolean }>;
  next: { alias: string; reason: string } | null;
}

/** 表示する文。待ちが無ければ null。 */
export function swapQueueText(q: SwapQueue | undefined): string | null {
  if (!q) return null;
  const rows = q.queue.filter((r) => r.needs_swap && r.waiting > 0);
  if (rows.length === 0 && !q.swapping) return null;
  const parts = rows.map((r) => `${r.alias} ${r.waiting} 件（最長 ${Math.round(r.oldest_wait_seconds)} 秒）`);
  if (q.swapping) parts.unshift(`${q.swapping} を載せています`);
  return `切り替え待ち: ${parts.join("、")}`;
}

export function SwapQueueNotice() {
  const { data } = useQuery({
    queryKey: ["swap-queue"], queryFn: () => api<SwapQueue>("/models/swap-queue"),
    refetchInterval: 3_000, refetchIntervalInBackground: false,
  });
  const text = swapQueueText(data);
  if (!text) return null;
  return (
    <p className="mb-2 truncate rounded-lg bg-amber-50 px-2 py-1 text-[11px] text-amber-800 dark:bg-amber-950/40 dark:text-amber-200"
      title={data?.next?.reason ? `${text}\n次: ${data.next.alias}（${data.next.reason}）` : text}
      data-testid="swap-queue-notice">
      {text}
    </p>
  );
}
