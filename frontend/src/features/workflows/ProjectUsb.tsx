/** OpenCode のプロジェクトの箱へ USB 機器（M5Stack・ESP32・Android・iOS の書き込み用）を渡す設定。
 *  - 作業フォルダの横の小さな「USB」の札: 機器が 1 台以上つながっているときだけ（管理者だけ）。押すとシート。
 *  - シート: つながっている機器の一覧（名前・推定・パス）と「このプロジェクトに渡す: 切／自動（全部）／選ぶ」。既定は切。
 *  - 返事に Permission denied など USB の権限・見つからないエラーが出たら、その下に UsbHint（押すと同じシート）。
 *  反映は箱を起こすとき。常駐の会話サーバーが動いていれば「起こし直す」で今の設定を反映する。 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import { BottomSheet } from "../../components/ui";
import { useAuth, useToasts } from "../../stores";

type Kind = "serial" | "adb" | "fastboot" | "ios";
type Entry = { kind: Kind; id: string };
type Mode = "off" | "auto" | "selected";
type UsbDevice = Entry & { label: string; path: string; vendor: string; serial_tail: string; guess: string; available: boolean; note: string };
type HostAdb = { running: boolean; pids: number[]; listening: boolean };
type DevicesResponse = {
  devices: UsbDevice[]; usbmuxd: boolean; tools: { name: string; ok: boolean; hint: string }[];
  android_sdk?: { installed: boolean }; host_adb?: HostAdb;
};
type Setting = { mode: Mode; selected: Entry[]; restart_needed?: boolean };

const KIND_LABEL: Record<Kind, string> = { serial: "シリアル", adb: "Android（adb）", fastboot: "Android（fastboot）", ios: "iOS" };
const MODES: { id: Mode; label: string; hint: string }[] = [
  { id: "off", label: "切", hint: "何も渡しません（既定）。" },
  { id: "auto", label: "自動（全部）", hint: "起動のたびに、つながっている開発向けの機器を全部渡します。" },
  { id: "selected", label: "選ぶ", hint: "選んだ機器だけを渡します。" },
];
/** 返事の中の、USB の権限・見つからないエラー（機器を渡していないときに出る） */
const USB_ERROR = /Permission denied[^\n]*(tty|usb|serial)|(tty(USB|ACM|CH\w*)\d*|\/dev\/serial|\/dev\/bus\/usb)[^\n]*(No such file|not found|Permission denied|could not open|cannot open)|could not open port|failed to open (serial|port)|no (adb )?devices\/emulators found|usbmuxd/i;

export function hasUsbError(text: string): boolean {
  return USB_ERROR.test(text);
}

function useDevices(enabled: boolean) {
  return useQuery({
    queryKey: ["opencode-usb-devices"], enabled, staleTime: 5_000, refetchInterval: 15_000,
    queryFn: () => api<DevicesResponse>("/opencode/devices"),
  });
}

