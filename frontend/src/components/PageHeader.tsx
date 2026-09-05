import type { ReactNode } from "react";

interface PageHeaderProps {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  leading?: ReactNode;
  className?: string;
}

/** 通常ページ共通header。独立viewer/editorは対象外。 */
export function PageHeader({ title, description, actions, leading, className = "" }: PageHeaderProps) {
  return (
    // 操作ボタンが無いheaderは高さを見出しだけに詰める（min-h-11はタップ領域用）。
    <header className={`mb-5 flex flex-wrap items-start gap-3 ${actions || leading ? "min-h-11" : ""} ${className}`}>
      {leading}
      <div className="min-w-0 flex-1 pt-0.5">
        <h1 className="text-xl font-semibold leading-7 tracking-tight">{title}</h1>
        {description && <p className="mt-1 max-w-3xl text-xs leading-relaxed text-zinc-500">{description}</p>}
      </div>
      {actions && <div className="flex min-h-11 min-w-0 flex-wrap items-center justify-end gap-2">{actions}</div>}
    </header>
  );
}
