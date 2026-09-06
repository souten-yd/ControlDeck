import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { ConfirmDialog, Skeleton } from "../../components/ui";
import { useToasts } from "../../stores";

/** OpenCode に読ませる手順書。「何を作るか」ではなく「作る前に何を決めるか」を持たせる。 */
interface Skill {
  id: string;
  name: string;
  summary: string;
  source: string;
  requires: string;
  license: string;
  repository: string;
  available_version: string;
  installed_version: string;
  installed: boolean;
  enabled: boolean;
  update_available: boolean;
  execution?: { state: string; message: string };
  effective?: boolean;
}

type Action = "install" | "update" | "enable" | "disable" | "remove";

const btn =
  "rounded-lg px-3 py-1.5 text-xs font-medium transition disabled:cursor-not-allowed";

export function RecommendedSkills() {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const [removing, setRemoving] = useState<Skill | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["skills"],
    queryFn: () => api<{ items: Skill[] }>("/skills"),
  });

  const act = useMutation({
    mutationFn: ({ id, action }: { id: string; action: Action }) =>
      api<Skill>(`/skills/${id}`, { method: "POST", json: { action } }),
    onSuccess: (result, { action }) => {
      void qc.invalidateQueries({ queryKey: ["skills"] });
      const label: Record<Action, string> = {
        install: "導入しました",
        update: "更新しました",
        enable: "有効にしました",
        disable: "無効にしました",
        remove: "削除しました",
      };
      show(`${result.name}を${label[action]}`);
    },
    onError: (error: Error) => show(error.message, "error"),
  });

  const busy = act.isPending;
  const skills = data?.items ?? [];

  return (
    <section className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900 md:p-5">
      <h2 className="mb-1 text-sm font-semibold text-zinc-500">推奨スキル</h2>
      <p className="mb-3 text-xs text-zinc-400">
        OpenCode に読ませる制作手順。有効にしたものだけが、Control Deck から開いた
        セッションで使われます。反映には OpenCode の起動し直しが要ります。
      </p>

      {isLoading && <Skeleton className="h-24 w-full" />}

      <div className="space-y-2">
        {skills.map((skill) => (
          <div
            key={skill.id}
            data-skill-id={skill.id}
            className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-800"
          >
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium">{skill.name}</span>
                  {skill.installed && !skill.enabled && (
                    <span className="rounded-full bg-zinc-100 px-2 py-0.5 text-[10px] text-zinc-500 dark:bg-zinc-800">
                      無効
                    </span>
                  )}
                  {skill.installed && skill.enabled && (
                    <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-300">
                      有効
                    </span>
                  )}
                  {skill.update_available && (
                    <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] text-amber-700 dark:bg-amber-950/50 dark:text-amber-300">
                      更新あり
                    </span>
                  )}
                  {skill.source === "git" && (
                    <span className="rounded-full bg-sky-100 px-2 py-0.5 text-[10px] text-sky-700 dark:bg-sky-950/50 dark:text-sky-300">
                      外部
                    </span>
                  )}
                </div>
                <p className="mt-1 text-xs text-zinc-500">{skill.summary}</p>
                {skill.requires && (
                  <p className="mt-1 text-[11px] text-amber-700 dark:text-amber-400">
                    使うには別途必要: {skill.requires}
                  </p>
                )}
                {skill.execution && skill.requires && (
                  <p className={`mt-1 text-[11px] ${skill.execution.state === "ready"
                    ? "text-emerald-700 dark:text-emerald-400" : "text-amber-700 dark:text-amber-400"}`}
                    role="status">
                    実行先: {skill.execution.message}
                    {skill.installed && skill.enabled && !skill.effective && " 現在はOpenCodeへ読み込ませません。"}
                  </p>
                )}
                <p className="mt-1 text-[10px] text-zinc-400">
                  {skill.installed
                    ? `導入済み ${skill.installed_version}`
                    : `未導入 ${skill.available_version}`}
                  {skill.license && ` · ${skill.license}`}
                  {skill.repository && ` · ${skill.repository.replace("https://github.com/", "")}`}
                </p>
              </div>

              <div className="flex shrink-0 flex-wrap gap-1.5">
                {!skill.installed && (
                  <button
                    onClick={() => act.mutate({ id: skill.id, action: "install" })}
                    disabled={busy}
                    className={`${btn} bg-accent-600 text-white hover:bg-accent-700 disabled:opacity-40`}
                  >
                    導入
                  </button>
                )}
                {skill.update_available && (
                  <button
                    onClick={() => act.mutate({ id: skill.id, action: "update" })}
                    disabled={busy}
                    className={`${btn} bg-accent-600 text-white hover:bg-accent-700 disabled:opacity-40`}
                  >
                    更新
                  </button>
                )}
                {skill.installed && skill.enabled && (
                  <button
                    onClick={() => act.mutate({ id: skill.id, action: "disable" })}
                    disabled={busy}
                    className={`${btn} bg-zinc-100 text-zinc-700 hover:bg-zinc-200 disabled:opacity-40 dark:bg-zinc-800 dark:text-zinc-200`}
                  >
                    無効化
                  </button>
                )}
                {skill.installed && !skill.enabled && (
                  <button
                    onClick={() => act.mutate({ id: skill.id, action: "enable" })}
                    disabled={busy}
                    className={`${btn} bg-accent-600 text-white hover:bg-accent-700 disabled:opacity-40`}
                  >
                    有効化
                  </button>
                )}
                {skill.installed && (
                  <button
                    onClick={() => setRemoving(skill)}
                    disabled={busy}
                    className={`${btn} text-red-600 hover:bg-red-50 disabled:opacity-40 dark:hover:bg-red-950/40`}
                  >
                    削除
                  </button>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>

      {removing && (
        <ConfirmDialog
          title="スキルを削除しますか？"
          message="Control Deckが導入した手順書だけを削除します。使わなくなっただけなら、無効化すればファイルは残ります。"
          confirmLabel="削除する"
          onConfirm={() => {
            const target = removing;
            setRemoving(null);
            act.mutate({ id: target.id, action: "remove" });
          }}
          onClose={() => setRemoving(null)}
        />
      )}
    </section>
  );
}