function UsbSheet({ project, data, onClose }: { project: string; data?: DevicesResponse; onClose: () => void }) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const key = ["opencode-project-usb", project];
  const { data: setting } = useQuery({ queryKey: key, queryFn: () => api<Setting>(`/opencode/projects/${encodeURIComponent(project)}/usb`) });
  const save = useMutation({
    mutationFn: (next: { mode: Mode; selected: Entry[] }) =>
      api<Setting>(`/opencode/projects/${encodeURIComponent(project)}/usb`, { method: "PUT", json: next }),
    onSuccess: (result) => { qc.setQueryData(key, result); show("USB 機器の設定を保存しました（次に箱を起こすときから）"); },
    onError: (e) => show(e instanceof Error ? e.message : "保存できませんでした", "error"),
  });
  const restart = useMutation({
    mutationFn: () => api<{ stopped: number }>(`/opencode/projects/${encodeURIComponent(project)}/usb/restart`, { method: "POST" }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: key }); show("会話サーバーを止めました。次のメッセージで今の設定の機器を渡して起き直します"); },
    onError: (e) => show(e instanceof Error ? e.message : "止められませんでした", "error"),
  });
  const stopAdb = useMutation({
    mutationFn: () => api<{ ok: boolean; status: HostAdb }>("/opencode/devices/host-adb/stop", { method: "POST" }),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ["opencode-usb-devices"] });
      show(r.ok && !r.status.running ? "ホストの adb を止めました" : "ホストの adb がまだ動いています", r.ok && !r.status.running ? undefined : "error");
    },
    onError: (e) => show(e instanceof Error ? e.message : "止められませんでした", "error"),
  });
  const mode = setting?.mode ?? "off";
  const selected = setting?.selected ?? [];
  const has = (e: Entry) => selected.some((s) => s.kind === e.kind && s.id === e.id);
  const toggle = (e: Entry) => save.mutate({
    mode: "selected",
    selected: (has(e) ? selected.filter((s) => !(s.kind === e.kind && s.id === e.id)) : [...selected, e]).map(({ kind, id }) => ({ kind, id })),
  });
  const missing = (data?.tools ?? []).filter((t) => !t.ok);
  const android = (data?.devices ?? []).filter((d) => d.kind === "adb" || d.kind === "fastboot");
  // Android の機器を渡す設定のとき（自動なら Android の機器があれば、選ぶなら選んだものに含まれれば）だけ、ホストの adb の案内を出す
  const androidGiven = mode === "auto" ? android.length > 0 : mode === "selected" && android.some((d) => has(d));
  const hostAdbBusy = !!data?.host_adb?.running && androidGiven;
  const needSdk = !!data && data.android_sdk?.installed === false && (android.length > 0 || missing.some((t) => t.name === "adb" || t.name === "fastboot"));
  const canManage = useAuth((s) => s.can)("settings.manage");
  return (
    <BottomSheet title="USB 機器" onClose={onClose}>
      <div data-testid="usb-sheet" className="space-y-3 text-sm">
        <div role="radiogroup" aria-label="このプロジェクトに渡す" className="grid grid-cols-3 gap-1.5">
          {MODES.map((m) => (
            <button key={m.id} type="button" role="radio" aria-checked={mode === m.id} disabled={save.isPending || !setting}
              onClick={() => save.mutate({ mode: m.id, selected: m.id === "selected" ? selected : [] })}
              className={`min-h-11 rounded-lg border px-2 text-sm ${mode === m.id ? "border-accent-500 bg-accent-50 font-medium dark:bg-accent-950/30" : "border-zinc-300 dark:border-zinc-700"}`}>
              {m.label}
            </button>
          ))}
        </div>
        <p className="text-xs text-zinc-500 dark:text-zinc-400">
          このプロジェクトに渡す: {MODES.find((m) => m.id === mode)?.hint}
          箱の中の AI は、渡した機器へ書き込み・消去・任意の通信ができます。信頼できる機器だけをつないでください。
        </p>
        {hostAdbBusy && (
          <div data-testid="usb-host-adb" role="alert" className="rounded-lg border border-amber-300 bg-amber-50 p-2 text-xs text-amber-900 dark:border-amber-700 dark:bg-amber-950/30 dark:text-amber-200">
            <p className="font-medium">ホストの adb が機器を使っています</p>
            <p>ホストの adb サーバーが動いていると、箱の中の adb から機器が見えません。止めてから箱を起こし直してください（次に adb を使うと箱の側で起動します）。</p>
            <button type="button" data-testid="usb-host-adb-stop" disabled={stopAdb.isPending} onClick={() => stopAdb.mutate()}
              className="mt-1.5 min-h-11 rounded-lg border border-amber-400 bg-white px-3 dark:border-amber-600 dark:bg-transparent">ホストの adb を止める</button>
          </div>
        )}
        <ul className="space-y-1">
          {(data?.devices ?? []).map((d) => (
            <li key={`${d.kind}:${d.id}`} className="flex min-h-11 min-w-0 items-center gap-2">
              {mode === "selected" && (
                <input type="checkbox" aria-label={`${d.guess || d.label} を渡す`} checked={has(d)} disabled={save.isPending || !d.available} onChange={() => toggle(d)} />
              )}
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium">{KIND_LABEL[d.kind]}・{d.guess || d.label}</span>
                <span className="block truncate text-[11px] text-zinc-500">{d.vendor && `${d.vendor}・`}{d.serial_tail && `シリアル末尾 …${d.serial_tail}・`}{d.path}</span>
                {d.note && <span className="block text-[11px] text-amber-700 dark:text-amber-300">{d.note}</span>}
              </span>
            </li>
          ))}
        </ul>
        {setting?.restart_needed && mode !== "off" && (
          <div className="rounded-lg bg-zinc-100 p-2 text-xs dark:bg-zinc-800/60">
            <p>この会話サーバーは動いたままです。今の設定の機器を渡すには起こし直します（返事の途中なら中断されます）。</p>
            <button type="button" onClick={() => restart.mutate()} disabled={restart.isPending}
              className="mt-1.5 min-h-11 rounded-lg border border-zinc-300 px-3 dark:border-zinc-600">会話サーバーを起こし直す</button>
          </div>
        )}
        {needSdk && canManage && (
          <p data-testid="usb-sdk-link" className="text-xs text-zinc-600 dark:text-zinc-300">
            Android の道具（adb・fastboot・ビルド用の SDK）が箱にありません。{" "}
            <Link to="/settings/features#feature-android-sdk" className="font-medium text-accent-600 underline dark:text-accent-400">設定で入れる</Link>
            <span className="block text-[11px] text-zinc-500">Android のビルドは依存の取得に外への通信が要ります（プロジェクトの箱は通信できます。通信が中継だけの作業場の箱では取れません）。</span>
          </p>
        )}
        {missing.length > 0 && (
          <details className="text-xs text-zinc-500 dark:text-zinc-400">
            <summary className="min-h-11 cursor-pointer leading-[2.75rem]">箱の中に無い道具（{missing.length}）</summary>
            <ul className="space-y-0.5 pb-2">
              {missing.map((t) => <li key={t.name}>{t.name}: 入っていません。{t.hint}（自動では入れません）</li>)}
            </ul>
          </details>
        )}
      </div>
    </BottomSheet>
  );
}

