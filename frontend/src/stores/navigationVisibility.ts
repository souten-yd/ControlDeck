import { create } from "zustand";

/* メニューから隠す項目の鍵（NavEntry.key）の集合。端末ごと（「よく使う」と同じ）。
   空＝全部表示なので、新しく入った機能・Add-on・プラグインは初めから出る。 */

const STORAGE_KEY = "cd-navigation-hidden-v1";
/** 隠すと設定へ戻れなくなる項目。 */
export const NAV_ALWAYS_VISIBLE = ["/settings"];

function normalize(keys: unknown): string[] {
  if (!Array.isArray(keys)) return [];
  const strings = keys.filter((key): key is string => typeof key === "string" && !NAV_ALWAYS_VISIBLE.includes(key));
  return [...new Set(strings)];
}

function load(): string[] {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return value === null ? [] : normalize(JSON.parse(value));
  } catch {
    return [];
  }
}

function save(keys: string[]) {
  try {
    if (keys.length) localStorage.setItem(STORAGE_KEY, JSON.stringify(keys));
    else localStorage.removeItem(STORAGE_KEY);
  } catch { /* 保存できなくても、この場の選択は効く */ }
}

interface NavigationVisibilityState {
  hidden: string[];
  setHidden: (key: string, hidden: boolean) => void;
  reset: () => void;
}

export const useNavigationVisibility = create<NavigationVisibilityState>((set, get) => ({
  hidden: load(),
  setHidden: (key, hide) => {
    const rest = get().hidden.filter((item) => item !== key);
    const next = normalize(hide ? [...rest, key] : rest);
    save(next);
    set({ hidden: next });
  },
  reset: () => {
    save([]);
    set({ hidden: [] });
  },
}));
