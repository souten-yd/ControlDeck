/** ファン制御の画面のグラフ 2 つ（直近の推移・温度マップ）。外部のグラフ部品は使わず SVG で描く。 */
import { useEffect, useRef, useState, type PointerEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";

export type SensorKind = "junction" | "mem" | "edge";

/** センサーの種類ごとの名前と色。色は種類に付く（順位や並びでは変えない）。 */
export const SENSOR_KINDS: ReadonlyArray<{ kind: SensorKind; label: string; line: string; swatch: string }> = [
  { kind: "junction", label: "hotspot", line: "stroke-[#2a78d6] dark:stroke-[#3987e5]", swatch: "bg-[#2a78d6] dark:bg-[#3987e5]" },
  { kind: "mem", label: "VRAM", line: "stroke-[#eb6834] dark:stroke-[#d95926]", swatch: "bg-[#eb6834] dark:bg-[#d95926]" },
  { kind: "edge", label: "GPU本体", line: "stroke-[#1baf7a] dark:stroke-[#199e70]", swatch: "bg-[#1baf7a] dark:bg-[#199e70]" },
];

type Sample = {
  t: number;
  temps: Partial<Record<SensorKind, number | null>>;
  smoothed_temps?: Partial<Record<SensorKind, number | null>>;
  limits?: Partial<Record<SensorKind, number>>;
  targets?: Partial<Record<SensorKind, number>>;
  fan_percent: number | null;
  rpm: number | null;
  power_w: number | null;
  feedforward_percent?: number | null;
  emergency?: boolean;
  tuning?: boolean;
};

const RANGES = [10, 30, 60] as const;

/** 画面が見えている間だけ true（裏に回ったら取りに行かない）。 */
export function usePageVisible() {
  const [visible, setVisible] = useState(() => typeof document === "undefined" || document.visibilityState === "visible");
  useEffect(() => {
    const onChange = () => setVisible(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", onChange);
    return () => document.removeEventListener("visibilitychange", onChange);
  }, []);
  return visible;
}

const W = 320;
const L = 30;
const R = 300;

function clock(t: number) {
  return new Date(t * 1000).toLocaleTimeString("ja-JP", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

/** 温度とファン回転の推移。記録はサーバーが常時行い、ここでは画面が見えている間だけ差分を取りに行く。
 *  温度とファン%は単位が違うので、軸を共有せず上下 2 段に分ける。 */
export function FanHistoryChart() {
  const visible = usePageVisible();
  const [minutes, setMinutes] = useState<(typeof RANGES)[number]>(10);
  const [hover, setHover] = useState<number | null>(null);
  const samples = useRef<Sample[]>([]);
  const since = useRef(0);
  const [, redraw] = useState(0);
  useQuery({
    queryKey: ["system-fan-history"],
    enabled: visible,
    refetchInterval: visible ? 3000 : false,
    queryFn: async () => {
      const result = await api<{ samples: Sample[] }>(`/system/fan-control/history?since=${since.current}`);
      if (result.samples.length) {
        samples.current = [...samples.current, ...result.samples].slice(-1200);
        since.current = samples.current[samples.current.length - 1].t;
        redraw((n) => n + 1);
      }
      return result.samples.length;
    },
  });

  const all = samples.current;
  const end = all.length ? all[all.length - 1].t : Date.now() / 1000;
  const start = end - minutes * 60;
  const shown = all.filter((s) => s.t >= start);
  const x = (t: number) => L + (R - L) * ((t - start) / (end - start || 1));

  // 温度の縦軸: 見えている値と hotspot の上限が入る範囲（10°C 刻み）
  const values: number[] = [];
  for (const s of shown) {
    for (const { kind } of SENSOR_KINDS) {
      const v = s.temps[kind];
      if (v != null) values.push(v);
    }
    if (s.limits?.junction != null) values.push(s.limits.junction);
  }
  const lo = values.length ? Math.floor((Math.min(...values) - 3) / 10) * 10 : 30;
  const hi = values.length ? Math.ceil((Math.max(...values) + 3) / 10) * 10 : 100;
  const TT = 8, TB = 128;
  const yT = (c: number) => TB - (TB - TT) * ((Math.min(hi, Math.max(lo, c)) - lo) / (hi - lo || 1));
  const FT = 6, FB = 60;
  const yF = (p: number) => FB - (FB - FT) * (Math.min(100, Math.max(0, p)) / 100);

  const path = (pick: (s: Sample) => number | null | undefined, y: (v: number) => number) => {
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
  const fanLine = path((s) => s.fan_percent, yF);
  const firstFan = shown.find((s) => s.fan_percent != null);
  const lastFan = [...shown].reverse().find((s) => s.fan_percent != null);
  const fanArea = fanLine && firstFan && lastFan
    ? `${fanLine}L${x(lastFan.t).toFixed(1)} ${FB}L${x(firstFan.t).toFixed(1)} ${FB}Z` : "";

  const pick = (e: PointerEvent<SVGSVGElement>) => {
    if (!shown.length) return;
    const box = e.currentTarget.getBoundingClientRect();
    const vx = ((e.clientX - box.left) / box.width) * W;
    const t = start + ((vx - L) / (R - L)) * (end - start);
    let best = 0;
    for (let i = 1; i < shown.length; i++) {
      if (Math.abs(shown[i].t - t) < Math.abs(shown[best].t - t)) best = i;
    }
    setHover(best);
  };
  const focus = hover != null && shown[hover] ? shown[hover] : shown[shown.length - 1];
  const limitNow = focus?.limits?.junction;
  const targetNow = focus?.targets?.junction;
  const handlers = {
    onPointerMove: pick, onPointerDown: pick,
    onPointerLeave: () => setHover(null),
  };
  const cross = hover != null && focus ? x(focus.t) : null;

  if (!visible) return null;
  return (
    <section aria-labelledby="fan-history-title" className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900 md:p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id="fan-history-title" className="text-sm font-semibold">直近の推移</h3>
        <div className="flex gap-1 rounded-xl bg-zinc-100 p-1 dark:bg-zinc-800/80" role="group" aria-label="表示する期間">
          {RANGES.map((m) => (
            <button key={m} type="button" aria-pressed={m === minutes} onClick={() => setMinutes(m)}
              className={"min-h-9 min-w-11 rounded-lg px-2 text-xs font-medium " + (m === minutes
                ? "bg-white text-zinc-900 shadow-sm dark:bg-zinc-950 dark:text-zinc-50"
                : "text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200")}>
              {m}分
            </button>
          ))}
        </div>
      </div>

      {/* 読み取り: 指でなぞった時刻（触っていなければ最新）の値 */}
      <p className="mt-2 min-h-10 text-[11px] leading-relaxed tabular-nums text-zinc-600 dark:text-zinc-300" aria-live="off">
        {focus ? <>
          <span className="text-zinc-400">{hover != null ? clock(focus.t) : "最新"}</span>{" "}
          {SENSOR_KINDS.map(({ kind, label, swatch }) => focus.temps[kind] != null && (
            <span key={kind} className="mr-2 inline-flex items-center gap-1">
              <span className={`inline-block h-2 w-2 rounded-full ${swatch}`} aria-hidden />
              {label} {(focus.smoothed_temps?.[kind] ?? focus.temps[kind])!.toFixed(1)}°C
              <span className="text-zinc-400">（実測 {focus.temps[kind]!.toFixed(0)}）</span>
            </span>
          ))}
          <span className="mr-2">ファン {focus.fan_percent ?? "—"}%{focus.rpm != null && `（${focus.rpm} rpm）`}</span>
          {focus.power_w != null && <span className="mr-2">電力 {Math.round(focus.power_w)} W</span>}
          {focus.emergency && <span className="mr-2 font-semibold text-red-600 dark:text-red-400">上限超え・最大回転</span>}
          {focus.tuning && <span className="text-accent-600 dark:text-accent-400">自動調整中</span>}
        </> : <span className="text-zinc-400">記録を待っています（制御中は 3 秒ごとに記録します）</span>}
      </p>

      <svg viewBox={`0 0 ${W} 146`} role="img" className="mt-1 w-full touch-pan-y select-none text-zinc-500" {...handlers}
        aria-label={`温度の推移（直近${minutes}分）。太線は平滑した値、細線は実測、点線は hotspot の目標、赤い点線は上限`}>
        {Array.from({ length: Math.round((hi - lo) / 10) + 1 }, (_, i) => lo + i * 10).map((c) => (
          <g key={c}>
            <line x1={L} x2={R} y1={yT(c)} y2={yT(c)} className="stroke-zinc-200 dark:stroke-zinc-800" strokeWidth="1" />
            <text x={L - 4} y={yT(c) + 3} fontSize="9" textAnchor="end" className="fill-current">{c}°</text>
          </g>
        ))}
        {targetNow != null && <>
          <path d={path((s) => s.targets?.junction, yT)} fill="none" className="stroke-zinc-500 dark:stroke-zinc-400" strokeWidth="1.2" strokeDasharray="4 3" />
          <text x={R + 2} y={yT(targetNow) + 3} fontSize="8" className="fill-current">目標</text>
        </>}
        {limitNow != null && <>
          <path d={path((s) => s.limits?.junction, yT)} fill="none" className="stroke-red-500" strokeWidth="1.2" strokeDasharray="2 3" />
          <text x={R + 2} y={yT(limitNow) - 1} fontSize="8" className="fill-red-600 dark:fill-red-400">上限</text>
        </>}
        {/* 実測は細く薄く（hotspot は電力が入った瞬間に 25°C ほど跳ねる。本物だが、太いと読めない） */}
        {SENSOR_KINDS.map(({ kind, line }) => (
          <path key={"raw-" + kind} d={path((s) => s.temps[kind], yT)} fill="none" className={line}
            strokeWidth="0.7" strokeOpacity=".35" strokeLinejoin="round" />
        ))}
        {SENSOR_KINDS.map(({ kind, line }) => (
          <path key={"ema-" + kind} d={path((s) => s.smoothed_temps?.[kind] ?? s.temps[kind], yT)} fill="none"
            className={line} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
        ))}
        {cross != null && <line x1={cross} x2={cross} y1={TT} y2={TB} className="stroke-zinc-400" strokeWidth="1" />}
        <text x={L} y={142} fontSize="9" className="fill-current">-{minutes}分</text>
        <text x={R} y={142} fontSize="9" textAnchor="end" className="fill-current">現在</text>
      </svg>

      <svg viewBox={`0 0 ${W} 66`} role="img" className="w-full touch-pan-y select-none text-zinc-500" {...handlers}
        aria-label={`ファン回転（%）の推移（直近${minutes}分）。赤い印は上限を超えて最大回転にしたとき`}>
        {[0, 50, 100].map((p) => (
          <g key={p}>
            <line x1={L} x2={R} y1={yF(p)} y2={yF(p)} className="stroke-zinc-200 dark:stroke-zinc-800" strokeWidth="1" />
            <text x={L - 4} y={yF(p) + 3} fontSize="9" textAnchor="end" className="fill-current">{p}%</text>
          </g>
        ))}
        {fanArea && <path d={fanArea} className="fill-accent-500/15" />}
        <path d={fanLine} fill="none" className="stroke-accent-600 dark:stroke-accent-400" strokeWidth="2" strokeLinejoin="round" />
        {shown.filter((s) => s.emergency).map((s) => (
          <rect key={s.t} x={x(s.t) - 1} y={FT - 4} width="2" height="4" className="fill-red-600" />
        ))}
        {cross != null && <line x1={cross} x2={cross} y1={FT} y2={FB} className="stroke-zinc-400" strokeWidth="1" />}
      </svg>

      <p className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-zinc-500">
        {SENSOR_KINDS.map(({ kind, label, swatch }) => (
          <span key={kind} className="inline-flex items-center gap-1"><span className={`inline-block h-0.5 w-4 rounded ${swatch}`} aria-hidden />{label}</span>
        ))}
        <span>太線＝平滑・細線＝実測</span>
        <span>点線＝hotspot の目標</span>
        <span className="text-red-600 dark:text-red-400">赤い点線＝上限</span>
        <span>下段＝ファン回転%</span>
      </p>
    </section>
  );
}

export type MapPoint = { below_c: number; percent: number };

/** 温度マップのグラフ。横軸は hotspot の温度（目標 − below_c）。
 *  サーバーの map_percent と同じ形: いちばん冷たい点より下は平ら、点の間は直線、
 *  最後の点から目標までは平ら、目標から上限へ 100% まで直線、上限より上は 100%。 */
export function FanMapChart({ points, target, limit, minPercent, currentTemp }: {
  points: MapPoint[]; target: number; limit: number; minPercent: number; currentTemp: number | null;
}) {
  const sorted = [...points].sort((a, b) => b.below_c - a.below_c);
  if (sorted.length < 2) return null;
  const coldest = target - sorted[0].below_c;
  const x0 = Math.floor((coldest - 5) / 5) * 5;
  const x1 = Math.ceil((limit + 4) / 5) * 5;
  const H = 168, T = 10, B = 140;
  const x = (c: number) => L + (R - L) * ((Math.min(x1, Math.max(x0, c)) - x0) / (x1 - x0));
  const y = (p: number) => B - (B - T) * (Math.min(100, Math.max(0, p)) / 100);
  const floor = (p: number) => Math.max(minPercent, p);
  const atTarget = floor(sorted[sorted.length - 1].percent);
  const curve: [number, number][] = [[x0, floor(sorted[0].percent)]];
  for (const p of sorted) curve.push([target - p.below_c, floor(p.percent)]);
  curve.push([target, atTarget]);
  const d = (pts: [number, number][]) => pts.map(([c, p], i) => `${i ? "L" : "M"}${x(c).toFixed(1)} ${y(p).toFixed(1)}`).join("");
  const rise: [number, number][] = [[target, atTarget], [limit, 100], [x1, 100]];
  const percentAt = (c: number) => {
    const all = [...curve, ...rise.slice(1)];
    if (c <= all[0][0]) return all[0][1];
    for (let i = 1; i < all.length; i++) {
      const [ca, pa] = all[i - 1], [cb, pb] = all[i];
      if (c <= cb) return cb === ca ? pb : pa + (pb - pa) * ((c - ca) / (cb - ca));
    }
    return 100;
  };
  const ticks: number[] = [];
  for (let c = x0; c <= x1; c += x1 - x0 > 40 ? 10 : 5) ticks.push(c);
  const now = currentTemp != null ? Math.round(percentAt(currentTemp)) : null;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} role="img" className="w-full select-none text-zinc-500"
      aria-label={`温度マップ。横軸は hotspot の温度、縦軸はファン回転%。目標 ${target}°C で ${Math.round(atTarget)}%、上限 ${limit}°C で 100%`}>
      {[0, 25, 50, 75, 100].map((p) => (
        <g key={p}>
          <line x1={L} x2={R} y1={y(p)} y2={y(p)} className="stroke-zinc-200 dark:stroke-zinc-800" strokeWidth="1" />
          <text x={L - 4} y={y(p) + 3} fontSize="9" textAnchor="end" className="fill-current">{p}%</text>
        </g>
      ))}
      {ticks.map((c) => (
        <text key={c} x={x(c)} y={B + 13} fontSize="9" textAnchor="middle" className="fill-current">{c}°</text>
      ))}
      <line x1={x(target)} x2={x(target)} y1={T} y2={B} className="stroke-zinc-500 dark:stroke-zinc-400" strokeWidth="1" strokeDasharray="4 3" />
      <text x={x(target) - 3} y={T + 8} fontSize="9" textAnchor="end" className="fill-current">目標 {target}°</text>
      <line x1={x(limit)} x2={x(limit)} y1={T} y2={B} className="stroke-red-500" strokeWidth="1" strokeDasharray="2 3" />
      <text x={x(limit) + 3} y={y(100) + 12} fontSize="9" className="fill-red-600 dark:fill-red-400">上限 {limit}°</text>
      <path d={d(curve)} fill="none" className="stroke-accent-600 dark:stroke-accent-400" strokeWidth="2.2" strokeLinejoin="round" strokeLinecap="round" />
      {/* 目標から上限までは自動で 100% へ上げる（点では決めない） */}
      <path d={d(rise)} fill="none" className="stroke-accent-600 dark:stroke-accent-400" strokeWidth="2.2" strokeDasharray="5 3" strokeLinejoin="round" />
      {sorted.map((p) => (
        <circle key={p.below_c} cx={x(target - p.below_c)} cy={y(floor(p.percent))} r="4.5"
          className="fill-white stroke-accent-600 dark:fill-zinc-900 dark:stroke-accent-400" strokeWidth="2">
          <title>{`${target - p.below_c}°C → ${p.percent}%`}</title>
        </circle>
      ))}
      {currentTemp != null && now != null && currentTemp >= x0 && currentTemp <= x1 && (
        <g>
          <line x1={x(currentTemp)} x2={x(currentTemp)} y1={T} y2={B} className="stroke-zinc-400" strokeWidth="1" />
          <circle cx={x(currentTemp)} cy={y(now)} r="4" className="fill-zinc-700 stroke-white dark:fill-zinc-200 dark:stroke-zinc-900" strokeWidth="2" />
          <text x={Math.min(x(currentTemp) + 6, R - 70)} y={Math.min(y(now) + 14, B - 4)} fontSize="9" className="fill-zinc-700 dark:fill-zinc-200">
            今 {Math.round(currentTemp)}° → {now}%
          </text>
        </g>
      )}
    </svg>
  );
}
