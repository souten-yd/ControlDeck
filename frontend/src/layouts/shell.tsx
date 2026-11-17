import { createContext, useContext, type ReactNode } from "react";
import { IconMenu } from "../components/icons";

/** 画面の殻（AppLayout）が各ページへ貸す操作。
 *  自前の上の行を持つ画面（AI アシスタント）も、同じ ☰ で同じドロワーを開く。 */
export interface ShellContextValue {
  openNav: () => void;
  navOpen: boolean;
  /** どの画面でも見せる通知（承認待ちなど）。自前の上の行を持つ画面が置く。 */
  extras: ReactNode;
  /** 殻の上の行にある、題名と主操作の置き場。PageHeader がここへ描く。 */
  headerSlot: HTMLElement | null;
  /** PageHeader が「題名は自分が出す」と届け出る。届け出がある間、殻は予備の題名を
   *  出さない（h1 は画面に 1 つだけ）。戻り値で取り消す。 */
  registerHeader: () => () => void;
}

export const ShellContext = createContext<ShellContextValue | null>(null);

export function useShell(): ShellContextValue | null {
  return useContext(ShellContext);
}

/** 上の行の高さと面。殻の上の行と、自前の上の行を持つ画面とで揃える。
 *  面は不透明にする。上の行の下へ中身が潜る作りではないので透かす意味が無く、
 *  iPhone のホーム画面アプリでは半透明＋ぼかしがステータスバーの帯と重なって、
 *  題名が曇って見える。上の行から開くメニューがページの中身の下に潜らないよう
 *  z-30 で前に出す（シート・ドロワーの z-50 よりは下）。 */
export const SHELL_TOPBAR_CLASS =
  "safe-top relative z-30 shrink-0 border-b border-zinc-200/80 bg-zinc-50 dark:border-zinc-800/80 dark:bg-zinc-950";

export const shellIconButtonClass =
  "grid h-11 w-11 shrink-0 place-items-center rounded-xl text-xl text-zinc-600 transition-colors hover:bg-zinc-200/60 active:bg-zinc-200 dark:text-zinc-300 dark:hover:bg-zinc-800 dark:active:bg-zinc-700";

/** 左上の ☰。携帯ではドロワーを開く。PC ではサイドバーの先頭に同じ形の
 *  ボタンがあるので、ここでは出さない。 */
export function ShellMenuButton({ className = "" }: { className?: string }) {
  const shell = useShell();
  if (!shell) return null;
  return (
    <button
      type="button"
      onClick={shell.openNav}
      aria-label="メニューを開く"
      aria-haspopup="dialog"
      aria-expanded={shell.navOpen}
      className={`${shellIconButtonClass} md:hidden ${className}`}
    >
      <IconMenu />
    </button>
  );
}
