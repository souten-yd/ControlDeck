import { useEffect, useId, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { useToasts } from "../../stores";

type FanSettings = {
  enabled: boolean;
  bdf: string | null;
  preset_id: "recommended" | "quiet" | "cooling" | "custom";
  min_percent: number;
  allow_zero_rpm: boolean;
  cool_hold_seconds: number;
  cool_step_seconds: number;
  cool_step_percent: number;
  zero_stable_seconds: number;
  learning_enabled: boolean;
  ff_gain: number;
  kp: number;
  ki: number;
  targets_c: Record<string, number>;
};
type FanPreset = Omit<FanSettings, "enabled" | "bdf" | "preset_id" | "targets_c"> & {
  id: "recommended" | "quiet" | "cooling";
  label: string;
  description: string;
  target_offset_c: number;
};
type Sensor = {
  id: string;
  label: string;
  value_c: number | null;
  crit_c: number | null;
  max_target_c: number;
  default_target_c: number;
  headroom_c?: number;
};
type FanDevice = {
  bdf: string;
  vram_bytes: number;
  sensors: Sensor[];
  fan_rpm: number | null;
  curve_supported: boolean;
  zero_rpm_supported: boolean;
  /** false: SMU 14 (RDNA4). Disabling keeps the last curve until reboot. */
  restore_supported?: boolean;
  power_w: number | null;
  power_cap_w: number | null;
  curve_limits: {
    temp_min_c: number; temp_max_c: number;
    pwm_min_percent: number; pwm_max_percent: number;
  } | null;
};
type FanStatus = {
  settings: FanSettings;
  presets: FanPreset[];
  devices: FanDevice[];
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
  warning: string;
};

const surface = "rounded-xl border border-zinc-200 bg-zinc-50/60 p-3 dark:border-zinc-700 dark:bg-zinc-800/40";
const primaryButton = "min-h-11 rounded-xl bg-accent-600 px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40";

function RangeControl({
  label, value, min, max, step = 1, suffix = "", hint, disabled = false, onChange,
}: {
  label: string; value: number; min: number; max: number; step?: number;
  suffix?: string; hint?: string; disabled?: boolean; onChange: (value: number) => void;
}) {
  const id = useId();
  const valid = Number.isFinite(value) && min <= max;
  const safe = valid ? Math.max(min, Math.min(max, value)) : min;
  const set = (next: number) => {
    if (!Number.isFinite(next)) return;
    const clamped = Math.max(min, Math.min(max, next));
    onChange(Number(clamped.toFixed(3)));
  };
  return (
    <div className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-700">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <label htmlFor={id} className="text-xs font-medium">{label}</label>
        <span className="tabular-nums text-sm font-semibold">{safe}{suffix}</span>
      </div>
      {hint && <p className="mt-1 text-[11px] leading-relaxed text-zinc-500">{hint}</p>}
      <div className="mt-2 flex items-center gap-3">
        <input
          id={id}
          type="range" min={min} max={max} step={step}
          value={safe} disabled={disabled || !valid} onChange={(e) => set(Number(e.target.value))}
          className="min-w-0 flex-1 accent-sky-600"
          aria-label={label}
        />
        <input
          type="number" min={min} max={max} step={step}
          value={safe} disabled={disabled || !valid} onChange={(e) => {
            if (e.target.value !== "") set(Number(e.target.value));
          }}
          aria-label={label + " 数値入力"}
          className="w-20 rounded-lg border border-zinc-300 bg-white px-2 py-2 text-right text-xs tabular-nums dark:border-zinc-600 dark:bg-zinc-900"
        />
      </div>
      <div className="flex justify-between text-[10px] tabular-nums text-zinc-400">
        <span>{min}{suffix}</span><span>{max}{suffix}</span>
      </div>
    </div>
  );
}

function FanCurvePreview({ device, minimum, activeCurve }: {
  device: FanDevice; minimum: number; activeCurve: string | null;
}) {
  const limits = device.curve_limits;
  if (!limits) return null;
  const { temp_min_c: t0, temp_max_c: t1, pwm_min_percent: p0, pwm_max_percent: p1 } = limits;
  const start = Math.max(t0, 25);
  const end = Math.min(t1, 95);
  if (end - start < 4) return null;
  const baseline = [minimum, minimum, 65, 90, p1].map((v, index) => ({
    temp: start + Math.floor(((end - start) * index) / 4),
    percent: Math.min(p1, Math.max(p0, minimum, v)),
  }));
  const fromHardware = activeCurve?.split(",").map((item) => {
    const [a, b] = item.split(":").map(Number);
    return { temp: a, percent: b };
  }).filter((v) => Number.isFinite(v.temp) && Number.isFinite(v.percent)) ?? [];
  const x = (temp: number) => 28 + 286 * ((temp - t0) / (t1 - t0));
  const y = (percent: number) => 145 - 117 * (percent / 100);
  const path = (points: { temp: number; percent: number }[]) =>
    points.map((p, i) => (i ? "L" : "M") + x(p.temp).toFixed(1) + " " + y(p.percent).toFixed(1)).join(" ");
  return (
    <div className={surface}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-xs font-semibold">ファンカーブのプレビュー</h3>
        <span className="text-[11px] text-zinc-500">GPU温度（横軸） / ファン指令%（縦軸）</span>
      </div>
      <svg viewBox="0 0 340 178" role="img" aria-label="参考ファンカーブ。青は未適用の基本曲線、点線は適用中の曲線" className="mt-2 w-full" style={{ maxHeight: 225 }}>
        {[0, 25, 50, 75, 100].map((v) => (
          <g key={v}>
            <line x1="28" x2="314" y1={y(v)} y2={y(v)} stroke="currentColor" strokeOpacity=".14" />
            <text x="21" y={y(v) + 3} fontSize="9" textAnchor="end" fill="currentColor" opacity=".72">{v}</text>
          </g>
        ))}
        {[t0, Math.round((t0 + t1) / 2), t1].map((v) => (
          <text key={v} x={x(v)} y="165" textAnchor="middle" fontSize="10" fill="currentColor" opacity=".7">{v}°</text>
        ))}
        <path d={path(baseline)} fill="none" stroke="#0284c7" strokeWidth="2.8" strokeLinecap="round" strokeLinejoin="round" />
        {baseline.map((p, i) => <circle key={i} cx={x(p.temp)} cy={y(p.percent)} r="3" fill="#0284c7" />)}
        {fromHardware.length === 5 && (
          <path d={path(fromHardware)} fill="none" stroke="#d97706" strokeWidth="1.8" strokeDasharray="5 4" />
        )}
      </svg>
      <p className="text-[11px] leading-relaxed text-zinc-500">
        青: 変更中の基本カーブ（未適用）。{fromHardware.length === 5 && "橙破線: 最後に適用したカーブ。"}
        実際は温度・電力・学習によって青い線より回転を引き上げます。GPUが公開する最大ファン指令までの保護は維持します。
      </p>
    </div>
  );
}

export function FanControlSettings() {
  const show = useToasts((state) => state.show);
  const qc = useQueryClient();
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

  const selected = data?.devices.find((device) => device.bdf === draft?.bdf)
    ?? (data?.devices.length === 1 ? data.devices[0] : undefined);
  const modified = Boolean(draft && data && JSON.stringify(draft) !== JSON.stringify(data.settings));
  const update = (patch: Partial<FanSettings>, markCustom = true) =>
    setDraft((current) => current
      ? { ...current, ...patch, preset_id: markCustom ? "custom" : (patch.preset_id ?? current.preset_id) }
      : current);

  const selectPreset = (preset: FanPreset) => {
    if (!draft) return;
    const targets: Record<string, number> = {};
    for (const sensor of selected?.sensors ?? []) {
      targets[sensor.id] = Math.max(45, Math.min(sensor.max_target_c, sensor.default_target_c + preset.target_offset_c));
    }
    setDraft({ ...draft, preset_id: preset.id, min_percent: preset.min_percent,
      allow_zero_rpm: preset.allow_zero_rpm, cool_hold_seconds: preset.cool_hold_seconds,
      cool_step_seconds: preset.cool_step_seconds, cool_step_percent: preset.cool_step_percent,
      zero_stable_seconds: preset.zero_stable_seconds, learning_enabled: preset.learning_enabled,
      ff_gain: preset.ff_gain, kp: preset.kp, ki: preset.ki, targets_c: targets,
    });
  };

  const canUseZero = Boolean(selected?.zero_rpm_supported && selected.fan_rpm != null
    && selected.power_w != null && selected.power_cap_w != null);
  const invalidTarget = Boolean(selected?.sensors.some((sensor) =>
    sensor.max_target_c < 45 || (draft?.targets_c[sensor.id] ?? sensor.default_target_c) > sensor.max_target_c));
  const canApply = Boolean(draft && (!draft.enabled || (selected?.curve_supported && data?.helper_installed
    && !invalidTarget && (!draft.allow_zero_rpm || canUseZero))));
  const save = async (settings: FanSettings) => {
    setSaving(true);
    try {
      const result = await api<FanStatus>("/system/fan-control", { method: "PUT", json: settings });
      setDraft(result.settings);
      qc.setQueryData(["system-fan-control"], result);
      show(result.settings.enabled ? "ファン設定を保存・適用しました" : "設定を保存しGPU自動制御に戻しました");
    } catch (e) {
      show(e instanceof Error ? e.message : "設定を保存できません", "error");
      void qc.invalidateQueries({ queryKey: ["system-fan-control"] });
    } finally {
      setSaving(false);
    }
  };

  return (
    <section aria-labelledby="fan-settings-title" className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900 md:p-5">
      <h2 id="fan-settings-title" className="text-sm font-semibold">ファンコントロール（AMD GPU）</h2>
      <p className="mt-2 text-xs leading-relaxed text-zinc-500">
        GPU本体・Hotspot・VRAMなど、実際に取得できる温度のうち余裕が最小のセンサーを優先します。
        ファン加速は早く、減速はゆっくり。短いLLM負荷のON/OFFではファンを細かく動かしません。
      </p>
      <p className="mt-2 text-xs text-amber-700 dark:text-amber-300">
        温度目標は保証された上限ではありません。過熱時の安全制御とGPU本来の温度保護は変更できません。
      </p>
      {isLoading && <p className="mt-3 text-xs text-zinc-500">GPU情報を読み取っています…</p>}
      {error && <p role="alert" className="mt-3 text-xs text-red-600">{error instanceof Error ? error.message : "取得失敗"}</p>}
      {data && draft && (
        <div className="mt-4 space-y-4">
          <div className={surface} role="status" aria-live="polite">
            <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
              <strong>{data.state === "controlling" ? "温度制御中" : data.state === "fan-stop"
                ? "Zero RPM許可中（GPUファームウェア）" : data.state === "fault"
                ? "制御異常・復帰処理中" : "標準自動制御"}</strong>
              <span className="tabular-nums">
                {data.active?.fan_rpm == null ? "RPM: N/A" : data.active.fan_rpm + " RPM"}
                {" / "}
                {data.active?.floor_percent == null ? "指令: PMFW任せ" : "指令: " + data.active.floor_percent + "%"}
              </span>
            </div>
            {data.active && <p className="mt-2 text-[11px] tabular-nums text-zinc-500">
              主センサー: {data.active.critical_sensor} / 昇温: {data.active.rise_c_per_min.toFixed(1)}°C/分 /
              電力(平滑): {data.active.power_ema_w == null ? "N/A" : data.active.power_ema_w + "W"}
            </p>}
            {data.active?.learning && <p className="mt-1 text-[11px] text-zinc-500 tabular-nums">
              学習中の係数 FF {data.active.learning.ff_gain.toFixed(1)} / P {data.active.learning.kp.toFixed(2)}
            </p>}
            {data.error && <p role="alert" className={`mt-2 text-xs ${data.state === "fault"
              ? "text-red-600" : "text-amber-700 dark:text-amber-300"}`}>{data.error}</p>}
          </div>

          <label className="block text-xs font-medium">
            対象GPU
            <select value={draft.bdf ?? ""} onChange={(e) => {
              const device = data.devices.find((item) => item.bdf === e.target.value);
              const preset = data.presets.find((item) => item.id === draft.preset_id);
              const targets: Record<string, number> = {};
              for (const sensor of device?.sensors ?? []) {
                targets[sensor.id] = Math.max(45, Math.min(sensor.max_target_c,
                  sensor.default_target_c + (preset?.target_offset_c ?? 0)));
              }
              update({ bdf: e.target.value || null, targets_c: targets }, false);
            }}
              className="mt-1 min-h-11 w-full rounded-xl border border-zinc-300 bg-white px-3 text-sm dark:border-zinc-700 dark:bg-zinc-950">
              <option value="">対象を選択</option>
              {data.devices.map((device) =>
                <option key={device.bdf} value={device.bdf}>
                  {device.bdf} / VRAM {(device.vram_bytes / 2 ** 30).toFixed(1)}GB / {device.curve_supported ? "対応" : "非対応"}
                </option>)}
            </select>
          </label>
          {selected && selected.restore_supported === false && <p className="text-xs text-amber-700 dark:text-amber-300">
            このGPUは標準の自動制御へ戻すとファン設定が壊れるため、無効化・異常時・ControlDeck停止時も最後のファンカーブ（最低回転＋60/77/95°Cの安全網）を保持します。標準の自動制御にはPCの再起動で戻ります。
          </p>}
          {!data.helper_installed && <p className="text-xs text-amber-700">固定権限helperが未登録です。 ./deck.sh service で登録してください。</p>}

          <div className="space-y-2">
            <h3 className="text-xs font-semibold">プリセットを選択</h3>
            <div className="grid gap-2 sm:grid-cols-3">
              {data.presets.map((preset) => (
                <button key={preset.id} type="button" disabled={saving} onClick={() => selectPreset(preset)}
                  aria-pressed={draft.preset_id === preset.id}
                  className={"min-h-20 rounded-xl border p-3 text-left transition-colors disabled:opacity-40 " +
                    (draft.preset_id === preset.id
                      ? "border-sky-500 bg-sky-50 dark:bg-sky-950/40"
                      : "border-zinc-200 dark:border-zinc-700")}>
                  <span className="block text-xs font-semibold">{preset.label}</span>
                  <span className="mt-1 block text-[11px] leading-relaxed text-zinc-500">{preset.description}</span>
                </button>
              ))}
            </div>
            {draft.preset_id === "custom" && <p className="text-[11px] text-zinc-500">カスタム調整中。推奨を選び直せば初期値へ戻せます。</p>}
            <p className="text-[11px] text-zinc-500">プリセットを選ぶだけではGPU設定は変わりません。「保存して適用」で確定します。</p>
          </div>

          {selected && (
            <div className="space-y-3">
              <h3 className="text-xs font-semibold">各温度センサーの目標値</h3>
              {selected.sensors.map((sensor) => {
                const goal = draft.targets_c[sensor.id] ?? sensor.default_target_c;
                return (
                  <div key={sensor.id} className="space-y-1">
                    <RangeControl label={sensor.label + " / " + sensor.id} min={45}
                      max={Math.max(45, sensor.max_target_c)} value={goal} suffix="°C"
                      hint={"現在 " + (sensor.value_c == null ? "N/A" : sensor.value_c + "°C") +
                        " ・ HW上限に基づく設定可能上限 " + sensor.max_target_c + "°C"}
                      onChange={(value) => update({ targets_c: { ...draft.targets_c, [sensor.id]: value } })}/>
                    {sensor.value_c != null && (
                      <div className="h-1.5 overflow-hidden rounded-full bg-zinc-100 dark:bg-zinc-800" aria-hidden>
                        <div className="h-full rounded-full bg-sky-600" style={{ width: Math.max(0, Math.min(100, sensor.value_c / Math.max(1, goal) * 100)) + "%" }}/>
                      </div>
                    )}
                  </div>
                );
              })}
              {!selected.sensors.length && <p className="text-xs text-amber-700">使用できるGPU温度センサーがありません。</p>}
            </div>
          )}

          <RangeControl label="アイドル時の最低ファン指令" value={draft.min_percent}
            min={Math.max(15, selected?.curve_limits?.pwm_min_percent ?? 15)} max={70} suffix="%" hint="通常は低速回転を維持し、温度が上がれば自動的に加速します。"
            onChange={(value) => update({ min_percent: value })}/>
          {selected && <FanCurvePreview device={selected} minimum={draft.min_percent}
            activeCurve={data.active?.bdf === selected.bdf ? data.active.curve : null}/>}

          <label className={"flex min-h-11 items-center justify-between gap-3 " + surface}>
            <span>
              <span className="block text-sm font-medium">低温安定時のファン停止（Zero RPM）</span>
              <span className="block text-[11px] leading-relaxed text-zinc-500">
                初期OFF。低温・低電力が長時間続いたときだけGPU標準機能へ停止を許可します。
                RPM・電力が取得できないGPUでは設定できません。
              </span>
            </span>
            <input type="checkbox" aria-label="Zero RPMを許可" checked={draft.allow_zero_rpm}
              disabled={saving || !canUseZero} onChange={(e) => update({ allow_zero_rpm: e.target.checked })}
              className="h-5 w-5 shrink-0"/>
          </label>

          <details className={surface}>
            <summary className="min-h-10 cursor-pointer text-xs font-semibold">詳細な制御パラメータ</summary>
            <div className="mt-3 space-y-3">
              <RangeControl label="減速前の保持時間" min={180} max={600} step={15}
                value={draft.cool_hold_seconds} suffix="秒" hint="高温から冷えた直後に速度を急に落としません。"
                onChange={(value) => update({ cool_hold_seconds: value })}/>
              <RangeControl label="減速指令の更新間隔" min={45} max={180} step={15}
                value={draft.cool_step_seconds} suffix="秒" hint="長いほどファン回転数の上下が減ります。"
                onChange={(value) => update({ cool_step_seconds: value })}/>
              <RangeControl label="1回あたりの最大減速幅" min={1} max={5}
                value={draft.cool_step_percent} suffix="%" hint="加速にはこの制限を適用しません。"
                onChange={(value) => update({ cool_step_percent: value })}/>
              <RangeControl label="Zero RPM停止判定の継続時間" min={600} max={1800} step={60}
                value={draft.zero_stable_seconds} suffix="秒" hint="最低10分。短いGPU負荷の谷では停止しません。"
                onChange={(value) => update({ zero_stable_seconds: value })}/>
              <label className="flex min-h-11 items-center justify-between gap-3 rounded-xl border border-zinc-200 p-3 text-xs dark:border-zinc-700">
                <span>
                  <span className="block font-semibold">運転データによる自動学習</span>
                  <span className="mt-1 block text-[11px] text-zinc-500">GPUに追加の負荷をかけず、熱応答から安全な範囲で係数を最適化します。</span>
                </span>
                <input type="checkbox" checked={draft.learning_enabled} aria-label="熱制御の自動学習"
                  onChange={(e) => update({ learning_enabled: e.target.checked })} className="h-5 w-5 shrink-0"/>
              </label>
              <RangeControl label="電力フィードフォワード係数" min={25} max={55} step={1}
                value={draft.ff_gain} hint="消費電力の増加をどの程度先読み加速に使うかの開始係数。"
                onChange={(value) => update({ ff_gain: value })}/>
              <RangeControl label="温度偏差の比例ゲイン（P）" min={1.8} max={3.2} step={0.05}
                value={draft.kp} hint="高温に近づいた時の追従性。上限は安全に固定されています。"
                onChange={(value) => update({ kp: value })}/>
              <RangeControl label="積分ゲイン（I）" min={0.005} max={0.03} step={0.005}
                value={draft.ki} hint="積分飽和防止（Anti-windup）は常に有効です。"
                onChange={(value) => update({ ki: value })}/>
              <p className="text-[11px] text-zinc-500">
                過熱時の最大ファン指令・温度上昇時の即時加速・センサー故障時の復帰動作は、安全機能のため変更できません。
              </p>
            </div>
          </details>

          <label className={"flex min-h-11 items-center justify-between gap-3 " + surface}>
            <span className="text-sm font-medium">ファン温度制御を有効にする（初期OFF）</span>
            <input type="checkbox" aria-label="温度制御を有効にする" checked={draft.enabled}
              onChange={(e) => update({ enabled: e.target.checked }, false)} className="h-5 w-5 shrink-0"/>
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" className={primaryButton}
              disabled={saving || !canApply}
              onClick={() => void save({ ...draft, bdf: selected?.bdf ?? draft.bdf })}>
              {saving ? "保存中…" : "保存して適用"}
            </button>
            <button type="button" disabled={saving || !modified}
              onClick={() => setDraft(data.settings)}
              className="min-h-11 rounded-xl border border-zinc-300 px-4 py-2 text-sm dark:border-zinc-700 disabled:opacity-40">
              変更を破棄
            </button>
            <button type="button" disabled={saving || (!data.settings.enabled && data.state !== "fault")}
              onClick={() => void save({ ...draft, enabled: false, bdf: data.settings.bdf })}
              className="min-h-11 rounded-xl border border-zinc-300 px-4 py-2 text-sm dark:border-zinc-700 disabled:opacity-40">
              GPU標準制御へ戻す
            </button>
            {modified && <span className="text-[11px] text-amber-700 dark:text-amber-300">未保存の変更があります</span>}
          </div>
          <p className="text-[11px] leading-relaxed text-zinc-500">
            外部ファン制御アプリ・任意のsysfs書き込みは利用しません。VRAM温度が非公開ならVRAMの閉ループ制御はできません。
            設定はControlDeckに保存され、センサー不足やファン異常時はGPU標準制御への復帰を試みます。
          </p>
        </div>
      )}
    </section>
  );
}
