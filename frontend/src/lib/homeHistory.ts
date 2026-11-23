/** Home のグラフが読む履歴の長さ（分）。設定 → 表示で変え、このブラウザに保存する。
 *
 * 2026-11-23 利用者「履歴 15分・生データ・点数の 1 行はもう不要。何分のデータを取得するかは設定へ。今まで 15 分から変えたことない」。
 * 以前は Home の画面の中だけの状態（保存なし・毎回 15 分）だった。保存は端末ごとの好みなので localStorage に置く
 * （読み書きが失敗しても 15 分で動く）。 */
export const HISTORY_RANGES = [
  [15, "15分"],
  [60, "1時間"],
  [360, "6時間"],
  [1440, "24時間"],
  [10080, "7日"],
  [43200, "30日"],
  [129600, "90日"],
  [525600, "1年"],
] as const;

export const HISTORY_MIN = 15;
export const HISTORY_MAX = 525600;
const KEY = "control-deck-home-history-minutes";

export function clampHistoryMinutes(value: number): number {
  if (!Number.isFinite(value)) return HISTORY_MIN;
  return Math.min(HISTORY_MAX, Math.max(HISTORY_MIN, Math.round(value)));
}

export function readHistoryMinutes(): number {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? clampHistoryMinutes(Number(raw)) : HISTORY_MIN;
  } catch {
    return HISTORY_MIN;
  }
}

export function writeHistoryMinutes(minutes: number): number {
  const value = clampHistoryMinutes(minutes);
  try {
    localStorage.setItem(KEY, String(value));
  } catch {
    /* 保存できなくても、この回の画面では効く */
  }
  return value;
}
