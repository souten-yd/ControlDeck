/** 特許の分析（取り込んだ公報の書誌から: 年別・出願人・IPC・出願人×IPC のバブル・出願人×年のヒートマップ・
 *  請求項の数・国と種別・類似マップ）。外部のグラフ部品は使わず SVG と HTML で描く（FanCharts と同じ方針）。
 *  2026-11-21 利用者「特許の分析をグラフで（推移・書誌・請求項・類似・バブル）」。 */
import { useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { patentsApi, type CountRow, type MapPoint, type PatentAnalytics } from "../../api/patents";
import { useAuth, useToasts } from "../../stores";
import { EmptyState, Skeleton, primaryButtonClass, secondaryButtonClass } from "../../components/ui";
import { DocumentSheet } from "../knowledge/DocumentSheet";
import { errorText } from "./shared";

/** 分類ごとの色（類似マップ・国と種別）。並び順で固定して使う。赤は重大な状態だけに使うので入れない。 */
const SERIES = [
  { fill: "fill-[#2a78d6] dark:fill-[#3987e5]", bg: "bg-[#2a78d6] dark:bg-[#3987e5]" },
  { fill: "fill-[#eb6834] dark:fill-[#d95926]", bg: "bg-[#eb6834] dark:bg-[#d95926]" },
  { fill: "fill-[#1baf7a] dark:fill-[#199e70]", bg: "bg-[#1baf7a] dark:bg-[#199e70]" },
  { fill: "fill-[#eda100] dark:fill-[#c98500]", bg: "bg-[#eda100] dark:bg-[#c98500]" },
  { fill: "fill-[#e87ba4] dark:fill-[#d55181]", bg: "bg-[#e87ba4] dark:bg-[#d55181]" },
  { fill: "fill-[#6250d6] dark:fill-[#9085e9]", bg: "bg-[#6250d6] dark:bg-[#9085e9]" },
] as const;
const OTHER = { fill: "fill-zinc-400 dark:fill-zinc-500", bg: "bg-zinc-400 dark:bg-zinc-500" };

const chartCard = "min-w-0 rounded-2xl border border-zinc-200 bg-white p-3 shadow-sm dark:border-zinc-800 dark:bg-zinc-900 sm:p-4";
const muted = "text-xs text-zinc-500";

/** 描く場所の実際の幅（px）。viewBox を幅に合わせ、文字が画面の幅で伸び縮みしないようにする。 */
function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    setWidth(el.clientWidth);
    const ro = new ResizeObserver(([entry]) => setWidth(Math.round(entry.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width] as const;
}

/** 目盛りの切りのよい上限（1・2・5 の倍数）。 */
function niceMax(value: number) {
  if (value <= 4) return Math.max(1, Math.ceil(value));
  const step = 10 ** Math.floor(Math.log10(value));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * step >= value) return m * step;
  return 10 * step;
}

/** 上の角だけ丸めた棒（下端は基線に揃える）。 */
function barPath(x: number, y: number, w: number, h: number) {
  const r = Math.min(4, w / 2, h);
  return `M${x} ${y + h}V${y + r}Q${x} ${y} ${x + r} ${y}H${x + w - r}Q${x + w} ${y} ${x + w} ${y + r}V${y + h}Z`;
}

function ChartCard({ title, caption, wide, children }: { title: string; caption?: ReactNode; wide?: boolean; children: ReactNode }) {
  return (
    <section className={`${chartCard} ${wide ? "lg:col-span-2" : ""}`}>
      <h3 className="text-sm font-semibold">{title}</h3>
      {caption && <p className={`mt-0.5 ${muted}`}>{caption}</p>}
      <div className="mt-3">{children}</div>
    </section>
  );
}

function NoData({ children = "書誌が読めた公報がまだありません。" }: { children?: ReactNode }) {
  return <p className="py-2 text-xs text-zinc-500">{children}</p>;
}

/** 縦の棒（年別の件数・請求項の数の分布）。 */
function ColumnChart({ rows, label, unit = "件", keySuffix = "" }: { rows: CountRow[]; label: string; unit?: string; keySuffix?: string }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const H = 168, T = 16, B = H - 22, L = 30, R = Math.max(L + 40, width - 4);
  const top = niceMax(Math.max(...rows.map((r) => r.count), 1));
  const y = (v: number) => B - (B - T) * (v / top);
  const slot = (R - L) / Math.max(rows.length, 1);
  const barW = Math.max(3, Math.min(36, slot * 0.66));
  const every = Math.max(1, Math.ceil(rows.length / Math.max(1, Math.floor((R - L) / 38))));
  const showValues = slot >= 22;
  return (
    <div ref={ref} className="w-full" style={{ height: H }}>
      {width > 0 && (
        <svg width={width} height={H} viewBox={`0 0 ${width} ${H}`} role="img" aria-label={label} className="block select-none text-zinc-500">
          {[0, top / 2, top].map((v) => (
            <g key={v}>
              <line x1={L} x2={R} y1={y(v)} y2={y(v)} className="stroke-zinc-200 dark:stroke-zinc-800" strokeWidth="1" />
              <text x={L - 6} y={y(v) + 4} fontSize="11" textAnchor="end" className="num fill-current">{Number.isInteger(v) ? v : v.toFixed(1)}</text>
            </g>
          ))}
          {rows.map((r, i) => {
            const cx = L + slot * i + slot / 2;
            const h = B - y(r.count);
            return (
              <g key={r.key}>
                <title>{`${r.key}${keySuffix}: ${r.count} ${unit}`}</title>
                <rect x={cx - slot / 2} y={T} width={slot} height={B - T} className="fill-transparent hover:fill-zinc-100 dark:hover:fill-zinc-800/60" />
                {r.count > 0 && <path d={barPath(cx - barW / 2, y(r.count), barW, h)} className="pointer-events-none fill-accent-600 dark:fill-accent-500" />}
                {showValues && r.count > 0 && (
                  <text x={cx} y={y(r.count) - 4} fontSize="10" textAnchor="middle" className="num pointer-events-none fill-zinc-600 dark:fill-zinc-300">{r.count}</text>
                )}
                {i % every === 0 && (
                  <text x={cx} y={H - 6} fontSize="11" textAnchor="middle" className="num pointer-events-none fill-current">{r.key}</text>
                )}
              </g>
            );
          })}
        </svg>
      )}
    </div>
  );
}

/** 横の棒（出願人・IPC の順位）。名前が長いので、文字は HTML で切り詰めて全体を title に出す。 */
function RankBars({ rows, label, limit = 15 }: { rows: CountRow[]; label: string; limit?: number }) {
  const shown = rows.slice(0, limit);
  const top = Math.max(...shown.map((r) => r.count), 1);
  return (
    <ol aria-label={label} className="space-y-1.5">
      {shown.map((r, i) => (
        <li key={r.key} title={`${r.key}: ${r.count} 件`} className="grid grid-cols-[minmax(0,7.5rem)_minmax(0,1fr)_2.25rem] items-center gap-2 text-xs sm:grid-cols-[minmax(0,10rem)_minmax(0,1fr)_2.5rem]">
          <span className="truncate text-zinc-700 dark:text-zinc-300"><span className="num mr-1 text-zinc-400">{i + 1}</span>{r.key}</span>
          <span className="h-3 rounded-r bg-zinc-100 dark:bg-zinc-800/70">
            <span className="block h-full rounded-r bg-accent-600 dark:bg-accent-500" style={{ width: `${Math.max(2, (r.count / top) * 100)}%` }} />
          </span>
          <span className="num text-right text-zinc-600 dark:text-zinc-300">{r.count}</span>
        </li>
      ))}
    </ol>
  );
}

function order<T>(items: T[], key: (item: T) => string, count: (item: T) => number) {
  const sums = new Map<string, number>();
  for (const it of items) sums.set(key(it), (sums.get(key(it)) ?? 0) + count(it));
  return [...sums.entries()].sort((a, b) => b[1] - a[1]).map(([k]) => k);
}

/** 行の名前（出願人）と、縦書きの列見出しを持つ格子。バブルとヒートマップで共用する。 */
function Grid({ label, rows, cols, colLabel, cell }: {
  label: string; rows: string[]; cols: string[]; colLabel: (col: string, index: number) => string;
  cell: (row: string, col: string) => ReactNode;
}) {
  const template = { gridTemplateColumns: `minmax(0, var(--label)) repeat(${cols.length}, minmax(0, 1fr))` };
  return (
    <div role="img" aria-label={label} className="w-full text-xs [--label:5.5rem] sm:[--label:7rem]">
      <div className="grid items-end gap-0.5" style={template}>
        <span />
        {cols.map((c, i) => (
          <span key={c} className="num flex h-11 items-end justify-center overflow-hidden pb-1 text-[11px] text-zinc-500">
            <span className="[writing-mode:vertical-rl] rotate-180 whitespace-nowrap">{colLabel(c, i)}</span>
          </span>
        ))}
      </div>
      {rows.map((r) => (
        <div key={r} className="grid items-center gap-0.5 py-px" style={template}>
          <span className="truncate pr-2 text-zinc-700 dark:text-zinc-300" title={r}>{r}</span>
          {cols.map((c) => <span key={c} className="block">{cell(r, c)}</span>)}
        </div>
      ))}
    </div>
  );
}

function BubbleChart({ data }: { data: PatentAnalytics["applicant_ipc"] }) {
  const apps = order(data, (d) => d.applicant, (d) => d.count);
  const ipcs = order(data, (d) => d.ipc, (d) => d.count);
  const max = Math.max(...data.map((d) => d.count), 1);
  const at = new Map(data.map((d) => [`${d.applicant}\u0000${d.ipc}`, d.count]));
  return (
    <>
      <Grid label={`出願人（縦）と IPC（横）の組み合わせごとの件数。円の面積が件数。最大 ${max} 件`} rows={apps} cols={ipcs}
        colLabel={(c) => c} cell={(r, c) => {
          const n = at.get(`${r}\u0000${c}`) ?? 0;
          const radius = n ? 2 + 8 * Math.sqrt(n / max) : 0;
          return (
            <svg viewBox="0 0 22 22" className="mx-auto block aspect-square w-full max-w-9">
              <title>{n ? `${r} × ${c}: ${n} 件` : `${r} × ${c}: なし`}</title>
              <line x1="0" x2="22" y1="11" y2="11" className="stroke-zinc-100 dark:stroke-zinc-800" strokeWidth="1" />
              {n > 0 && <circle cx="11" cy="11" r={radius} className="fill-accent-600/70 stroke-accent-700 dark:fill-accent-500/70 dark:stroke-accent-300" strokeWidth="0.8" />}
            </svg>
          );
        }} />
      <p className={`mt-2 ${muted}`}>円の大きさ＝件数（最大 <span className="num">{max}</span> 件）。円の無いところは、その出願人がその分野に出していない。</p>
    </>
  );
}

function HeatMap({ data }: { data: PatentAnalytics["applicant_year"] }) {
  const apps = order(data, (d) => d.applicant, (d) => d.count);
  const years = data.map((d) => Number(d.year)).filter((y) => Number.isFinite(y) && y > 1900);
  const lo = Math.min(...years), hi = Math.max(...years);
  // 間の年も列にする（出していない年が空白として見えるように）。広すぎるときは直近 30 年だけ。
  const span = years.length ? Array.from({ length: hi - lo + 1 }, (_, i) => String(lo + i)).slice(-30) : [];
  const max = Math.max(...data.map((d) => d.count), 1);
  const at = new Map(data.map((d) => [`${d.applicant}\u0000${d.year}`, d.count]));
  const every = Math.max(1, Math.ceil(span.length / 10));
  return (
    <>
      <Grid label={`出願人（縦）と年（横）ごとの件数。濃いほど多い。最大 ${max} 件`} rows={apps} cols={span}
        colLabel={(c, i) => (i % every === 0 ? c : "")} cell={(r, c) => {
          const n = at.get(`${r}\u0000${c}`) ?? 0;
          return (
            <span title={n ? `${r}（${c} 年）: ${n} 件` : `${r}（${c} 年）: なし`}
              className={`block h-6 rounded-[3px] ${n ? "bg-accent-600 dark:bg-accent-500" : "bg-zinc-50 dark:bg-zinc-800/40"}`}
              style={n ? { opacity: 0.25 + 0.75 * (n / max) } : undefined} />
          );
        }} />
      <p className={`mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 ${muted}`}>
        <span className="inline-flex items-center gap-1">
          <span className="inline-block h-3 w-3 rounded-[3px] bg-accent-600 opacity-30 dark:bg-accent-500" aria-hidden />少ない
          <span className="inline-block h-3 w-3 rounded-[3px] bg-accent-600 dark:bg-accent-500" aria-hidden />多い（<span className="num">{max}</span> 件）
        </span>
        <span>薄い灰色は 0 件（空白の年）</span>
      </p>
    </>
  );
}

/** 国・種別の内訳（積み上げの帯 + 札）。多いものから 5 つ、残りは「その他」。 */
function Breakdown({ title, rows }: { title: string; rows: CountRow[] }) {
  if (!rows.length) return null;
  const head = rows.slice(0, 5);
  const rest = rows.slice(5).reduce((n, r) => n + r.count, 0);
  const parts = rest ? [...head, { key: "その他", count: rest }] : head;
  const total = parts.reduce((n, r) => n + r.count, 0) || 1;
  return (
    <div>
      <p className="mb-1.5 text-xs font-medium text-zinc-600 dark:text-zinc-300">{title}</p>
      <div className="flex h-3 gap-0.5 overflow-hidden rounded-full" role="img"
        aria-label={`${title}: ${parts.map((p) => `${p.key} ${p.count} 件`).join("、")}`}>
        {parts.map((p, i) => (
          <span key={p.key} title={`${p.key}: ${p.count} 件`} className={`block h-full ${p.key === "その他" ? OTHER.bg : (SERIES[i] ?? OTHER).bg}`}
            style={{ width: `${(p.count / total) * 100}%` }} />
        ))}
      </div>
      <p className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-xs text-zinc-600 dark:text-zinc-300">
        {parts.map((p, i) => (
          <span key={p.key} className="inline-flex items-center gap-1">
            <span className={`inline-block h-2 w-2 rounded-full ${p.key === "その他" ? OTHER.bg : (SERIES[i] ?? OTHER).bg}`} aria-hidden />
            {p.key} <span className="num text-zinc-500">{p.count}</span>
          </span>
        ))}
      </p>
    </div>
  );
}

/** 類似マップ。内容のベクトルを 2 次元に縮めた散布図。軸の数値には意味が無いので出さない。 */
function SimilarityMap({ points, onOpen }: { points: MapPoint[]; onOpen: (ref: string) => void }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<MapPoint | null>(null);
  const groups = useMemo(() => {
    const counts = new Map<string, number>();
    for (const p of points) if (p.ipc) counts.set(p.ipc, (counts.get(p.ipc) ?? 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, SERIES.length);
  }, [points]);
  const colorOf = (ipc: string) => {
    const i = groups.findIndex(([k]) => k === ipc);
    return i >= 0 ? SERIES[i] : OTHER;
  };
  const others = points.filter((p) => colorOf(p.ipc) === OTHER).length;
  const H = Math.round(Math.min(380, Math.max(240, width * 0.6)));
  const pad = 14;
  const xs = points.map((p) => p.x), ys = points.map((p) => p.y);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const sx = (v: number) => pad + (width - 2 * pad) * ((v - x0) / (x1 - x0 || 1));
  const sy = (v: number) => H - pad - (H - 2 * pad) * ((v - y0) / (y1 - y0 || 1));
  const onKey = (e: KeyboardEvent, p: MapPoint) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(p.ref); }
  };
  return (
    <>
      <p className="min-h-5 truncate text-xs text-zinc-600 dark:text-zinc-300" aria-live="off">
        {hover ? <>{hover.title}<span className="text-zinc-400">{[hover.applicant, hover.ipc, hover.year].filter(Boolean).map((s) => ` · ${s}`).join("")}</span></>
          : <span className="text-zinc-400">点に触れると題名が出ます。押すと公報を開きます。</span>}
      </p>
      <div ref={ref} className="mt-1 w-full overflow-hidden rounded-xl bg-zinc-50 dark:bg-zinc-950/40" style={{ height: H }}>
        {width > 0 && (
          <svg width={width} height={H} viewBox={`0 0 ${width} ${H}`} role="img" className="block select-none"
            aria-label={`類似マップ（${points.length} 件の公報）。近い点ほど内容が似ている。色は IPC のサブクラス`}>
            <line x1={width / 2} x2={width / 2} y1={pad} y2={H - pad} className="stroke-zinc-200 dark:stroke-zinc-800" strokeDasharray="2 4" />
            <line x1={pad} x2={width - pad} y1={H / 2} y2={H / 2} className="stroke-zinc-200 dark:stroke-zinc-800" strokeDasharray="2 4" />
            {points.map((p) => {
              const active = hover?.ref === p.ref;
              return (
                <g key={p.ref} role="button" tabIndex={0} aria-label={`${p.title}を開く`} className="cursor-pointer outline-none [&:focus-visible>circle:last-child]:stroke-accent-500"
                  onPointerEnter={() => setHover(p)} onPointerLeave={() => setHover(null)} onFocus={() => setHover(p)} onBlur={() => setHover(null)}
                  onClick={() => onOpen(p.ref)} onKeyDown={(e) => onKey(e, p)}>
                  <title>{[p.title, p.applicant, p.ipc, p.year].filter(Boolean).join(" · ")}</title>
                  <circle cx={sx(p.x)} cy={sy(p.y)} r={12} className="fill-transparent" />
                  <circle cx={sx(p.x)} cy={sy(p.y)} r={active ? 7 : 5}
                    className={`${colorOf(p.ipc).fill} stroke-white dark:stroke-zinc-900`} strokeWidth={active ? 2.5 : 1.5} />
                </g>
              );
            })}
          </svg>
        )}
      </div>
      <p className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-zinc-600 dark:text-zinc-300">
        {groups.map(([k, n], i) => (
          <span key={k} className="inline-flex items-center gap-1">
            <span className={`inline-block h-2.5 w-2.5 rounded-full ${SERIES[i].bg}`} aria-hidden />{k} <span className="num text-zinc-500">{n}</span>
          </span>
        ))}
        {others > 0 && (
          <span className="inline-flex items-center gap-1">
            <span className={`inline-block h-2.5 w-2.5 rounded-full ${OTHER.bg}`} aria-hidden />その他・IPC なし <span className="num text-zinc-500">{others}</span>
          </span>
        )}
      </p>
    </>
  );
}

