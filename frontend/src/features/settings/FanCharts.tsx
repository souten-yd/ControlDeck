/** ファン制御の画面のグラフ（直近の推移: 各部の温度とファンの回転数）。外部のグラフ部品は使わず SVG で描く。 */
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

/** 温度とファンの回転数（rpm）の推移。記録はサーバーが常時行い、ここでは画面が見えている間だけ差分を取りに行く。
 *  温度と回転数は単位が違うので、軸を共有せず上下 2 段に分ける（2026-11-18 利用者の指示: 回転数と各部の温度を出す）。 */
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
  // 回転数の縦軸: 0 から、見えている最大を 1000 rpm 単位で切り上げたところまで
  const rpms = shown.map((s) => s.rpm).filter((v): v is number => v != null);
  const rpmTop = Math.max(1000, Math.ceil((rpms.length ? Math.max(...rpms) : 0) / 1000) * 1000);
  const FT = 6, FB = 60;
  const yF = (rpm: number) => FB - (FB - FT) * (Math.min(rpmTop, Math.max(0, rpm)) / rpmTop);

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
  const fanLine = path((s) => s.rpm, yF);
  const firstFan = shown.find((s) => s.rpm != null);
  const lastFan = [...shown].reverse().find((s) => s.rpm != null);
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
          <span className="mr-2">ファン {focus.rpm != null ? `${focus.rpm.toLocaleString()} rpm` : "—"}{focus.fan_percent != null && `（${focus.fan_percent}%）`}</span>
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
        aria-label={`ファンの回転数（rpm）の推移（直近${minutes}分）。赤い印は上限を超えて最大回転にしたとき`}>
        {[0, rpmTop / 2, rpmTop].map((rpm) => (
          <g key={rpm}>
            <line x1={L} x2={R} y1={yF(rpm)} y2={yF(rpm)} className="stroke-zinc-200 dark:stroke-zinc-800" strokeWidth="1" />
            <text x={L - 4} y={yF(rpm) + 3} fontSize="9" textAnchor="end" className="fill-current">{rpm >= 1000 ? `${rpm / 1000}k` : rpm}</text>
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
        <span>下段＝ファンの回転数（rpm）</span>
      </p>
    </section>
  );
}
