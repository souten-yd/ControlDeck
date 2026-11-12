import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { useToasts } from "../../stores";

type Sensor = {
  id: string;
  label: string;
  value_c: number | null;
  crit_c: number | null;
  max_target_c: number;
  default_target_c: number;
  target_c?: number;
  headroom_c?: number;
};
type Device = {
  bdf: string;
  vram_bytes: number;
  sensors: Sensor[];
  fan_rpm: number | null;
  curve_supported: boolean;
  zero_rpm_supported: boolean;
};
type FanSettings = {
  enabled: boolean;
  bdf: string | null;
  min_percent: number;
  allow_zero_rpm: boolean;
  targets_c: Record<string, number>;
};
type FanStatus = {
  settings: FanSettings;
  devices: Device[];
  helper_installed: boolean;
  state: string;
  error: string;
  active: {
    bdf: string;
    sensors: Sensor[];
    fan_rpm: number | null;
    floor_percent: number | null;
    rise_c_per_min: number;
    zero_rpm_permitted: boolean;
    power_ema_w?: number | null;
    min_predicted_headroom_c?: number;
    learning?: { ff_gain: number; kp: number; ki: number; observations: number };
    critical_sensor: string;
    observed_at: number;
    curve: string | null;
  } | null;
  poll_interval_seconds: number;
  zero_stable_seconds: number;
  learned_parameters?: { ff_gain: number; kp: number };
  cool_hold_seconds: number;
  warning: string;
};

