/** モデルの表示名（利用者が付ける名前）。ゲートウェイでの名前（alias）は変えない（会話・Workflow・既定のモデルが alias を覚えているため）。
 *  2026-11-21 利用者「モデルが同じ名前になるので分かりにくい。任意の名前に変えれるようにしたい」。 */
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { useToasts } from "../../stores";

type Runtime = "llama.cpp" | "strata" | "radiance" | "sly-radiance" | "maya-s";

export function DisplayNameField({ runtime, alias, disabled }: { runtime: Runtime; alias: string; disabled?: boolean }) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const { data } = useQuery({
    queryKey: ["provider-models", runtime],
    queryFn: () => api<{ id: string; details: { display_name?: string } }[]>(`/models/providers/${runtime}/models`),
  });
  const current = data?.find((m) => m.id === alias)?.details.display_name ?? "";
  const [name, setName] = useState(current);
  useEffect(() => setName(current), [current]);
  const save = useMutation({
    mutationFn: (value: string) => api<{ name: string }>(`/models/display-names/${encodeURIComponent(runtime)}/${encodeURIComponent(alias)}`,
      { method: "PUT", json: { name: value } }),
    onSuccess: (r) => {
      for (const key of [["provider-models", runtime], ["models", "local"], ["llm-endpoints"]]) qc.invalidateQueries({ queryKey: key });
      show(r.name ? `表示名を「${r.name}」にしました` : "表示名を既定に戻しました");
    },
    onError: (e) => show(e instanceof Error ? e.message : "保存できませんでした", "error"),
  });
  const changed = name.trim() !== current;
  return (
    <div className="rounded-2xl border border-zinc-200 p-3 dark:border-zinc-800">
      <label className="block text-xs font-medium">表示名
        <input aria-label="モデルの表示名" value={name} maxLength={80} disabled={disabled || save.isPending}
          onChange={(e) => setName(e.target.value)}
          className="mt-1 min-h-11 w-full rounded-xl border border-zinc-300 bg-white px-3 text-sm dark:border-zinc-700 dark:bg-zinc-900" />
      </label>
      <p className="mt-1 text-[11px] text-zinc-500">
        一覧やモデルを選ぶ欄に出る名前です。ゲートウェイでの名前 <code className="font-mono">{alias}</code> は変わりません（会話や Workflow の設定はそのまま使えます）。
      </p>
      <div className="mt-2 flex gap-2">
        <button type="button" disabled={!changed || !name.trim() || save.isPending} onClick={() => save.mutate(name.trim())}
          className="min-h-10 rounded-xl bg-accent-600 px-3.5 text-xs font-medium text-white hover:bg-accent-700 disabled:opacity-40">保存</button>
        <button type="button" disabled={save.isPending} onClick={() => save.mutate("")}
          className="min-h-10 rounded-xl px-3.5 text-xs text-zinc-600 hover:bg-zinc-100 disabled:opacity-40 dark:text-zinc-300 dark:hover:bg-zinc-800">既定に戻す</button>
      </div>
    </div>
  );
}
