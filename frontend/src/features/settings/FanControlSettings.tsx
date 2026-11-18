/** GPU ファン制御の設定画面（v2）。
 *
 * 2026-11-17 利用者の依頼「設定が多すぎて分からない・入れ子・プリセットが謎」を受けて作り直した。
 * 方式は PID（電力の先読みつき）だけ（2026-11-18 実機で温度マップと比べ、温度が一定に保てたほうを採用）。
 * プリセットは無い。選べるのは「この制御」か「GPU 標準（デフォルト）」だけ。利用者が決めるのは上限温度・
 * マージン・最低回転・なめらかさ・起動時の動作で、PID の効き具合は自動調整が決める（手でも直せる）。
 * 入れ子（開閉する詳細）は作らない。上から「今の状態 → 推移 → 設定 → 自動調整」と並べる。
 */
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { getLlamaCapacity } from "../../api/models";
import { useToasts } from "../../stores";
import { ConfirmDialog, primaryButtonClass, secondaryButtonClass } from "../../components/ui";
import { FanHistoryChart, SENSOR_KINDS, type SensorKind } from "./FanCharts";

type FanSettings = {
  enabled: boolean;
  bdf: string | null;
  limits_c: Record<string, number>;
  margin_c: number;
  min_percent: number;
  smoothing_s: number;
  pid_kp: number;
  pid_ti: number;
  startup_mode: "apply_saved" | "firmware";
};

type Sensor = {
  id: string;
  label: string;
  kind: SensorKind;
  value_c: number | null;
  crit_c: number | null;
  max_limit_c: number;
  default_limit_c: number;
  limit_c?: number;
  target_c?: number;
  headroom_c?: number;
};

type FanDevice = {
  bdf: string;
  vram_bytes: number;
  sensors: Sensor[];
  fan_rpm: number | null;
  curve_supported: boolean;
  power_w: number | null;
  power_cap_w: number | null;
  restore_supported?: boolean;
  curve_limits: { temp_min_c: number; temp_max_c: number; pwm_min_percent: number; pwm_max_percent: number } | null;
};

type Control = {
  output_percent: number;
  governing_sensor: string;
  error_c: number;
  feedforward_percent: number;
  floor_percent?: number;
  reserve_c?: number;
  p_percent: number;
  i_percent: number;
  emergency: boolean;
  pv_c: Record<string, number>;
};

type TunePhase = "off" | "idle" | "jump" | "stairs" | "verify" | "done" | "aborted";
type AutoTune = {
  phase: TunePhase;
  message: string;
  running: boolean;
  elapsed_s: number | null;
  hold_percent: number | null;
  llm?: string | null;
  tokens?: number | null;
  result: {
    pid_kp: number; pid_ti: number; smoothing_s: number; margin_c: number;
  } | null;
};

type FanStatus = {
  settings: FanSettings;
  devices: FanDevice[];
  helper_installed: boolean;
  state: "disabled" | "controlling" | "fault";
  error: string;
  pending_auto_restore: boolean;
  active: {
    bdf: string;
    sensors: Sensor[];
    fan_rpm: number | null;
    fan_percent: number;
    power_w: number | null;
    governing_sensor: string;
    control: Control;
  } | null;
  poll_interval_seconds: number;
  model: { usable: boolean } | null;
  autotune: AutoTune;
  warning: string;
};

const KIND_ORDER: SensorKind[] = ["junction", "mem", "edge"];
const kindLabel = (kind: SensorKind) => SENSOR_KINDS.find((k) => k.kind === kind)?.label ?? kind;
const sortSensors = (sensors: Sensor[]) =>
  [...sensors].sort((a, b) => KIND_ORDER.indexOf(a.kind) - KIND_ORDER.indexOf(b.kind));

const card = "rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900 md:p-5";
const muted = "text-[11px] leading-relaxed text-zinc-500";
const inputClass = "min-h-11 w-20 rounded-xl border border-zinc-300 bg-white px-2 text-right text-base tabular-nums outline-none focus:border-accent-500 disabled:opacity-40 dark:border-zinc-700 dark:bg-zinc-950 sm:text-sm";

