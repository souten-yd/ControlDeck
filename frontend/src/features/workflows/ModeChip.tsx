/** 機能の選択肢の形（＋メニュー: PlusMenu.tsx）。旧「機能の札」は 2026-11-18 に＋メニューへ統合した。 */
export interface ModeOption { id: string; icon: string; label: string; hint: string; tile?: string }

export const AUTO_MODE_OPTION: ModeOption = {
  id: "auto", icon: "✦", label: "自動判定", hint: "内容に合わせて機能を自動で選ぶ",
};
