import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { useToasts } from "../../stores";

export type PidInfo = {
  model: {
    idle_c: Record<string, number>;
    idle_power_w: number | null;
    points: { fan: number; power_w: number; r: Record<string, number> }[];
    tau_s: number;
    gain_c_per_percent: number | null;
    tuned: { kp: number; ti: number; tau_s: number; gain_c_per_percent: number; points: number } | null;
    usable: boolean;
  } | null;
  autotune: {
    phase: "off" | "waiting_idle" | "waiting_load" | "settling" | "done" | "aborted";
    level_percent: number | null;
    points: { fan: number; temps: Record<string, number>; settled: boolean }[];
    message: string;
    result: { kp: number; ti: number; tau_s: number } | null;
    elapsed_s: number | null;
  };
};

export type PidLive = {
  output_percent: number;
  feedforward_percent: number;
  p_percent: number;
  i_percent: number;
  error_c: number;
  governing_sensor: string;
  override: boolean;
  tune_hold_percent: number | null;
};

type Sample = {
  t: number;
  temps: Record<string, number | null>;
  smoothed_temps?: Record<string, number | null>;
  targets: Record<string, number>;
  fan_percent: number | null;
  rpm: number | null;
  power_w: number | null;
};

const SERIES = [
  { kind: "junction", label: "hotspot", color: "#e11d48" },
  { kind: "mem", label: "mem", color: "#d97706" },
  { kind: "edge", label: "edge", color: "#0284c7" },
] as const;
const RANGES = [10, 30, 60] as const;

