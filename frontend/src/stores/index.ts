import { create } from "zustand";
import type { MetricsSnapshot, UserInfo } from "../types";

// ---- 認証 ----
interface AuthState {
  user: UserInfo | null;
  setUser: (u: UserInfo | null) => void;
  can: (perm: string) => boolean;
}

export const useAuth = create<AuthState>((set, get) => ({
  user: null,
  setUser: (user) => set({ user }),
  can: (perm) => get().user?.permissions.includes(perm) ?? false,
}));

// ---- テーマ ----
export type Theme = "system" | "dark" | "light";

function applyTheme(theme: Theme) {
  const dark =
    theme === "dark" ||
    (theme === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
}

interface ThemeState {
  theme: Theme;
  setTheme: (t: Theme) => void;
}

export const useTheme = create<ThemeState>((set) => ({
  theme: (localStorage.getItem("cd-theme") as Theme) || "system",
  setTheme: (theme) => {
    localStorage.setItem("cd-theme", theme);
    applyTheme(theme);
    set({ theme });
  },
}));

applyTheme(useTheme.getState().theme);
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
  applyTheme(useTheme.getState().theme);
});

// ---- メトリクス（WebSocket から更新、セレクター経由で購読） ----
interface MetricsState {
  latest: MetricsSnapshot | null;
  connected: boolean;
  history: MetricsSnapshot[]; // 直近スパークライン用（最大 120 点）
  push: (s: MetricsSnapshot) => void;
  setConnected: (c: boolean) => void;
}

export const useMetrics = create<MetricsState>((set) => ({
  latest: null,
  connected: false,
  history: [],
  push: (s) =>
    set((st) => ({
      latest: s,
      history: [...st.history.slice(-119), s],
    })),
  setConnected: (connected) => set({ connected }),
}));

// ---- トースト ----
export interface Toast {
  id: number;
  message: string;
  kind: "success" | "error" | "info";
}

interface ToastState {
  toasts: Toast[];
  show: (message: string, kind?: Toast["kind"]) => void;
  dismiss: (id: number) => void;
}

let toastId = 0;
export const useToasts = create<ToastState>((set) => ({
  toasts: [],
  show: (message, kind = "success") => {
    const id = ++toastId;
    set((st) => ({ toasts: [...st.toasts.slice(-2), { id, message, kind }] }));
    setTimeout(
      () => set((st) => ({ toasts: st.toasts.filter((t) => t.id !== id) })),
      4000,
    );
  },
  dismiss: (id) =>
    set((st) => ({ toasts: st.toasts.filter((t) => t.id !== id) })),
}));