/** 数値の入力。打っている途中（「7.」など）を消さないよう文字列で持ち、範囲内になったら反映する。 */
function NumberInput({ value, min, max, step, label, onChange, disabled }: {
  value: number; min: number; max: number; step: number; label: string;
  onChange: (value: number) => void; disabled?: boolean;
}) {
  const [text, setText] = useState(String(value));
  useEffect(() => {
    setText((current) => (Number(current) === value && current !== "" ? current : String(value)));
  }, [value]);
  return (
    <input type="number" inputMode="decimal" min={min} max={max} step={step} value={text} disabled={disabled}
      aria-label={label} className={inputClass}
      onChange={(e) => {
        setText(e.target.value);
        const next = Number(e.target.value);
        if (e.target.value !== "" && Number.isFinite(next) && next >= min && next <= max) onChange(next);
      }}
      onBlur={() => setText(String(value))} />
  );
}

/** 1 つの値: 名前・数値・単位・（あれば）右側の補足、その下にスライダーと説明。枠で囲まない。 */
function Field({ label, value, min, max, step = 1, unit, aside, hint, onChange }: {
  label: string; value: number; min: number; max: number; step?: number; unit: string;
  aside?: ReactNode; hint?: ReactNode; onChange: (value: number) => void;
}) {
  const id = useId();
  const safe = Math.max(min, Math.min(max, value));
  return (
    <div className="py-3">
      <div className="flex items-center justify-between gap-3">
        <label htmlFor={id} className="min-w-0 text-sm font-medium">{label}</label>
        <span className="flex shrink-0 items-center gap-1.5 text-sm tabular-nums">
          <NumberInput value={value} min={min} max={max} step={step} label={label} onChange={onChange} />
          <span className="w-6 text-xs text-zinc-500">{unit}</span>
        </span>
      </div>
      <div className="flex items-center gap-3">
        <input id={id} type="range" min={min} max={max} step={step} value={safe}
          onChange={(e) => onChange(Number(e.target.value))} aria-label={label + "（スライダー）"}
          className="h-11 min-w-0 flex-1 accent-accent-600" />
        {aside && <span className="shrink-0 text-xs tabular-nums text-zinc-600 dark:text-zinc-300">{aside}</span>}
      </div>
      {hint && <p className={muted}>{hint}</p>}
    </div>
  );
}

/** 大きな 2 択（起動時の動作）。各選択肢に 1 行の説明を付ける。 */
function Choice<T extends string>({ name, value, options, onChange }: {
  name: string; value: T; options: ReadonlyArray<{ id: T; label: string; hint: string }>; onChange: (value: T) => void;
}) {
  return (
    <div role="radiogroup" aria-label={name} className="grid grid-cols-2 gap-1 rounded-xl bg-zinc-100 p-1 dark:bg-zinc-800/80">
      {options.map((option) => {
        const on = option.id === value;
        return (
          <button key={option.id} type="button" role="radio" aria-checked={on} onClick={() => onChange(option.id)}
            className={"min-h-11 min-w-0 rounded-lg px-2 py-2 text-left transition " + (on
              ? "bg-white text-zinc-900 shadow-sm ring-1 ring-accent-500/50 dark:bg-zinc-950 dark:text-zinc-50"
              : "text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100")}>
            <span className="block text-sm font-semibold leading-5">{option.label}</span>
            <span className="mt-0.5 block text-[11px] leading-snug text-zinc-500">{option.hint}</span>
          </button>
        );
      })}
    </div>
  );
}

function Switch({ checked, label, onChange, disabled }: {
  checked: boolean; label: string; onChange: (value: boolean) => void; disabled?: boolean;
}) {
  return (
    <button type="button" role="switch" aria-checked={checked} aria-label={label} disabled={disabled}
      onClick={() => onChange(!checked)}
      className="grid min-h-11 min-w-14 shrink-0 place-items-center disabled:opacity-40">
      <span className={"relative h-7 w-12 rounded-full transition-colors " + (checked ? "bg-accent-600" : "bg-zinc-300 dark:bg-zinc-700")}>
        <span className={"absolute top-0.5 h-6 w-6 rounded-full bg-white shadow transition-[left] " + (checked ? "left-[22px]" : "left-0.5")} />
      </span>
    </button>
  );
}

