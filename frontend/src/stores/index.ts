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
export type Accent = "blue" | "violet" | "emerald" | "teal" | "orange" | "rose";

export const ACCENTS: { id: Accent; label: string; color: string }[] = [
  { id: "blue", label: "ブルー", color: "#3b82f6" },
  { id: "violet", label: "バイオレット", color: "#8b5cf6" },
  { id: "emerald", label: "エメラルド", color: "#10b981" },
  { id: "teal", label: "ティール", color: "#14b8a6" },
  { id: "orange", label: "オレンジ", color: "#f97316" },
  { id: "rose", label: "ローズ", color: "#f43f5e" },
];

function applyTheme(theme: Theme, accent: Accent, oled: boolean) {
  const el = document.documentElement;
  const dark =
    theme === "dark" ||
    (theme === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  el.classList.toggle("dark", dark);
  if (accent === "blue") delete el.dataset.accent;
  else el.dataset.accent = accent;
  if (oled) el.dataset.oled = "1";
  else delete el.dataset.oled;
  // ブラウザ UI（アドレスバー等）の色も追従させる
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute("content", dark ? (oled ? "#000000" : "#09090b") : "#fafafa");
}

interface ThemeState {
  theme: Theme;
  accent: Accent;
  oled: boolean;
  setTheme: (t: Theme) => void;
  setAccent: (a: Accent) => void;
  setOled: (v: boolean) => void;
}

export const useTheme = create<ThemeState>((set, get) => ({
  theme: (localStorage.getItem("cd-theme") as Theme) || "system",
  accent: (localStorage.getItem("cd-accent") as Accent) || "blue",
  oled: localStorage.getItem("cd-oled") === "1",
  setTheme: (theme) => {
    localStorage.setItem("cd-theme", theme);
    set({ theme });
    const s = get();
    applyTheme(s.theme, s.accent, s.oled);
  },
  setAccent: (accent) => {
    localStorage.setItem("cd-accent", accent);
    set({ accent });
    const s = get();
    applyTheme(s.theme, s.accent, s.oled);
  },
  setOled: (oled) => {
    localStorage.setItem("cd-oled", oled ? "1" : "0");
    set({ oled });
    const s = get();
    applyTheme(s.theme, s.accent, s.oled);
  },
}));

{
  const s = useTheme.getState();
  applyTheme(s.theme, s.accent, s.oled);
}
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
  const s = useTheme.getState();
  applyTheme(s.theme, s.accent, s.oled);
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
