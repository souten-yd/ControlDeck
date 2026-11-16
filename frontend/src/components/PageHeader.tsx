import type { ReactNode } from "react";

interface PageHeaderProps {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  leading?: ReactNode;
  className?: string;
}

/** 通常ページ共通header。独立viewer/editorは対象外。
 *
 *  見出しと操作は必ず1行に収める。操作は IconButton（主操作は primary、補助は soft）で渡す。
 *  説明は操作と幅を取り合わないよう、行の下に全幅で置く。 */
export function PageHeader({ title, description, actions, leading, className = "" }: PageHeaderProps) {
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