/** センサー 1 つ: 今の温度のバーに、目標（灰の線）と上限（赤の線）を重ねる。 */
function SensorBar({ sensor, limit, target }: { sensor: Sensor; limit: number; target: number }) {
  const lo = 30, hi = Math.max(105, limit + 5);
  const pos = (c: number) => `${Math.max(0, Math.min(100, ((c - lo) / (hi - lo)) * 100))}%`;
  const v = sensor.value_c;
  const tone = v == null ? "bg-zinc-300" : v >= limit ? "bg-red-600" : v > target ? "bg-amber-500" : "bg-accent-600 dark:bg-accent-500";
  return (
    <li className="py-1.5">
      <div className="flex items-baseline justify-between gap-2 text-xs tabular-nums">
        <span className="font-medium">{kindLabel(sensor.kind)}</span>
        <span>
          <strong className="text-base font-semibold">{v == null ? "N/A" : `${v.toFixed(0)}°C`}</strong>
          <span className="ml-2 text-zinc-500">目標 {target}・上限 {limit}</span>
        </span>
      </div>
      <div className="relative mt-1 h-2.5 rounded-full bg-zinc-100 dark:bg-zinc-800"
        role="meter" aria-label={`${kindLabel(sensor.kind)} の温度`} aria-valuemin={lo} aria-valuemax={hi}
        aria-valuenow={v ?? undefined} aria-valuetext={v == null ? "取得できません" : `${v}°C（目標 ${target}°C・上限 ${limit}°C）`}>
        {v != null && <div className={`h-full rounded-full ${tone}`} style={{ width: pos(v) }} />}
        <span className="absolute -top-0.5 h-3.5 w-0.5 rounded bg-zinc-500 dark:bg-zinc-300" style={{ left: pos(target) }} aria-hidden />
        <span className="absolute -top-0.5 h-3.5 w-0.5 rounded bg-red-600" style={{ left: pos(limit) }} aria-hidden />
      </div>
    </li>
  );
}

const TUNE_STEPS: ReadonlyArray<{ phase: TunePhase; label: string; hint: string }> = [
  { phase: "idle", label: "アイドル", hint: "負荷なしの温度と電力" },
  { phase: "jump", label: "跳ね", hint: "負荷を入れた瞬間の上がり方" },
  { phase: "stairs", label: "階段", hint: "回転を下げながら温度の落ち着き先" },
  { phase: "verify", label: "往復で確認", hint: "0%↔100% の負荷で上限に近づかないか" },
];

type LogSummary = {
  minutes: number; deviations: number; deviation_seconds: number; emergency_minutes: number;
  peak_c: { hot?: number | null; mem?: number | null };
};

/** 診断の記録（1 分ごと・直近 2 日）から: 逸脱の数と、記録からの作り直し（数秒、GPU は使わない）。
 *  2026-11-18 利用者の依頼「PID が逸脱した際に毎回長時間調整するのが面倒」。 */
function RefitSection({ enabled }: { enabled: boolean }) {
  const show = useToasts((state) => state.show);
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const { data: summary } = useQuery({
    queryKey: ["system-fan-log-summary"], enabled,
    queryFn: () => api<LogSummary>("/system/fan-control/log-summary?hours=24"), refetchInterval: 60_000,
  });
  const refit = async () => {
    setBusy(true);
    try {
      const result = await api<{ pid_kp: number; pid_ti: number; changes: { points: { fan: number }[] } }>(
        "/system/fan-control/refit", { method: "POST" });
      show(`記録から作り直しました（Kp ${result.pid_kp}・Ti ${result.pid_ti} 秒・測れた回転 ${result.changes.points.map((p) => `${p.fan}%`).join("・")}）`);
      await qc.invalidateQueries({ queryKey: ["system-fan-control"] });
    } catch (e) {
      show(e instanceof Error ? e.message : "記録から作り直せません", "error");
    } finally {
      setBusy(false);
    }
  };
  const warn = summary && (summary.emergency_minutes > 0 || summary.deviations >= 3);
  return (
    <section aria-labelledby="fan-refit-title" className={card}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id="fan-refit-title" className="text-sm font-semibold">記録から作り直す</h3>
        <button type="button" disabled={busy || !enabled} onClick={() => void refit()} className={primaryButtonClass}>
          {busy ? "作り直しています…" : "記録から作り直す"}
        </button>
      </div>
      <p className={"mt-1 " + muted}>
        制御中の温度・電力・回転を 1 分ごとに記録しています（直近 2 日）。目標から外れるようになったら、
        この記録の「全力で落ち着いていた区間」と「休みの区間」から熱の特性と効き具合を数秒で作り直せます。GPU は使いません。
      </p>
      {summary && (
        <p role="status" className={"mt-2 text-xs tabular-nums " + (warn ? "text-amber-700 dark:text-amber-300" : "text-zinc-600 dark:text-zinc-300")}>
          直近 24 時間（記録 {summary.minutes} 分）: 目標超え {summary.deviations} 回・緊急の最大回転 {summary.emergency_minutes} 分・
          hotspot 最高 {summary.peak_c.hot ?? "—"}°C
          {warn && "。作り直しをおすすめします"}
        </p>
      )}
    </section>
  );
}

