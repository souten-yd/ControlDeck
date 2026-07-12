/** UI プリミティブ: BottomSheet / Drawer / ConfirmDialog / Menu / Toasts / StatusBadge / Sparkline */
import {
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { useToasts } from "../stores";
import type { AppStatus } from "../types";
import { IconX } from "./icons";

// ---- オーバーレイ共通（Esc / 背景クリックで閉じる + フォーカス移動）----
function Overlay({
  onClose,
  children,
  align,
}: {
  onClose: () => void;
  children: ReactNode;
  align: "bottom" | "right" | "center";
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    ref.current?.focus();
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [onClose]);
  const alignCls = {
    bottom: "items-end sm:items-center justify-center",
    right: "items-stretch justify-end",
    center: "items-center justify-center",
  }[align];
  return createPortal(
    <div
      className={`fixed inset-0 z-50 flex ${alignCls} bg-black/40 backdrop-blur-[2px]`}
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
      role="presentation"
    >
      <div ref={ref} tabIndex={-1} className="outline-none max-h-full flex">
        {children}
      </div>
    </div>,
    document.body,
  );
}

/** モバイル: 下からのシート / デスクトップ: 中央モーダル */
export function BottomSheet({
  title,
  onClose,
  children,
  wide,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}) {
  return (
    <Overlay onClose={onClose} align="bottom">
      <div
        role="dialog"
        aria-label={title}
        className={`flex w-screen flex-col rounded-t-2xl bg-white shadow-xl dark:bg-zinc-900 sm:rounded-2xl ${
          wide ? "sm:w-[640px]" : "sm:w-[480px]"
        } max-h-[85dvh] safe-bottom`}
      >
        <div className="flex items-center justify-between px-5 pt-4 pb-2">
          <div className="mx-auto h-1 w-9 rounded-full bg-zinc-300 dark:bg-zinc-700 sm:hidden absolute left-1/2 -translate-x-1/2 top-2" />
          <h2 className="text-base font-semibold">{title}</h2>
          <button
            onClick={onClose}
            aria-label="閉じる"
            className="rounded-lg p-2 text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"
          >
            <IconX />
          </button>
        </div>
        <div className="overflow-y-auto px-5 pb-6">{children}</div>
      </div>
    </Overlay>
  );
}

/** デスクトップ: 右ドロワー / モバイル: 全画面シート */
export function Drawer({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <Overlay onClose={onClose} align="right">
      <div
        role="dialog"
        aria-label={title}
        className="flex h-dvh w-screen flex-col bg-white shadow-xl dark:bg-zinc-900 sm:w-[520px]"
      >
        <div className="flex items-center justify-between border-b border-zinc-200 px-5 py-3 dark:border-zinc-800 safe-top">
          <h2 className="text-base font-semibold">{title}</h2>
          <button
            onClick={onClose}
            aria-label="閉じる"
            className="rounded-lg p-2 text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"
          >
            <IconX />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-5 py-4 safe-bottom">{children}</div>
      </div>
    </Overlay>
  );
}

/** 破壊的操作専用の確認ダイアログ */
export function ConfirmDialog({
  title,
  message,
  confirmLabel,
  danger = true,
  busy,
  onConfirm,
  onClose,
  children,
}: {
  title: string;
  message: string;
  confirmLabel: string;
  danger?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onClose: () => void;
  children?: ReactNode;
}) {
  return (
    <Overlay onClose={onClose} align="center">
      <div
        role="alertdialog"
        aria-label={title}
        className="mx-4 w-[min(420px,90vw)] rounded-2xl bg-white p-6 shadow-xl dark:bg-zinc-900"
      >
        <h2 className="text-base font-semibold">{title}</h2>
        <p className="mt-2 text-sm text-zinc-600 dark:text-zinc-400">{message}</p>
        {children}
        <div className="mt-5 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded-xl px-4 py-2 text-sm font-medium text-zinc-700 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800"
          >
            キャンセル
          </button>
          <button
            onClick={onConfirm}
            disabled={busy}
            className={`rounded-xl px-4 py-2 text-sm font-medium text-white disabled:opacity-50 ${
              danger ? "bg-red-600 hover:bg-red-700" : "bg-accent-600 hover:bg-accent-700"
            }`}
          >
            {busy ? "実行中..." : confirmLabel}
          </button>
        </div>
      </div>
    </Overlay>
  );
}

// ---- ドロップダウンメニュー ----
export interface MenuItem {
  label: string;
  danger?: boolean;
  onSelect: () => void;
}

export function DropdownMenu({
  items,
  trigger,
  ariaLabel,
}: {
  items: MenuItem[];
  trigger: ReactNode;
  ariaLabel: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  return (
    <div className="relative" ref={ref}>
      <button
        aria-label={ariaLabel}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        className="rounded-lg p-2 text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"
      >
        {trigger}
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 z-40 mt-1 w-44 overflow-hidden rounded-xl border border-zinc-200 bg-white py-1 shadow-lg dark:border-zinc-700 dark:bg-zinc-800"
        >
          {items.map((item) => (
            <button
              key={item.label}
              role="menuitem"
              onClick={(e) => {
                e.stopPropagation();
                setOpen(false);
                item.onSelect();
              }}
              className={`block w-full px-4 py-2.5 text-left text-sm hover:bg-zinc-100 dark:hover:bg-zinc-700 ${
                item.danger ? "text-red-600 dark:text-red-400" : ""
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ---- トースト ----
export function Toasts() {
  const toasts = useToasts((s) => s.toasts);
  const dismiss = useToasts((s) => s.dismiss);
  if (toasts.length === 0) return null;
  const colors = {
    success: "border-emerald-300 dark:border-emerald-800",
    error: "border-red-300 dark:border-red-800",
    info: "border-zinc-300 dark:border-zinc-700",
  };
  return createPortal(
    <div className="pointer-events-none fixed inset-x-0 bottom-20 z-[60] flex flex-col items-center gap-2 px-4 sm:bottom-6">
      {toasts.map((t) => (
        <button
          key={t.id}
          onClick={() => dismiss(t.id)}
          className={`pointer-events-auto max-w-full rounded-xl border bg-white px-4 py-2.5 text-sm shadow-lg dark:bg-zinc-800 ${colors[t.kind]}`}
        >
          {t.message}
        </button>
      ))}
    </div>,
    document.body,
  );
}

// ---- 状態バッジ（色 + 文字を併用）----
const STATUS_STYLE: Record<AppStatus, { label: string; cls: string; dot: string }> = {
  RUNNING: { label: "実行中", cls: "text-emerald-700 dark:text-emerald-400", dot: "bg-emerald-500" },
  STOPPED: { label: "停止", cls: "text-zinc-500 dark:text-zinc-400", dot: "bg-zinc-400" },
  STARTING: { label: "起動中", cls: "text-accent-700 dark:text-accent-400", dot: "bg-accent-500 animate-pulse" },
  STOPPING: { label: "停止中", cls: "text-zinc-600 dark:text-zinc-400", dot: "bg-zinc-500 animate-pulse" },
  RESTARTING: { label: "再起動中", cls: "text-accent-700 dark:text-accent-400", dot: "bg-accent-500 animate-pulse" },
  FAILED: { label: "失敗", cls: "text-red-700 dark:text-red-400", dot: "bg-red-500" },
  DEGRADED: { label: "劣化", cls: "text-amber-700 dark:text-amber-400", dot: "bg-amber-500" },
  UNKNOWN: { label: "不明", cls: "text-zinc-400", dot: "bg-zinc-300 dark:bg-zinc-600" },
};

export function StatusBadge({ status }: { status: AppStatus }) {
  const s = STATUS_STYLE[status] ?? STATUS_STYLE.UNKNOWN;
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs font-medium ${s.cls}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${s.dot}`} aria-hidden />
      {s.label}
    </span>
  );
}

// ---- スパークライン（軽量インライン SVG）----
export function Sparkline({
  values,
  max = 100,
  className = "",
}: {
  values: (number | null)[];
  max?: number;
  className?: string;
}) {
  const w = 100;
  const h = 28;
  const pts = values
    .map((v, i) => {
      if (v == null) return null;
      const x = (i / Math.max(1, values.length - 1)) * w;
      const y = h - (Math.min(v, max) / max) * h;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .filter(Boolean)
    .join(" ");
  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      preserveAspectRatio="none"
      className={`block h-7 w-full ${className}`}
      aria-hidden
    >
      {pts && (
        <polyline
          points={pts}
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          vectorEffect="non-scaling-stroke"
        />
      )}
    </svg>
  );
}

// ---- スケルトン ----
export function Skeleton({ className = "" }: { className?: string }) {
  return (
    <div className={`animate-pulse rounded-lg bg-zinc-200 dark:bg-zinc-800 ${className}`} />
  );
}
