/** リモートデスクトップの有効・無効（設定の 1 項目）。
 *
 * 2026-11-21 利用者「リモートデスクトップは一旦停止する。設定に有効・無効のボタンを。オフになったら通信・
 * サーバーの起動・ホームのアイコンも消す」。判断と強制はサーバー（backend/app/remote_desktop/toggle.py）。
 * ここは切り替えと、補助の更新が要るときの表示だけ。 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { useToasts } from "../../stores";

interface RemoteDesktopState {
  enabled: boolean;
  apply: { state: "ok" | "skipped" | "needs_helper_update" | "error"; message: string; enabled: boolean } | null;
}

export function RemoteDesktopSettings() {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const { data } = useQuery({ queryKey: ["remote-desktop-settings"], queryFn: () => api<RemoteDesktopState>("/settings/remote-desktop") });
  const save = useMutation({
    mutationFn: (enabled: boolean) => api<RemoteDesktopState>("/settings/remote-desktop", { method: "PUT", json: { enabled } }),
    onSuccess: (next) => {
      qc.setQueryData(["remote-desktop-settings"], next);
      // ナビ・ホームのアイコン・/remote の出し分けは meta の enabled_features で決まる。
      qc.invalidateQueries({ queryKey: ["meta"] });
      qc.invalidateQueries({ queryKey: ["remote-status"] });
      show(next.enabled ? "リモートデスクトップを有効にしました" : "リモートデスクトップを無効にしました");
    },
    onError: (e) => show(e instanceof Error ? e.message : "保存に失敗しました", "error"),
  });
  const enabled = data?.enabled ?? true;
  const apply = data?.apply;
  return (
    <section className="mb-4 rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900 md:p-5" data-remote-desktop-settings>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold">リモートデスクトップ</h2>
          <p className="mt-1 text-xs leading-relaxed text-zinc-500">
            オフにすると、この PC のリモートデスクトップのサーバー（xrdp）を止め、起動時にも立ち上げません。
            ControlDeck のリモートの画面と接続も使えなくなります。
          </p>
        </div>
        <button type="button" role="switch" aria-checked={enabled} aria-label="リモートデスクトップを有効にする"
          disabled={!data || save.isPending} onClick={() => save.mutate(!enabled)}
          className="grid min-h-11 min-w-14 shrink-0 place-items-center disabled:opacity-40">
          <span className={"relative h-7 w-12 rounded-full transition-colors " + (enabled ? "bg-accent-600" : "bg-zinc-300 dark:bg-zinc-700")}>
            <span className={"absolute top-0.5 h-6 w-6 rounded-full bg-white shadow transition-[left] " + (enabled ? "left-[22px]" : "left-0.5")} />
          </span>
        </button>
      </div>
      {apply?.state === "needs_helper_update" && (
        <p role="alert" data-remote-desktop-helper-update
          className="mt-3 rounded-xl border border-amber-300 bg-amber-50 px-3 py-2 text-xs leading-relaxed text-amber-800 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-300">
          設定は保存しました。ただし、サーバー（xrdp）の{apply.enabled ? "起動" : "停止"}はまだ行えていません。
          補助の更新が要ります（<code className="break-all font-mono">sudo ./deck.sh service</code> を 1 回）。実行後にもう一度切り替えてください。
        </p>
      )}
      {apply?.state === "error" && (
        <p role="alert" data-remote-desktop-error
          className="mt-3 rounded-xl border border-red-300 bg-red-50 px-3 py-2 text-xs leading-relaxed text-red-800 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300">
          設定は保存しましたが、サーバー（xrdp）の操作に失敗しました: {apply.message}
        </p>
      )}
    </section>
  );
}