function duration(seconds: number) {
  const m = Math.floor(seconds / 60);
  return m >= 60 ? `${Math.floor(m / 60)}時間${m % 60}分` : `${m}分${seconds % 60}秒`;
}

function AutoTuneSection({ tune, canStart, startHint }: {
  tune: AutoTune; canStart: boolean; startHint: string;
}) {
  const show = useToasts((state) => state.show);
  const qc = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [llm, setLlm] = useState("");
  // 調整に使う LLM の候補（起動中のもの）。取れなければ選ばせず、サーバーの既定（登録済みの最初の LLM）に任せる
  const { data: capacity } = useQuery({
    queryKey: ["llama-capacity"], queryFn: getLlamaCapacity, enabled: confirming, retry: false,
  });
  const running = [...new Set([
    ...(capacity?.endpoints ?? []).map((e) => e.running_alias),
    capacity?.last_local?.running_alias,
  ].filter((alias): alias is string => Boolean(alias)))];

  const act = async (action: "start" | "cancel") => {
    setBusy(true);
    try {
      await api("/system/fan-control/autotune", {
        method: "POST", json: action === "start" && llm ? { action, llm } : { action },
      });
      await qc.invalidateQueries({ queryKey: ["system-fan-control"] });
      if (action === "cancel") show("自動調整を中止しました");
    } catch (e) {
      show(e instanceof Error ? e.message : "自動調整を開始できません", "error");
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  };
  const current = TUNE_STEPS.findIndex((step) => step.phase === tune.phase);
  const result = tune.phase === "done" ? tune.result : null;
  return (
    <section aria-labelledby="fan-tune-title" className={card}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id="fan-tune-title" className="text-sm font-semibold">自動調整</h3>
        {tune.running
          ? <button type="button" disabled={busy} onClick={() => void act("cancel")} className={secondaryButtonClass}>中止</button>
          : <button type="button" disabled={busy || !canStart} onClick={() => setConfirming(true)} className={primaryButtonClass}>
            {busy ? "LLM を準備しています…" : tune.phase === "done" ? "もう一度調整する" : "自動調整を始める"}
          </button>}
      </div>
      <p className={"mt-1 " + muted}>
        記録が無いとき（初めて・GPU や冷却を変えたとき）に使います。PID の効き具合（Kp・Ti）とマージンを、この PC で実際に測って決めます。LLM に長文を書かせ続けて
        GPU を全力にし、回転を変えながら温度の動きを測ります。終わると結果を保存します（そのまま使えます）。
        {!canStart && !tune.running && <span className="mt-1 block text-amber-700 dark:text-amber-300">{startHint}</span>}
      </p>

      {(tune.running || current >= 0) && (
        <ol className="mt-3 grid grid-cols-4 gap-1" aria-label="自動調整の段階">
          {TUNE_STEPS.map((step, index) => {
            const done = index < current;
            const now = index === current;
            return (
              <li key={step.phase} aria-current={now ? "step" : undefined} title={step.hint}
                className={"rounded-lg px-1 py-1.5 text-center text-[11px] leading-tight " + (now
                  ? "bg-accent-600 font-semibold text-white"
                  : done ? "bg-accent-100 text-accent-800 dark:bg-accent-950 dark:text-accent-200"
                    : "bg-zinc-100 text-zinc-500 dark:bg-zinc-800")}>
                {done && "✓ "}{step.label}
              </li>
            );
          })}
        </ol>
      )}
      {tune.running && (
        <div role="status" className="mt-2 space-y-0.5 text-xs tabular-nums">
          {tune.llm && <p className="font-semibold">{tune.llm} で調整中</p>}
          <p>{tune.message}</p>
          <p className="text-zinc-500">
            {tune.elapsed_s != null && `経過 ${duration(tune.elapsed_s)}`}
            {tune.hold_percent != null && `・回転を ${Math.round(tune.hold_percent)}% に固定中`}
            {tune.tokens != null && `・生成 ${tune.tokens.toLocaleString()} トークン`}
          </p>
        </div>
      )}
      {tune.phase === "aborted" && (
        <p role="status" className="mt-2 text-xs text-amber-700 dark:text-amber-300">中止: {tune.message}</p>
      )}
      {result && (
        <div className="mt-3" role="status">
          <p className="text-xs font-semibold">結果（保存済み）</p>
          <dl className="mt-1 grid grid-cols-2 gap-x-4 gap-y-1 text-xs tabular-nums sm:grid-cols-4">
            <div><dt className="text-zinc-500">Kp</dt><dd className="font-semibold">{result.pid_kp} %/°C</dd></div>
            <div><dt className="text-zinc-500">Ti</dt><dd className="font-semibold">{result.pid_ti} 秒</dd></div>
            <div><dt className="text-zinc-500">なめらかさ</dt><dd className="font-semibold">{result.smoothing_s} 秒</dd></div>
            <div><dt className="text-zinc-500">マージン</dt><dd className="font-semibold">{result.margin_c}°C</dd></div>
          </dl>
        </div>
      )}
      {confirming && (
        <ConfirmDialog title="自動調整を始めますか" confirmLabel="始める" danger={false} busy={busy}
          message="LLM に長文を書かせ続けて GPU を 30〜90 分使います。その間 LLM は使えません。途中で中止できます（中止すると元の設定に戻ります）。"
          onConfirm={() => void act("start")} onClose={() => setConfirming(false)}>
          {running.length > 0 && (
            <label className="mt-3 block text-xs font-medium">
              負荷に使う LLM
              <select value={llm} onChange={(e) => setLlm(e.target.value)} aria-label="負荷に使う LLM"
                className="mt-1 min-h-11 w-full rounded-xl border border-zinc-300 bg-white px-3 text-base dark:border-zinc-700 dark:bg-zinc-950 sm:text-sm">
                <option value="">おまかせ（登録済みの最初の LLM）</option>
                {running.map((alias) => <option key={alias} value={alias}>{alias}（起動中）</option>)}
              </select>
            </label>
          )}
        </ConfirmDialog>
      )}
    </section>
  );
}

export function FanControlSettings() {
  const show = useToasts((state) => state.show);
  const qc = useQueryClient();
  const [draft, setDraft] = useState<FanSettings | null>(null);
  // draft の元になったサーバーの設定。手を付けていなければ、自動調整などで変わった値へ追従する
  const base = useRef<string>("");
  const [saving, setSaving] = useState(false);
  const { data, error, isLoading } = useQuery({
    queryKey: ["system-fan-control"],
    queryFn: () => api<FanStatus>("/system/fan-control"),
    refetchInterval: 5000,
  });
  useEffect(() => {
    if (!data) return;
    const incoming = JSON.stringify(data.settings);
    setDraft((current) => {
      if (current === null || JSON.stringify(current) === base.current) {
        base.current = incoming;
        return data.settings;
      }
      return current;
    });
  }, [data]);

  if (isLoading) return <section className={card}><p className="text-xs text-zinc-500">GPU情報を読み取っています…</p></section>;
  if (error || !data || !draft) {
    return (
      <section className={card}>
        <p role="alert" className="text-xs text-red-600">{error instanceof Error ? error.message : "ファン制御の状態を取得できません"}</p>
      </section>
    );
  }

  const supported = data.devices.filter((d) => d.curve_supported);
  const selected = data.devices.find((d) => d.bdf === draft.bdf) ?? (supported.length === 1 ? supported[0] : undefined);
  const sensors = sortSensors(selected?.sensors ?? []);
  const liveSensors = new Map((data.active?.sensors ?? []).map((s) => [s.id, s]));
  const update = (patch: Partial<FanSettings>) => setDraft((current) => (current ? { ...current, ...patch } : current));
  const limitOf = (s: Sensor, settings: FanSettings) => settings.limits_c[s.id] ?? s.default_limit_c;
  const minFloor = Math.max(15, selected?.curve_limits?.pwm_min_percent ?? 15);

  const modified = JSON.stringify(draft) !== base.current;
  const limitError = sensors.map((s) => {
    const limit = limitOf(s, draft);
    if (limit < 50 || limit > s.max_limit_c) return `${kindLabel(s.kind)}の上限は 50〜${s.max_limit_c}°C にしてください`;
    if (limit - draft.margin_c < 40) return `${kindLabel(s.kind)}の目標（上限 − マージン）が低すぎます`;
    return "";
  }).find(Boolean) ?? "";
  const blocker = !draft.enabled ? "" : !data.helper_installed ? "helper が未登録のため有効にできません"
    : !selected ? "対象の GPU を選んでください" : !selected.curve_supported ? "この GPU はファン制御に対応していません"
      : limitError;

  const save = async () => {
    setSaving(true);
    const body: FanSettings = { ...draft, bdf: selected?.bdf ?? draft.bdf };
    try {
      const result = await api<FanStatus>("/system/fan-control", { method: "PUT", json: body });
      base.current = JSON.stringify(result.settings);
      setDraft(result.settings);
      qc.setQueryData(["system-fan-control"], result);
      show(result.settings.enabled ? "ファン設定を保存・適用しました" : "設定を保存しGPU標準の自動制御に戻しました");
    } catch (e) {
      show(e instanceof Error ? e.message : "設定を保存できません", "error");
      void qc.invalidateQueries({ queryKey: ["system-fan-control"] });
    } finally {
      setSaving(false);
    }
  };
  const discard = () => {
    base.current = JSON.stringify(data.settings);
    setDraft(data.settings);
  };

  const tune = data.autotune;
  const control = data.active?.control;
  const emergency = Boolean(control?.emergency);
  const stateLabel = data.state === "controlling" ? "制御中" : data.state === "fault" ? "制御異常・復帰処理中" : "GPU標準の自動制御";
  const stateTone = data.state === "controlling"
    ? "bg-accent-50 text-accent-700 dark:bg-accent-950 dark:text-accent-300"
    : data.state === "fault" ? "bg-red-50 text-red-700 dark:bg-red-950/60 dark:text-red-300"
      : "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300";
  const fanRpm = data.active?.fan_rpm ?? selected?.fan_rpm ?? null;
  const power = data.active?.power_w ?? selected?.power_w ?? null;
  const governing = data.active ? sensors.find((s) => s.id === data.active!.governing_sensor) : undefined;

  return (
    <div className="space-y-4">
      {/* ---- 今の状態 ---- */}
      <section aria-labelledby="fan-settings-title" className={card}>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 id="fan-settings-title" className="text-sm font-semibold">ファン制御（AMD GPU）</h2>
            <p className="mt-1 flex flex-wrap items-center gap-1.5" role="status" aria-live="polite">
              <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${stateTone}`}>{stateLabel}</span>
              {emergency && <span className="rounded-full bg-red-600 px-2 py-0.5 text-[11px] font-semibold text-white">⚠ 上限超え・最大回転中</span>}
              {tune.running && <span className="rounded-full bg-accent-600 px-2 py-0.5 text-[11px] font-medium text-white">自動調整中</span>}
            </p>
          </div>
          <Switch checked={draft.enabled} label="ファン制御を有効にする" onChange={(enabled) => update({ enabled })}
            disabled={saving || (draft.enabled === false && !selected?.curve_supported)} />
        </div>

        {!data.helper_installed && <p className="mt-3 text-xs text-amber-700 dark:text-amber-300">固定権限helperが未登録です。 ./deck.sh service で登録してください。</p>}
        {data.devices.length === 0 && <p className="mt-3 text-xs text-amber-700 dark:text-amber-300">AMD GPU が見つかりません。</p>}
        {data.devices.length > 0 && supported.length === 0 && (
          <p className="mt-3 text-xs text-amber-700 dark:text-amber-300">ファン制御に対応した AMD GPU がありません（GPUドライバが安全な5点ファンカーブを公開していません）。</p>
        )}
        {selected && !sensors.length && <p className="mt-3 text-xs text-amber-700 dark:text-amber-300">使用できるGPU温度センサーがありません。</p>}
        {data.error && <p role="alert" className={"mt-3 text-xs " + (data.state === "fault" ? "text-red-600 dark:text-red-400" : "text-amber-700 dark:text-amber-300")}>{data.error}</p>}
        {data.pending_auto_restore && <p className="mt-2 text-xs text-amber-700 dark:text-amber-300">GPU標準の自動制御への復帰を再試行しています。</p>}
        {selected?.restore_supported === false && (
          <p className={"mt-2 " + muted}>
            このGPUは標準の自動制御へ戻すとファン設定が壊れるため、無効化・異常時・ControlDeck停止時も最後のファンカーブ（上限温度で最大回転）を保持します。標準の自動制御にはPCの再起動で戻ります。
          </p>
        )}

        <dl className="mt-4 grid grid-cols-3 gap-2 text-center tabular-nums">
          <div className="rounded-xl bg-zinc-50 px-1 py-2 dark:bg-zinc-800/50">
            <dt className="text-[11px] text-zinc-500">ファン</dt>
            <dd className="text-lg font-semibold leading-6">{data.active ? `${data.active.fan_percent}%` : "—"}</dd>
            <dd className="text-[11px] text-zinc-500">{fanRpm == null ? "rpm N/A" : `${fanRpm.toLocaleString()} rpm`}</dd>
          </div>
          <div className="rounded-xl bg-zinc-50 px-1 py-2 dark:bg-zinc-800/50">
            <dt className="text-[11px] text-zinc-500">GPU電力</dt>
            <dd className="text-lg font-semibold leading-6">{power == null ? "N/A" : `${Math.round(power)} W`}</dd>
            <dd className="text-[11px] text-zinc-500">{selected?.power_cap_w ? `上限 ${Math.round(selected.power_cap_w)} W` : " "}</dd>
          </div>
          <div className="rounded-xl bg-zinc-50 px-1 py-2 dark:bg-zinc-800/50">
            <dt className="text-[11px] text-zinc-500">基準のセンサー</dt>
            <dd className="text-lg font-semibold leading-6">{governing ? kindLabel(governing.kind) : "—"}</dd>
            <dd className="text-[11px] text-zinc-500">{control ? `目標との差 ${control.error_c > 0 ? "+" : ""}${control.error_c.toFixed(1)}°C` : " "}</dd>
          </div>
        </dl>

        {sensors.length > 0 && (
          <ul className="mt-3" aria-label="センサーごとの温度">
            {sensors.map((s) => {
              const live = liveSensors.get(s.id);
              const limit = live?.limit_c ?? limitOf(s, data.settings);
              const target = live?.target_c ?? Math.round((limit - data.settings.margin_c) * 10) / 10;
              return <SensorBar key={s.id} sensor={live ?? s} limit={limit} target={target} />;
            })}
          </ul>
        )}
        {control && !emergency && (
          <p className={"mt-2 tabular-nums " + muted}>
            出力 {control.output_percent.toFixed(1)}% ＝ 先読み {control.feedforward_percent.toFixed(1)} ＋ P {control.p_percent.toFixed(1)} ＋ I {control.i_percent.toFixed(1)}
            {(control.floor_percent ?? 0) > 0 && (control.floor_percent ?? 0) > control.output_percent - 0.05 && (
              <>（負荷中の床 {control.floor_percent!.toFixed(1)}%）</>
            )}
            {(control.reserve_c ?? 0) >= 0.5 && <>（重い局面への余白 +{control.reserve_c!.toFixed(1)}°C）</>}
            （先読み＝電力から目標で釣り合う回転を見積もった分。負荷中は先読みより下げない。余白＝直近 3 分の最大電力に戻ったときの hotspot の跳ねを見込んだ分。I＝上乗せ: 先読みの不足分（外気温・季節）を覚えた分）
          </p>
        )}
        {emergency && <p className="mt-2 text-xs text-red-700 dark:text-red-300">上限温度を超えたため、なめらかさを無視して最大回転にしています。上限を下回ると通常の制御に戻ります。</p>}
      </section>

      {/* ---- 推移（制御中だけ記録される） ---- */}
      {data.settings.enabled && <FanHistoryChart />}

      {/* ---- 設定 ---- */}
      <section aria-labelledby="fan-config-title" className={card}>
        <h3 id="fan-config-title" className="text-sm font-semibold">設定</h3>
        {data.devices.length > 1 && (
          <label className="mt-3 block text-xs font-medium">
            対象GPU
            <select value={draft.bdf ?? ""} onChange={(e) => update({ bdf: e.target.value || null, limits_c: {} })}
              className="mt-1 min-h-11 w-full rounded-xl border border-zinc-300 bg-white px-3 text-base dark:border-zinc-700 dark:bg-zinc-950 sm:text-sm">
              <option value="">対象を選択</option>
              {data.devices.map((d) => (
                <option key={d.bdf} value={d.bdf}>{d.bdf} / VRAM {(d.vram_bytes / 2 ** 30).toFixed(1)}GB / {d.curve_supported ? "対応" : "非対応"}</option>
              ))}
            </select>
          </label>
        )}

        <div className="mt-4 divide-y divide-zinc-100 dark:divide-zinc-800">
          <div className="pb-1">
            <p className="text-sm font-medium">上限温度</p>
            <p className={muted}>超えたらすぐ最大回転にする温度。ふだんは「上限 − マージン」を目標に制御します。</p>
          </div>
          {sensors.map((s) => {
            const limit = limitOf(s, draft);
            return (
              <Field key={s.id} label={`${kindLabel(s.kind)}の上限`} value={limit} min={50} max={s.max_limit_c} unit="°C"
                aside={<>目標 <strong>{Math.round((limit - draft.margin_c) * 10) / 10}°C</strong></>}
                onChange={(value) => update({ limits_c: { ...draft.limits_c, [s.id]: Math.round(value) } })} />
            );
          })}
          <Field label="マージン" value={draft.margin_c} min={2} max={15} step={0.5} unit="°C"
            hint="上限そのものを目標にすると、負荷がかかった瞬間に必ず超えます。その分だけ手前を目標にします。"
            onChange={(margin_c) => update({ margin_c })} />
          <Field label="最低回転" value={draft.min_percent} min={minFloor} max={70} unit="%"
            hint="冷えていてもこれより下げません。"
            onChange={(min_percent) => update({ min_percent: Math.round(min_percent) })} />
          <Field label="なめらかさ" value={draft.smoothing_s} min={20} max={300} step={5} unit="秒"
            hint="回転を下げるときと、温度・電力が下がったと見るまでの遅さ。上げる側は温度の上昇をすぐ追います。上限を超えたときは関係なくすぐ最大です。"
            onChange={(smoothing_s) => update({ smoothing_s })} />

          <div className="py-3" data-testid="fan-pid-gains">
            <p className="text-sm font-medium">効き具合 <span className="text-[11px] font-normal text-zinc-500">（自動調整が決めます）</span></p>
            <p className={muted}>
              出力 ＝ 先読み（フィードフォワード）＋ Kp × 目標との差 ＋ 上乗せ（積分）。普段は触らなくて大丈夫です。手で直すこともできます。
            </p>
            <div className="mt-2 flex flex-wrap gap-x-5 gap-y-2 text-xs">
              <label className="flex items-center gap-1.5">Kp（比例）
                <NumberInput value={draft.pid_kp} min={0.3} max={10} step={0.01} label="Kp（比例）" onChange={(pid_kp) => update({ pid_kp })} />
                <span className="text-zinc-500">%/°C</span>
              </label>
              <label className="flex items-center gap-1.5">Ti（積分時間）
                <NumberInput value={draft.pid_ti} min={10} max={600} step={1} label="Ti（積分時間）" onChange={(pid_ti) => update({ pid_ti })} />
                <span className="text-zinc-500">秒</span>
              </label>
            </div>
          </div>

          <div className="py-3">
            <p className="mb-2 text-sm font-medium">PC起動時の動作</p>
            <Choice name="PC起動時の動作" value={draft.startup_mode} onChange={(startup_mode) => update({ startup_mode })} options={[
              { id: "apply_saved", label: "保存した設定で制御", hint: "ControlDeck が起動したら自動で制御を始める" },
              { id: "firmware", label: "GPU標準のまま（デフォルト）", hint: "起動後にファンカーブを書かない。GPU の標準の自動制御で動く" },
            ]} />
            <p className={"mt-1 " + muted}>ControlDeck だけを再起動したときは、どちらでも制御を続けます。</p>
          </div>
        </div>
        <p className={"mt-2 " + muted}>{data.warning}</p>
      </section>

      {/* ---- 記録から作り直す（ふだんはこちら） ---- */}
      <RefitSection enabled={data.settings.enabled && data.state === "controlling" && !tune.running} />

      {/* ---- 自動調整 ---- */}
      <AutoTuneSection tune={tune}
        canStart={data.settings.enabled && data.state === "controlling" && !modified}
        startHint={modified ? "変更を保存してから始められます。" : "ファン制御を有効にして保存してから始められます。"} />

      {/* ---- 保存（変更があるときだけ） ---- */}
      {modified && (
        <div className="sticky bottom-0 z-10 flex flex-wrap items-center gap-2 rounded-2xl border border-zinc-200 bg-white/95 px-3 pt-3 shadow-lg backdrop-blur pb-[max(0.75rem,env(safe-area-inset-bottom))] dark:border-zinc-700 dark:bg-zinc-900/95">
          <p className="min-w-0 flex-1 basis-full text-[11px] text-amber-700 dark:text-amber-300 sm:basis-auto">
            {blocker || "未保存の変更があります。保存するとすぐに反映されます。"}
          </p>
          <button type="button" onClick={discard} disabled={saving} className={secondaryButtonClass + " flex-1 sm:flex-none"}>変更を破棄</button>
          <button type="button" onClick={() => void save()} disabled={saving || Boolean(blocker)} className={primaryButtonClass + " flex-1 sm:flex-none"}>
            {saving ? "保存中…" : "保存して適用"}
          </button>
        </div>
      )}
    </div>
  );
}
