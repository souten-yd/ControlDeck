import { create } from "zustand";

/** 操作性の便利機能（設定 → 操作性）。端末ごとに覚える（携帯と PC で使い方が違うため）。
 *
 * 2026-11-20 利用者「左から右へフリックでメニューを出し、右から左で閉じたい。オンオフを設定に。
 * そういう便利機能が他にも思い浮かべば追加して」。 */
export interface UxPrefs {
  /** 携帯幅で、左から右へのフリックでメニューを開き、右から左で閉じる */
  swipeNav: boolean;
  /** 読み上げ・音声入力の間は画面を消さない（iPhone は画面が消えると読み上げが止まる） */
  keepAwake: boolean;
  /** 画面上部の題名を押すと、いま見ている一覧・本文の先頭へ戻る */
  tapTitleToTop: boolean;
}

export const DEFAULT_UX_PREFS: UxPrefs = { swipeNav: true, keepAwake: true, tapTitleToTop: true };
const STORAGE_KEY = "cd-ux-prefs-v1";

function load(): UxPrefs {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    const value = { ...DEFAULT_UX_PREFS };
    for (const key of Object.keys(DEFAULT_UX_PREFS) as (keyof UxPrefs)[]) {
      if (typeof raw?.[key] === "boolean") value[key] = raw[key];
    }
    return value;
  } catch {
    return { ...DEFAULT_UX_PREFS };
  }
}

interface UxPrefsState extends UxPrefs {
  set: (patch: Partial<UxPrefs>) => void;
}

export const useUxPrefs = create<UxPrefsState>((set, get) => ({
  ...load(),
  set: (patch) => {
    const next: UxPrefs = { swipeNav: get().swipeNav, keepAwake: get().keepAwake, tapTitleToTop: get().tapTitleToTop, ...patch };
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(next)); } catch { /* この場では効く */ }
    set(next);
  },
}));
