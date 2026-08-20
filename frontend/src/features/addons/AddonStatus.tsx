import type { AddonStateName } from "../../api/addons";

const STATUS: Partial<Record<AddonStateName, { label: string; className: string }>> = {
  degraded: { label: "一部機能が利用できません", className: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300" },
  unavailable: { label: "停止中", className: "bg-zinc-200 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300" },
  setup_required: { label: "セットアップが必要", className: "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300" },
  incompatible: { label: "互換性がありません", className: "bg-zinc-200 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300" },
  enabling: { label: "確認中", className: "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300" },
  disable_pending: { label: "終了準備中", className: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300" },
};

export function AddonStatusChip({ state, compact = false }: { state: AddonStateName; compact?: boolean }) {
  const status = STATUS[state];
  if (!status) return null;
  if (compact) {
    return <span aria-label={status.label} title={status.label} className={`h-2 w-2 shrink-0 rounded-full ${status.className}`} />;
  }
  return <span className={`inline-flex max-w-full rounded-full px-2 py-1 text-[10px] font-semibold ${status.className}`}>{status.label}</span>;
}

export function addonStateMessage(state: AddonStateName): string {
  if (state === "degraded") return "一部の機能が停止しています。利用可能な機能はそのまま使えます。";
  if (state === "unavailable") return "拡張機能のserviceへ接続できません。";
  if (state === "setup_required") return "利用を始める前にセットアップを完了してください。";
  if (state === "incompatible") return "このmanifestは現在のControl Deckと互換性がありません。";
  if (state === "enabling") return "拡張機能の状態を確認しています。";
  if (state === "installed_disabled") return "この拡張機能は無効です。";
  if (state === "disable_pending") return "拡張機能を安全に終了しています。";
  return "拡張機能は利用できます。";
}
