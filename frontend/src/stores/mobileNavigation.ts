import { create } from "zustand";

export const MOBILE_NAV_MAX_ITEMS = 6;
export const DEFAULT_MOBILE_NAV = ["/", "/apps", "/runner", "/terminal", "/assistant"];
const STORAGE_KEY = "cd-mobile-navigation-v1";

function normalize(paths: unknown): string[] {
  if (!Array.isArray(paths)) return DEFAULT_MOBILE_NAV;
  return [...new Set(paths.filter((path): path is string => typeof path === "string"))].slice(0, MOBILE_NAV_MAX_ITEMS);
}

function load(): string[] {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return value === null ? DEFAULT_MOBILE_NAV : normalize(JSON.parse(value));
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
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    set({ paths: next });
  },
  reset: () => {
    localStorage.removeItem(STORAGE_KEY);
    set({ paths: DEFAULT_MOBILE_NAV });
  },
}));