export function FanControlSettings() {
  const show = useToasts((s) => s.show);
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<FanSettings | null>(null);
  const [saving, setSaving] = useState(false);
  const { data, error, isLoading } = useQuery({
    queryKey: ["system-fan-control"],
    queryFn: () => api<FanStatus>("/system/fan-control"),
    refetchInterval: 5000,
  });

  useEffect(() => {
    if (data && draft === null) setDraft(data.settings);
  }, [data, draft]);

  const selected = data?.devices.find((d) => d.bdf === draft?.bdf)
    ?? (data?.devices.length === 1 ? data.devices[0] : undefined);
  const update = (patch: Partial<FanSettings>) => {
    setDraft((old) => old ? { ...old, ...patch } : old);
  };

  const save = async (settings: FanSettings) => {
    setSaving(true);
    try {
      const result = await api<FanStatus>("/system/fan-control", { method: "PUT", json: settings });
      setDraft(result.settings);
      queryClient.setQueryData(["system-fan-control"], result);
      show(result.settings.enabled ? "ファン温度制御を有効にしました" : "GPU標準の自動制御に戻しました");
    } catch (e) {
      show(e instanceof Error ? e.message : "ファン設定を変更できません", "error");
      void queryClient.invalidateQueries({ queryKey: ["system-fan-control"] });
    } finally {
      setSaving(false);
    }
  };

  return (
    <section aria-labelledby="fan-settings-title" className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900 md:p-5">
      <h2 id="fan-settings-title" className="text-sm font-semibold text-zinc-600 dark:text-zinc-300">ファンコントロール（AMD GPU）</h2>
      <p className="mt-2 text-xs leading-relaxed text-zinc-500">
        GPUコア・Hotspot・VRAMなど、GPUが公開する全温度センサーのうち目標温度への余裕が最小のものを優先します。
        ファームウェアの温度連動カーブを使い、必要なときだけ引き上げます。外部ファン制御アプリは使用しません。
      </p>
      <p className="mt-2 text-xs text-amber-700 dark:text-amber-300">
        温度目標は保証された上限ではありません。センサー異常・書込失敗時は標準自動制御へ復帰を試みます。
        CPU・ケースファンは対象外です。
      </p>
      {isLoading && <p className="mt-3 text-xs text-zinc-500">GPUセンサーを検出しています…</p>}
      {error && <p role="alert" className="mt-3 text-xs text-red-600">{error instanceof Error ? error.message : "状態を取得できません"}</p>}
      {data && draft && (
        <div className="mt-4 space-y-4">
          <div className="rounded-xl bg-zinc-100 p-3 text-xs dark:bg-zinc-800" role="status" aria-live="polite">
            状態: <strong>{data.state === "controlling" ? "温度制御中" : data.state === "fan-stop" ? "Zero RPM許可中（PMFW自動制御）" : data.state === "fault" ? "制御停止（異常）" : "標準自動制御"}</strong>
            {data.active && (
              <span className="ml-3 tabular-nums">
                ファン {data.active.fan_rpm === null ? "N/A" : data.active.fan_rpm + " RPM"}・
                指令下限 {data.active.floor_percent === null ? "PMFWに委任" : data.active.floor_percent + "%"}・
                優先センサー {data.active.critical_sensor}・
                上昇傾向 {data.active.rise_c_per_min.toFixed(1)}°C/分
                {data.active.zero_rpm_permitted && "・ファン停止許可中"}
              </span>
            )}
            {data.active?.learning && <p className="mt-2 text-xs tabular-nums text-zinc-500">学習係数 FF {data.active.learning.ff_gain.toFixed(1)} / P {data.active.learning.kp.toFixed(2)} ・ 推定電力 {data.active.power_ema_w === null || data.active.power_ema_w === undefined ? "N/A" : data.active.power_ema_w + "W"}（過熱時は学習値より安全制御を優先）</p>}
            {data.error && <p role="alert" className="mt-2 text-red-600">{data.error}</p>}
          </div>
          <label className="block text-xs font-medium text-zinc-600 dark:text-zinc-300">
            対象GPU（PCIアドレス）
            <select
              className="mt-1 min-h-11 w-full rounded-xl border border-zinc-300 bg-white px-3 text-sm dark:border-zinc-700 dark:bg-zinc-950"
              value={draft.bdf ?? ""}
              onChange={(e) => update({ bdf: e.target.value || null, targets_c: {} })}
            >
              <option value="">GPUを選択</option>
              {data.devices.map((d) => (
                <option key={d.bdf} value={d.bdf}>
                  {d.bdf}・VRAM {(d.vram_bytes / (1024 ** 3)).toFixed(1)} GB・{d.curve_supported ? "制御可能" : "制御非対応"}
                </option>
              ))}
            </select>
          </label>
          {!data.helper_installed && <p className="text-xs text-amber-700">制御helperが未登録です。PC上で ./deck.sh service を実行してください。</p>}
          {selected && (
            <>
              {!selected.curve_supported && <p className="text-xs text-amber-700">このGPUではドライバが有効な5点fan_curveを公開していません。制御は開始できません。</p>}
              <div className="space-y-2">
                <p className="text-xs font-medium text-zinc-600 dark:text-zinc-300">温度目標（上限に近づくほど回転数を増加）</p>
                {selected.sensors.map((s) => (
                  <label key={s.id} className="flex min-h-11 flex-wrap items-center justify-between gap-2 rounded-lg border border-zinc-200 px-3 py-2 dark:border-zinc-800">
                    <span className="text-xs">
                      <span className="font-semibold">{s.label}</span>
                      <span className="ml-2 tabular-nums text-zinc-500">
                        {s.value_c === null ? "N/A" : s.value_c + "°C"}
                        {s.crit_c !== null ? " / HW critical " + s.crit_c + "°C" : ""}
                      </span>
                    </span>
                    <span className="flex items-center gap-1 text-xs">
                      目標
                      <input
                        aria-label={s.label + " 目標温度"}
                        type="number"
                        min={45}
                        max={s.max_target_c}
                        step={1}
                        value={draft.targets_c[s.id] ?? s.default_target_c}
                        onChange={(e) => update({ targets_c: { ...draft.targets_c, [s.id]: Number(e.target.value) } })}
                        className="w-16 rounded-lg border border-zinc-300 bg-white px-2 py-2 text-right tabular-nums dark:border-zinc-700 dark:bg-zinc-950"
                      />
                      °C
                    </span>
                  </label>
                ))}
                {selected.sensors.length === 0 && <p className="text-xs text-amber-700">温度センサーが見つかりません。</p>}
              </div>
            </>
          )}
          <label className="flex items-center justify-between gap-3 text-xs">
            最低ファン指令（30〜70%、初期35%）
            <input
              aria-label="最低ファン指令"
              type="number"
              min={30}
              max={70}
              value={draft.min_percent}
              onChange={(e) => update({ min_percent: Number(e.target.value) })}
              className="w-20 rounded-lg border border-zinc-300 bg-white px-3 py-2 text-right tabular-nums dark:border-zinc-700 dark:bg-zinc-950"
            />
          </label>
          <label className="flex min-h-11 items-center justify-between gap-3 rounded-lg border border-zinc-200 px-3 py-2 text-sm dark:border-zinc-800">
            <span>
              <span className="block">低温安定時のファン停止を許可（Zero RPM）</span>
              <span className="block text-xs text-zinc-500">
                初期OFF。全センサーが低温・低電力・温度安定を10分間継続した場合のみ、AMDファームウェアの停止機能を許可します。
                上昇時は即座に解除します。
              </span>
              {selected && !selected.zero_rpm_supported && (
                <span className="block text-xs text-amber-700 dark:text-amber-300">
                  このGPU/ドライバには検証可能なZero RPM専用制御がありません。
                </span>
              )}
            </span>
            <input
              type="checkbox"
              aria-label="低温時のファン停止を許可"
              checked={draft.allow_zero_rpm}
              disabled={!selected?.zero_rpm_supported || saving}
              onChange={(e) => update({ allow_zero_rpm: e.target.checked })}
              className="h-5 w-5 shrink-0"
            />
          </label>
          <p className="text-xs text-zinc-500">
            LLMの負荷率には反応せず、温度上昇を検知すると即加速します。低温方向は3分保持後、45秒ごとに最大5%ずつ減速します。
            通常時はアイドル回転を維持します。
          </p>
          <label className="flex min-h-11 items-center justify-between gap-3 rounded-lg bg-zinc-50 px-3 text-sm dark:bg-zinc-800">
            温度制御を有効にする（初期OFF）
            <input type="checkbox" checked={draft.enabled} onChange={(e) => update({ enabled: e.target.checked })} className="h-5 w-5" />
          </label>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              disabled={saving || (draft.enabled && (!selected?.curve_supported || !data.helper_installed || (draft.allow_zero_rpm && !selected?.zero_rpm_supported)))}
              onClick={() => void save({ ...draft, bdf: selected?.bdf ?? draft.bdf })}
              className="min-h-11 rounded-xl bg-accent-600 px-4 text-sm font-medium text-white disabled:opacity-40"
            >
              {saving ? "反映中…" : "保存して適用"}
            </button>
            <button
              type="button"
              disabled={saving || (!data.settings.enabled && data.state !== "fault")}
              onClick={() => void save({ ...draft, enabled: false, bdf: data.settings.bdf })}
              className="min-h-11 rounded-xl border border-zinc-300 px-4 text-sm dark:border-zinc-700 disabled:opacity-40"
            >
              標準自動制御に戻す
            </button>
          </div>
          <p className="text-[11px] text-zinc-500">
            使用可能なセンサーだけを表示します。VRAM温度が公開されないGPUではVRAMの温度維持は行えません。
            ファン回転数がN/AのGPUではファン停止を検知できないため、実機で動作確認してください。
          </p>
        </div>
      )}
    </section>
  );
}
