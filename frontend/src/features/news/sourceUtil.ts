import type { ManagedSource } from "../../api/news";

/** 設定のソースの識別子（backend/app/news/sourcemgr.key_of と同じ） */
export const keyOf = (kind: string, value: string): string =>
  kind === "rss" || kind === "page" ? `${kind}:${value}` : kind === "hackernews" ? "hn" : kind === "huggingface" ? "hf" : `${kind}:${value}`;

export const INTERVAL_CHOICES = [5, 15, 30, 60, 120, 180, 360, 720, 1440, 10080];
export const intervalLabel = (m: number): string =>
  m >= 10080 ? "1 週間" : m >= 1440 && m % 1440 === 0 ? `${m / 1440} 日` : m >= 60 && m % 60 === 0 ? `${m / 60} 時間` : `${m} 分`;

export type SourceState = "failing" | "stalled" | "disabled" | "ok" | "waiting";
const STALL_MS = 7 * 86_400_000;
/** 失敗中（最後の取得が失敗）・止まっている（有効だが 7 日以上成功していない／一度も成功していない）・無効・正常・未取得 */
export function stateOf(s: ManagedSource, now = Date.now()): SourceState {
  if (!s.enabled) return "disabled";
  if (!s.last_at) return "waiting";
  if (s.last_ok === false) return "failing";
  if (!s.last_ok_at || now - Date.parse(s.last_ok_at) > STALL_MS) return "stalled";
  return "ok";
}
export const STATE_LABEL: Record<SourceState, string> = { failing: "失敗中", stalled: "止まっている", disabled: "無効", ok: "正常", waiting: "未取得" };

/** 直近 7 日の成功率の小さな棒（高さ = 成功率。取っていない日は点線）。色だけに頼らず title に数値。 */
export function failRate(s: ManagedSource): number {
  const days = s.week.filter((v): v is number => v !== null);
  return days.length ? 100 - days.reduce((a, b) => a + b, 0) / days.length : 0;
}
