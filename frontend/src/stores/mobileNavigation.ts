import { create } from "zustand";

/* メニューの先頭に固定する「よく使う」機能。以前は携帯の下部ナビの並びだった。
   利用者の選択を失わないよう、保存先の鍵と形は変えていない。 */

export const MOBILE_NAV_MAX_ITEMS = 6;
export const DEFAULT_MOBILE_NAV = ["/", "/apps", "/workflows", "/terminal", "/assistant"];
const STORAGE_KEY = "cd-mobile-navigation-v1";

function normalize(paths: unknown): string[] {
  if (!Array.isArray(paths)) return DEFAULT_MOBILE_NAV;
  const migrated = paths
    .filter((path): path is string => typeof path === "string")
    .map((path) => path === "/runner" ? "/workflows" : path);
  return [...new Set(migrated)].slice(0, MOBILE_NAV_MAX_ITEMS);
}

function load(): string[] {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    if (value === null) return DEFAULT_MOBILE_NAV;
    const next = normalize(JSON.parse(value));
    if (JSON.stringify(next) !== value) localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    return next;
  } catch {
    return DEFAULT_MOBILE_NAV;
  }
}

interface MobileNavigationState {
  paths: string[];
  setPaths: (paths: string[]) => void;
  reset: () => void;
}

export const useMobileNavigation = create<MobileNavigationState>((set) => ({
  paths: load(),
  setPaths: (paths) => {
    const next = normalize(paths);
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } catch { /* 保存できなくても、この場の選択は効く */ }
    set({ paths: next });
  },
  reset: () => {
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch { /* 同上 */ }
    set({ paths: DEFAULT_MOBILE_NAV });
  },
}));
