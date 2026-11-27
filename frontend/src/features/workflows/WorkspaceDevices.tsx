/** 作業場へ渡す USB 機器の選択（管理者だけ。既定は何も渡さない）。
 *  シリアル（M5Stack・ESP32 など）・adb／fastboot（Android）・iOS（usbmuxd）の USB だけ。Wi-Fi は未対応。
 *  選んだ機器だけが、次に箱を起こすときに渡される（箱の中の AI は変えられない）。 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { useAuth, useToasts } from "../../stores";

type Entry = { kind: "serial" | "adb" | "fastboot" | "ios"; id: string };
type UsbDevice = Entry & { label: string; path: string; vendor: string; serial_tail: string; guess?: string; available?: boolean; note?: string };
const KIND_LABEL: Record<Entry["kind"], string> = { serial: "シリアル", adb: "Android（adb）", fastboot: "Android（fastboot）", ios: "iOS" };

export function WorkspaceDevices({ name, selected }: { name: string; selected: Entry[] }) {
  const can = useAuth((s) => s.can);
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const [open, setOpen] = useState(false);
  const allowed = can("settings.manage");
  const { data } = useQuery({
    queryKey: ["opencode-usb-devices"], enabled: allowed && open, staleTime: 5_000,
    queryFn: () => api<{ devices: UsbDevice[] }>("/opencode/devices"),
  });
  const save = useMutation({
    mutationFn: (devices: Entry[]) => api(`/opencode/workspaces/${encodeURIComponent(name)}/devices`, { method: "PUT", json: { devices } }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["opencode-workspaces"] }); show("渡す機器を保存しました（次に箱を起こすときから）"); },
    onError: (e) => show(e instanceof Error ? e.message : "保存できませんでした", "error"),
  });
  if (!allowed) return null;
  const has = (e: Entry) => selected.some((s) => s.kind === e.kind && s.id === e.id);
  const toggle = (e: Entry) => save.mutate((has(e) ? selected.filter((s) => !(s.kind === e.kind && s.id === e.id)) : [...selected, e]).map(({ kind, id }) => ({ kind, id })));
  return (
    <div data-testid="workspace-devices" className="min-w-0">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open}
        className="min-h-11 w-full text-left text-zinc-700 underline-offset-2 hover:underline dark:text-zinc-200">
        USB 機器（{selected.length} 台を渡している）{open ? "を閉じる" : "を選ぶ"}
      </button>
      {open && (
        <div className="space-y-1.5">
          <p className="rounded-lg bg-amber-50 p-2 text-[11px] leading-relaxed text-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
            渡した機器は、この作業場の AI が書き込み・消去・任意の通信まで操作できます。信頼できる機器だけを、必要なときだけ 1 台ずつ選んでください。
            シリアル（ESP32・M5Stack など）・Android（adb・fastboot）・iOS の USB だけで、Wi-Fi は渡せません。選んだ機器は次に箱を起こすときに渡り、外すと次から渡りません。
          </p>
          {data && data.devices.length === 0 && <p className="text-zinc-500">つながっている機器がありません。</p>}
          <ul className="space-y-1">
            {(data?.devices ?? []).map((d) => (
              <li key={`${d.kind}:${d.id}`}>
                <label className="flex min-h-11 min-w-0 items-center gap-2">
                  <input type="checkbox" checked={has(d)} disabled={save.isPending || d.available === false} onChange={() => toggle(d)} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium">{KIND_LABEL[d.kind]}・{d.guess || d.label}</span>
                    <span className="block truncate text-[11px] text-zinc-500">{d.vendor && `${d.vendor}・`}シリアル末尾 …{d.serial_tail}・{d.path}</span>
                    {d.note && <span className="block text-[11px] text-amber-700 dark:text-amber-300">{d.note}</span>}
                  </span>
                </label>
              </li>
            ))}
          </ul>
          {selected.filter((s) => !(data?.devices ?? []).some((d) => d.kind === s.kind && d.id === s.id)).map((s) => (
            <p key={s.id} className="flex min-w-0 items-center gap-2 text-[11px] text-zinc-500">
              <span className="min-w-0 flex-1 truncate">つながっていない: {s.id}</span>
              <button type="button" onClick={() => toggle(s)} className="min-h-9 shrink-0 rounded-lg border border-zinc-300 px-2 dark:border-zinc-700">外す</button>
            </p>
          ))}
        </div>
      )}
    </div>
  );
}
