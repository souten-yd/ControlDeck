import type { ReactNode } from "react";
import { createPortal } from "react-dom";
import { useShell } from "../layouts/shell";

interface PageHeaderProps {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  leading?: ReactNode;
  className?: string;
}

/** 通常ページ共通header。独立viewer/editorは対象外。
 *
 *  アプリの殻の中では、題名と操作を殻の上の行（☰ の右）へ描く。題名が「上の行」と
 *  「ページの先頭」の 2 か所に出ないようにし、どの画面も ☰・題名・操作の並びを同じにする。
 *  ページ側に残るのは説明だけ。殻の外（単体表示・テスト）では従来どおりその場に描く。
 *
 *  見出しと操作は必ず1行に収める。操作は IconButton（主操作は primary、補助は soft）で渡す。
 *  説明は操作と幅を取り合わないよう、行の下に全幅で置く。 */
export function PageHeader({ title, description, actions, leading, className = "" }: PageHeaderProps) {
  const shell = useShell();
  if (shell) {
    const slot = shell.headerSlot;
    return (
      <>
        {slot && createPortal(
          <>
            {leading}
            <h1 className="min-w-0 flex-1 truncate text-lg font-semibold leading-7 tracking-tight">{title}</h1>
            {actions && <div className="flex shrink-0 items-center justify-end gap-2">{actions}</div>}
          </>,
          slot,
        )}
        {description && <p className="mb-4 max-w-3xl text-xs leading-relaxed text-zinc-500">{description}</p>}
      </>
    );
  }
  return (
    <header className={`${/(^|\s)mb-/.test(className) ? "" : "mb-4"} ${className}`}>
      {/* 操作が無いheaderは高さを見出しだけに詰める（min-h-11はタップ領域用）。 */}
      <div className={`flex items-center gap-3 ${actions || leading ? "min-h-11" : ""}`}>
        {leading}
        <h1 className="min-w-0 flex-1 truncate text-xl font-semibold leading-7 tracking-tight">{title}</h1>
        {actions && <div className="flex shrink-0 items-center justify-end gap-2">{actions}</div>}
      </div>
      {description && <p className="mt-1.5 max-w-3xl text-xs leading-relaxed text-zinc-500">{description}</p>}
    </header>
  );
}
