/** 通知の種類ごとの有効・無効と、お休みモード（利用者本人の設定）。
 *
 * 2026-11-23 利用者の依頼。通知の種類を増やし、種類ごとに切れるようにする。お休みモードの間（既定は 22:00〜07:00）は
 * 通知を出さない。サーバーの入口（backend/app/alerts/events.py）が判断するので、ここは設定の読み書きだけ。 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { useToasts } from "../../stores";

interface KindRow { id: string; group: string; label: string; note: string; default: boolean; enabled: boolean }
interface Quiet { enabled: boolean; start: string; end: string; urgent_alerts: boolean }
interface View {
  groups: { id: string; label: string }[]; kinds: KindRow[]; quiet: Quiet;
  quiet_now: { active: boolean; until: string | null };
}
type Patch = { kinds?: Record<string, boolean>; quiet?: Partial<Quiet> };

const card = "rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900 md:p-5";

function Toggle({ on, label, onChange, ...rest }: { on: boolean; label: string; onChange: (value: boolean) => void } & { [key in `data-${string}`]?: string }) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} onClick={() => onChange(!on)} {...rest}
      className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${on ? "bg-accent-600" : "bg-zinc-300 dark:bg-zinc-700"}`}>
      <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-all ${on ? "left-[22px]" : "left-0.5"}`} />
    </button>
  );
}

export function NotificationSettings() {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const view = useQuery({ queryKey: ["notification-settings"], queryFn: () => api<View>("/notification-settings") });
  const save = useMutation({
    mutationFn: (patch: Patch) => api<View>("/notification-settings", { method: "PUT", json: patch }),
    onSuccess: (next) => qc.setQueryData(["notification-settings"], next),
    onError: (error) => show(error instanceof Error ? error.message : "保存できませんでした", "error"),
  });
  const data = view.data;
  if (!data) return null;
  const { quiet, quiet_now: now } = data;
  return (
    <>
      <section data-quiet-hours className={card}>
        <div className="flex items-center gap-3">
          <h2 className="min-w-0 flex-1 text-sm font-semibold text-zinc-500">お休みモード</h2>
          <Toggle on={quiet.enabled} label="お休みモード" data-quiet-toggle="1" onChange={(enabled) => save.mutate({ quiet: { enabled } })} />
        </div>
        <p className="mt-1 text-xs text-zinc-500">指定した時間のあいだは通知を出しません（その間の通知は届きません）。時刻はサーバーの地域の時刻です。</p>
        <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2 text-sm">
          <label className="flex items-center gap-2">開始
            <input type="time" value={quiet.start} disabled={!quiet.enabled} data-quiet-start
              onChange={(e) => e.target.value && save.mutate({ quiet: { start: e.target.value } })}
              className="min-h-10 rounded-lg border border-zinc-300 bg-white px-2 dark:border-zinc-700 dark:bg-zinc-950" />
          </label>
          <label className="flex items-center gap-2">終了
            <input type="time" value={quiet.end} disabled={!quiet.enabled} data-quiet-end
              onChange={(e) => e.target.value && save.mutate({ quiet: { end: e.target.value } })}
              className="min-h-10 rounded-lg border border-zinc-300 bg-white px-2 dark:border-zinc-700 dark:bg-zinc-950" />
          </label>
        </div>
        <label className="mt-3 flex items-start gap-3 text-sm">
          <Toggle on={quiet.urgent_alerts} label="緊急の監視の警報はお休み中も送る" data-quiet-urgent="1"
            onChange={(urgent_alerts) => save.mutate({ quiet: { urgent_alerts } })} />
          <span className="min-w-0">緊急の監視の警報はお休み中も送る
            <span className="block text-xs text-zinc-500">アプリの停止・ヘルスチェック失敗・高温の警報だけ</span></span>
        </label>
        <p data-quiet-state={now.active ? "active" : "idle"}
          className={`mt-3 text-xs font-medium ${now.active ? "text-amber-700 dark:text-amber-300" : "text-zinc-500"}`}>
          {!quiet.enabled ? "お休みモードは切れています" : now.active ? `今はお休み中です（${now.until} まで）` : "今はお休みの時間ではありません"}
        </p>
      </section>
      <section data-notification-kinds className={card}>
        <h2 className="text-sm font-semibold text-zinc-500">通知の種類</h2>
        <p className="mt-1 text-xs text-zinc-500">受け取りたい通知だけを有効にします。お休みモードの間は、有効でも届きません。</p>
        {data.groups.map((group) => (
          <div key={group.id} className="mt-4">
            <h3 className="text-xs font-semibold">{group.label}</h3>
            <ul className="mt-1 divide-y divide-zinc-100 dark:divide-zinc-800">
              {data.kinds.filter((k) => k.group === group.id).map((k) => (
                <li key={k.id} className="flex items-start gap-3 py-2" data-kind={k.id}>
                  <span className="min-w-0 flex-1 text-sm">{k.label}
                    <span className="block break-words text-xs text-zinc-500">{k.note}</span></span>
                  <Toggle on={k.enabled} label={k.label} data-kind-toggle={k.id} onChange={(enabled) => save.mutate({ kinds: { [k.id]: enabled } })} />
                </li>
              ))}
            </ul>
          </div>
        ))}
      </section>
    </>
  );
}