/** 分析の画面。/patents の「分析」で使う。onOpenDocs は「公報」へ移る（公報が無いときの案内）。 */
export function AnalyticsDashboard({ onOpenDocs }: { onOpenDocs?: () => void }) {
  const show = useToasts((s) => s.show);
  const can = useAuth((s) => s.can);
  const [open, setOpen] = useState<string | null>(null);
  const { data, isLoading, isError, refetch } = useQuery({ queryKey: ["patents-analytics"], queryFn: patentsApi.analytics, staleTime: 30_000 });
  const map = useQuery({ queryKey: ["patents-analytics-map"], queryFn: patentsApi.analyticsMap, staleTime: 60_000, enabled: Boolean(data?.documents) });
  const refresh = useMutation({
    mutationFn: patentsApi.refreshBiblio,
    onSuccess: () => show("読み直しています（Jobs で進み具合を見られます）"),
    onError: (e) => show(errorText(e), "error"),
  });

  if (isLoading) {
    return <div className="grid gap-4 lg:grid-cols-2">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-56" />)}</div>;
  }
  if (isError || !data) {
    return (
      <EmptyState actions={<button className={secondaryButtonClass} onClick={() => void refetch()}>もう一度読み込む</button>}>
        分析を読み込めませんでした。
      </EmptyState>
    );
  }
  if (data.documents === 0) {
    return (
      <EmptyState actions={onOpenDocs && <button className={primaryButtonClass} onClick={onOpenDocs}>公報を取り込む</button>}>
        まだ公報がありません。「公報」から公報・論文を取り込むと、年ごとの件数・出願人・IPC・似た公報の地図をここで見られます。
      </EmptyState>
    );
  }
  const cov = data.coverage;
  const histogram = data.claims_histogram.some((r) => r.count > 0) ? data.claims_histogram : [];
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <p className="num min-w-0 flex-1 text-xs leading-relaxed text-zinc-500">
          {data.documents} 件中、書誌が読めたもの: 年 {cov.year}・出願人 {cov.applicant}・IPC {cov.ipc}・請求項の数 {cov.claims}
        </p>
        {can("workflows.edit") && (
          <button className="min-h-11 shrink-0 rounded-xl px-3 text-sm text-accent-700 hover:bg-accent-600/10 disabled:opacity-40 dark:text-accent-300"
            disabled={refresh.isPending} onClick={() => refresh.mutate()}>書誌を読み直す</button>
        )}
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <ChartCard title="年別の件数" caption="公開日（無ければ出願日）の年">
          {data.by_year.length ? <ColumnChart rows={data.by_year} keySuffix=" 年" label={`年別の件数: ${data.by_year.map((r) => `${r.key}年 ${r.count}件`).join("、")}`} /> : <NoData />}
        </ChartCard>
        <ChartCard title="請求項の数" caption="公報 1 件あたりの請求項の数の分布">
          {histogram.length ? <ColumnChart rows={histogram} keySuffix=" 項" label={`請求項の数の分布: ${histogram.map((r) => `${r.key}項 ${r.count}件`).join("、")}`} /> : <NoData>請求項の数が読めた公報がまだありません。</NoData>}
        </ChartCard>
        <ChartCard title="出願人ランキング" caption="多い順に 15 位まで">
          {data.applicants.length ? <RankBars rows={data.applicants} label="出願人ごとの件数" /> : <NoData />}
        </ChartCard>
        <ChartCard title="IPC サブクラス" caption="技術の分類（4 桁）ごとの件数">
          {data.ipc_subclasses.length ? <RankBars rows={data.ipc_subclasses} label="IPC サブクラスごとの件数" /> : <NoData />}
        </ChartCard>
        <ChartCard title="出願人 × IPC" caption="誰がどの分野に出しているか">
          {data.applicant_ipc.length ? <BubbleChart data={data.applicant_ipc} /> : <NoData>出願人と IPC の両方が読めた公報がまだありません。</NoData>}
        </ChartCard>
        <ChartCard title="出願人 × 年" caption="いつ出しているか">
          {data.applicant_year.length ? <HeatMap data={data.applicant_year} /> : <NoData>出願人と年の両方が読めた公報がまだありません。</NoData>}
        </ChartCard>
        <ChartCard title="国・種別" wide>
          {data.countries.length || data.kinds.length ? (
            <div className="grid gap-4 sm:grid-cols-2">
              <Breakdown title="国" rows={data.countries} />
              <Breakdown title="種別" rows={data.kinds} />
            </div>
          ) : <NoData />}
        </ChartCard>
        <ChartCard title="類似マップ" caption="近い点ほど内容が似ている（軸に意味はありません）" wide>
          {map.isLoading ? <Skeleton className="h-60" /> : map.data && map.data.points.length ? (
            <SimilarityMap points={map.data.points} onOpen={setOpen} />
          ) : <NoData>内容を読み込んだ公報が 3 件以上になると出ます。</NoData>}
        </ChartCard>
      </div>
      <p className="text-xs leading-relaxed text-zinc-500">{data.note}</p>
      {open && <DocumentSheet docRef={open} onClose={() => setOpen(null)} />}
    </div>
  );
}
