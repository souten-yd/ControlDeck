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
    <header className={`mb-5 flex min-h-11 flex-wrap items-start gap-3 ${className}`}>
      {leading}
      <div className="min-w-0 flex-1 pt-0.5">
        <h1 className="text-xl font-semibold leading-7 tracking-tight">{title}</h1>
        {description && <p className="mt-1 max-w-3xl text-xs leading-relaxed text-zinc-500">{description}</p>}
      </div>
      {actions && <div className="flex min-h-11 shrink-0 flex-wrap items-center justify-end gap-2">{actions}</div>}
    </header>
  );
}