/** 作業フォルダの横の「USB」の札。管理者で、機器が 1 台以上つながっているときだけ出る。 */
export function UsbChip({ project }: { project: string }) {
  const allowed = useAuth((s) => s.can)("settings.manage");
  const [open, setOpen] = useState(false);
  const { data } = useDevices(allowed && !!project);
  const { data: setting } = useQuery({
    queryKey: ["opencode-project-usb", project], enabled: allowed && !!project && (data?.devices.length ?? 0) > 0,
    queryFn: () => api<Setting>(`/opencode/projects/${encodeURIComponent(project)}/usb`),
  });
  if (!allowed || !project || !data || data.devices.length === 0) return null;
  const on = setting && setting.mode !== "off";
  return (
    <>
      <button type="button" data-testid="usb-chip" onClick={() => setOpen(true)} aria-label={`USB 機器（${data.devices.length} 台つながっています）`}
        className={`min-h-11 shrink-0 rounded-lg border px-2.5 text-xs font-medium ${on ? "border-accent-500 bg-accent-50 text-accent-700 dark:bg-accent-950/30 dark:text-accent-300" : "border-zinc-300 text-zinc-600 dark:border-zinc-700 dark:text-zinc-300"}`}>
        USB{on ? "・渡す" : ""}
      </button>
      {open && <UsbSheet project={project} data={data} onClose={() => setOpen(false)} />}
    </>
  );
}

/** 返事に USB の権限・見つからないエラーが出たとき、その下に出す案内（押すと同じシート）。 */
export function UsbHint({ project, text }: { project: string; text: string }) {
  const allowed = useAuth((s) => s.can)("settings.manage");
  const [open, setOpen] = useState(false);
  const { data } = useDevices(allowed && !!project && hasUsbError(text));
  if (!allowed || !project || !hasUsbError(text)) return null;
  return (
    <>
      <button type="button" data-testid="usb-hint" onClick={() => setOpen(true)}
        className="mt-1 min-h-11 rounded-lg border border-amber-300 bg-amber-50 px-3 text-left text-xs text-amber-900 dark:border-amber-700 dark:bg-amber-950/30 dark:text-amber-200">
        USB 機器が見えていないようです。「USB 機器を渡す」を開く
      </button>
      {open && <UsbSheet project={project} data={data} onClose={() => setOpen(false)} />}
    </>
  );
}