function usePageVisible() {
  const [visible, setVisible] = useState(() => typeof document === "undefined" || document.visibilityState === "visible");
  useEffect(() => {
    const onChange = () => setVisible(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", onChange);
    return () => document.removeEventListener("visibilitychange", onChange);
  }, []);
  return visible;
}

/** 温度とファン回転の履歴。記録はサーバーが常時行い、ここでは表示中だけ取得・描画する。 */
export function FanHistoryChart() {
  const visible = usePageVisible();
  const [minutes, setMinutes] = useState<(typeof RANGES)[number]>(10);
  const [showRaw, setShowRaw] = useState(true);
  const samples = useRef<Sample[]>([]);
  const [, force] = useState(0);
  const since = useRef(0);
  useQuery({
    queryKey: ["system-fan-history"],
    enabled: visible,
    refetchInterval: visible ? 3000 : false,
    queryFn: async () => {
      const result = await api<{ samples: Sample[] }>(`/system/fan-control/history?since=${since.current}`);
      if (result.samples.length) {
        samples.current = [...samples.current, ...result.samples].slice(-1200);
        since.current = samples.current[samples.current.length - 1].t;
        force((n) => n + 1);
      }
      return result.samples.length;
    },
  });
  if (!visible) return null;
  const end = samples.current.length ? samples.current[samples.current.length - 1].t : Date.now() / 1000;
  const start = end - minutes * 60;
  const shown = samples.current.filter((s) => s.t >= start);
  const W = 340, H = 170, L = 30, R = 310, T = 10, B = 140;
  const x = (t: number) => L + (R - L) * ((t - start) / (end - start || 1));
  const yT = (c: number) => B - (B - T) * ((Math.min(105, Math.max(25, c)) - 25) / 80);
  const yP = (p: number) => B - (B - T) * (Math.min(100, Math.max(0, p)) / 100);
  const line = (pick: (s: Sample) => number | null | undefined, y: (v: number) => number) => {
    let d = "";
    let pen = false;
    for (const s of shown) {
      const v = pick(s);
      if (v == null) { pen = false; continue; }
      d += `${pen ? "L" : "M"}${x(s.t).toFixed(1)} ${y(v).toFixed(1)}`;
      pen = true;
    }
    return d;
  };
  const last = shown[shown.length - 1];
  return (
    <div className="rounded-xl border border-zinc-200 bg-zinc-50/60 p-3 dark:border-zinc-700 dark:bg-zinc-800/40">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-xs font-semibold">温度とファン回転の推移</h3>
        <div className="flex gap-1" role="group" aria-label="表示期間">
          <button type="button" aria-pressed={showRaw} onClick={() => setShowRaw((v) => !v)}
            className="min-h-8 rounded-lg border border-zinc-300 px-2 text-[11px] dark:border-zinc-600">実測線 {showRaw ? "ON" : "OFF"}</button>
          {RANGES.map((m) => (
            <button key={m} type="button" onClick={() => setMinutes(m)}
              className={"min-h-8 rounded-lg px-2 text-[11px] " + (m === minutes
                ? "bg-accent-600 text-white" : "border border-zinc-300 dark:border-zinc-600")}>
              {m}分
            </button>
          ))}
        </div>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="温度（左軸）とファン指令%（右軸）の推移" className="mt-2 w-full" style={{ maxHeight: 240 }}>
        {[30, 50, 70, 90].map((c) => (
          <g key={c}>
            <line x1={L} x2={R} y1={yT(c)} y2={yT(c)} stroke="currentColor" strokeOpacity=".12"/>
            <text x={L - 4} y={yT(c) + 3} fontSize="8" textAnchor="end" fill="currentColor" opacity=".7">{c}°</text>
          </g>
        ))}
        {[0, 50, 100].map((p) => (
          <text key={p} x={R + 4} y={yP(p) + 3} fontSize="8" fill="currentColor" opacity=".7">{p}%</text>
        ))}
        <path d={line((s) => s.fan_percent, yP)} fill="none" stroke="currentColor" strokeOpacity=".55" strokeWidth="1.6"/>
        <path d={line((s) => s.targets.junction, yT)} fill="none" stroke="#e11d48" strokeDasharray="4 3" strokeOpacity=".6"/>
        {SERIES.map((series) => (
          <g key={series.kind}>
            {showRaw && <path d={line((s) => s.temps[series.kind], yT)} fill="none" stroke={series.color}
              strokeWidth="0.8" strokeOpacity=".4" strokeDasharray="2 2"/>}
            <path d={line((s) => s.smoothed_temps?.[series.kind] ?? s.temps[series.kind], yT)}
              fill="none" stroke={series.color} strokeWidth="1.8"/>
          </g>
        ))}
        <text x={L} y={H - 14} fontSize="8" fill="currentColor" opacity=".7">-{minutes}分</text>
        <text x={R} y={H - 14} fontSize="8" textAnchor="end" fill="currentColor" opacity=".7">現在</text>
      </svg>
      <p className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-zinc-500">
        {SERIES.map((series) => (
          <span key={series.kind}><span style={{ color: series.color }}>━</span> {series.label}
            {last?.temps[series.kind] != null && ` ${Math.round(last.temps[series.kind] as number)}°C`}</span>
        ))}
        <span>温度: 太線EMA / {showRaw ? "細い点線は実測" : "実測線OFF"}</span>
        <span><span className="text-rose-600">┅</span> hotspot目標</span>
        <span>━ ファン指令{last?.fan_percent != null && ` ${last.fan_percent}%`}{last?.rpm != null && `（${last.rpm}rpm）`}</span>
      </p>
    </div>
  );
}

const PHASE_LABEL: Record<PidInfo["autotune"]["phase"], string> = {
  off: "未実行", waiting_idle: "アイドル温度を測定中", waiting_load: "負荷待ち",
  settling: "測定中", done: "完了", aborted: "中止",
};

export function PidStatus({ live }: { live: PidLive | null | undefined }) {
  if (!live) return null;
  return (
    <p className="mt-2 text-[11px] tabular-nums text-zinc-600 dark:text-zinc-300">
      PID出力 {live.output_percent.toFixed(1)}% ＝ FF {live.feedforward_percent.toFixed(1)} ＋ P {live.p_percent.toFixed(1)} ＋ I {live.i_percent.toFixed(1)}
      （偏差 {live.error_c > 0 ? "+" : ""}{live.error_c.toFixed(1)}°C）
      {live.override && <span className="ml-1 font-semibold text-red-600">目標+5°C超過のため最大</span>}
      {live.tune_hold_percent != null && <span className="ml-1 font-semibold text-accent-600">自動調整で{live.tune_hold_percent}%に固定中</span>}
    </p>
  );
}

export function AutoTuneCard({ pid, enabledPid }: { pid: PidInfo | undefined; enabledPid: boolean }) {
  const show = useToasts((state) => state.show);
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const tune = pid?.autotune;
  const running = tune && !["off", "done", "aborted"].includes(tune.phase);
  const act = async (action: "start" | "cancel") => {
    setBusy(true);
    try {
      await api("/system/fan-control/autotune", { method: "POST", json: { action } });
      await qc.invalidateQueries({ queryKey: ["system-fan-control"] });
    } catch (e) {
      show(e instanceof Error ? e.message : "自動調整を開始できません", "error");
    } finally {
      setBusy(false);
    }
  };
  const model = pid?.model;
  return (
    <div className="space-y-2 rounded-xl border border-zinc-200 p-3 text-xs dark:border-zinc-700">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-semibold">ゲインの自動調整</span>
        {running
          ? <button type="button" disabled={busy} onClick={() => act("cancel")} className="min-h-10 rounded-xl border border-zinc-300 px-3 dark:border-zinc-600">中止</button>
          : <button type="button" disabled={busy || !enabledPid} onClick={() => act("start")} className="min-h-10 rounded-xl bg-accent-600 px-3 font-semibold text-white disabled:opacity-40">開始</button>}
      </div>
      <p className="text-[11px] leading-relaxed text-zinc-500">
        アイドル温度を測ったあと、LLMの連続生成などの負荷中にファンを70%から10%ずつ下げ、各回転で温度が落ち着いた点を記録します。
        目標温度の3°C手前まで来たら終了し、熱モデル（フィードフォワード）とP・Iを設定します。負荷は自分では掛けません。負荷が止まると一時停止します。
        グラフ制御でも同じ熱モデルを使います。{!enabledPid && " 自動調整はPID制御を有効にして保存してから開始できます。"}
      </p>
      {tune && tune.phase !== "off" && (
        <p role="status" className="text-[11px]">
          <strong>{PHASE_LABEL[tune.phase]}</strong>：{tune.message}
          {tune.elapsed_s != null && `（${Math.floor(tune.elapsed_s / 60)}分${tune.elapsed_s % 60}秒）`}
        </p>
      )}
      {tune && tune.points.length > 0 && (
        <ul className="space-y-0.5 text-[11px] tabular-nums text-zinc-600 dark:text-zinc-300">
          {tune.points.map((p) => (
            <li key={p.fan}>{p.fan}% → {Object.entries(p.temps).map(([k, v]) => `${k} ${v}°C`).join(" / ")}{!p.settled && "（未収束）"}</li>
          ))}
        </ul>
      )}
      {model && (
        <p className="text-[11px] tabular-nums text-zinc-500">
          アイドル温度 {Object.values(model.idle_c).length ? Object.values(model.idle_c).map((v) => `${Math.round(v)}°C`).join(" / ") : "未測定"}
          {model.idle_power_w != null && `（${model.idle_power_w}W）`}・熱モデル {model.points.length}点
          {model.usable ? `・時定数 ${model.tau_s}秒` : "（自動調整前はFFなし・PIのみ）"}
          {model.tuned && `・調整結果 P ${model.tuned.kp} / Ti ${model.tuned.ti}秒`}
        </p>
      )}
    </div>
  );
}
