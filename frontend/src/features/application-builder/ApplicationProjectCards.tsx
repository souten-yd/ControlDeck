import { Link } from "react-router-dom";
import type { ApplicationProject } from "../../api/applicationBuilder";

export function ApplicationProjectCards({ projects }: { projects: ApplicationProject[] }) {
  if (projects.length === 0) {
    return <div className="rounded-2xl border border-dashed border-zinc-300 p-8 text-center text-sm text-zinc-400 dark:border-zinc-700">Application Projectはまだありません。</div>;
  }
  return <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{projects.map((project) => (
    <Link key={project.id} to={`/applications/${project.id}`} className="rounded-2xl border border-zinc-200 bg-white p-4 shadow-sm transition hover:border-accent-400 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="flex items-center gap-2"><strong className="min-w-0 flex-1 truncate text-sm">{project.name}</strong><span className="rounded-full bg-amber-50 px-2 py-1 text-[10px] font-semibold text-amber-700 dark:bg-amber-950/30 dark:text-amber-300">Draft</span></div>
      <p className="mt-2 line-clamp-2 text-xs text-zinc-500">{project.description || "説明なし"}</p>
      <div className="mt-3 flex flex-wrap gap-1.5 text-[10px] text-zinc-500"><span className="rounded bg-zinc-100 px-2 py-1 dark:bg-zinc-800">{project.application_type}</span><span className="rounded bg-zinc-100 px-2 py-1 dark:bg-zinc-800">{project.ui_framework}</span><span className="rounded bg-zinc-100 px-2 py-1 dark:bg-zinc-800">Spec v{project.schema_version}</span></div>
    </Link>
  ))}</div>;
}
