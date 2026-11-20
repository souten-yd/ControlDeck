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
/** 回線の状態。初回接続と再接続を分けるのは表示のため。
 *  まだ一度も繋がっていないのに「再接続中」と出すと、壊れているように見える。 */
export type MetricsStatus = "connecting" | "live" | "reconnecting";

interface MetricsState {
  latest: MetricsSnapshot | null;
  status: MetricsStatus;
  history: MetricsSnapshot[]; // 直近スパークライン用（最大 120 点）
  /** 受け取りが途切れた（画面を閉じていた・裏にあった）回数。変わったらサーバーの履歴を読み直す */
  gapEpoch: number;
  push: (s: MetricsSnapshot) => void;
  setStatus: (s: MetricsStatus) => void;
}

/** 前の点からこれ以上空いたら「途切れた」とみなす（送られてくる間隔は 2 秒） */
export const METRICS_GAP_MS = 15_000;

export const useMetrics = create<MetricsState>((set) => ({
  latest: null,
  status: "connecting",
  history: [],
  gapEpoch: 0,
  // 2026-11-20 利用者「ブラウザを閉じてまた開くと、閉じていた間のグラフが更新されない」。
  // 画面を閉じている間は受け取らない（電池のため）ので、戻ると手元の点の間が空いたまま詰めて描いていた。
  // 途切れたら手元の点を捨てて数え直し、閉じていた間はサーバーの履歴（Dashboard が gapEpoch で読み直す）で埋める。
  push: (s) =>
    set((st) => {
      const previous = st.history[st.history.length - 1];
      const gap = previous && Date.parse(s.timestamp) - Date.parse(previous.timestamp) > METRICS_GAP_MS;
      return gap
        ? { latest: s, history: [s], gapEpoch: st.gapEpoch + 1 }
        : { latest: s, history: [...st.history.slice(-119), s] };
    }),
  setStatus: (status) => set({ status }),
}));

// ---- トースト ----
export interface Toast {
  id: number;
  message: string;
  kind: "success" | "error" | "info";
  /** 「元に戻す」など。押すと実行して通知を閉じる */
  action?: { label: string; onClick: () => void };
}

interface ToastState {
  toasts: Toast[];
  show: (message: string, kind?: Toast["kind"], action?: Toast["action"], ms?: number) => void;
  dismiss: (id: number) => void;
}

let toastId = 0;
export const useToasts = create<ToastState>((set) => ({
  toasts: [],
  show: (message, kind = "success", action, ms) => {
    const id = ++toastId;
    set((st) => ({ toasts: [...st.toasts.slice(-2), { id, message, kind, action }] }));
    setTimeout(
      () => set((st) => ({ toasts: st.toasts.filter((t) => t.id !== id) })),
      ms ?? (action ? 6000 : 4000),
    );
  },
  dismiss: (id) =>
    set((st) => ({ toasts: st.toasts.filter((t) => t.id !== id) })),
}));
