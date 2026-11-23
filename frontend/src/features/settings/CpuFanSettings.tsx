import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { useAuth, useToasts } from "../../stores";

interface CpuFanInfo {
  index: number;
  candidates: { chip: string; index: number; label: string; rpm: number; pwm_percent: number | null }[];
  current: { rpm: number | null; pwm_percent: number | null };
}

/** Home の CPU に出すファンの選択。ラベルに CPU_FAN が付く板では自動で決まる（読むだけ・制御はしない）。 */
export function CpuFanSettings() {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const canManage = useAuth((s) => s.can)("settings.manage");
  const { data } = useQuery({ queryKey: ["cpu-fan"], queryFn: () => api<CpuFanInfo>("/system/cpu-fan") });
  const save = async (index: number) => {
    try {
      await api("/system/cpu-fan", { method: "PUT", json: { index } });
      await qc.invalidateQueries({ queryKey: ["cpu-fan"] });
      show("CPU ファンの番号を保存しました", "info");
    } catch {
      show("保存できませんでした", "error");
    }
  };
  return (
    <section className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900 md:p-5" data-testid="cpu-fan-settings">
      <h3 className="mb-1 text-sm font-semibold">CPU ファン</h3>
      <p className="mb-3 text-xs text-zinc-400">
        Home の CPU に出す回転数と PWM のファンを選びます（読み取りだけで、ファンは制御しません）。回転数が 0 のファンは候補に出ません。
        候補が無いときは、機能と拡張の「マザーボードのファン」で nct6775 ドライバを読み込んでください。
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor="cpu-fan-index" className="sr-only">CPU ファンの番号</label>
        <select id="cpu-fan-index" value={data?.index ?? 1} disabled={!canManage || !data}
          onChange={(e) => void save(Number(e.target.value))}
          className="min-h-11 rounded-xl border border-zinc-300 bg-white px-3 text-sm dark:border-zinc-700 dark:bg-zinc-900">
          {Array.from({ length: 7 }, (_, i) => i + 1).map((n) => {
            const c = data?.candidates.find((x) => x.index === n);
            return <option key={n} value={n}>{`fan${n}`}{c ? `（${c.label ? `${c.label} ` : ""}${c.rpm} rpm${c.pwm_percent != null ? `・PWM ${c.pwm_percent}%` : ""}）` : ""}</option>;
          })}
        </select>
        <span className="text-xs text-zinc-500">
          {data?.current.rpm != null ? `いま ${data.current.rpm} rpm${data.current.pwm_percent != null ? `・PWM ${data.current.pwm_percent}%` : ""}` : "いまは読めません"}
        </span>
      </div>
    </section>
  );
}
