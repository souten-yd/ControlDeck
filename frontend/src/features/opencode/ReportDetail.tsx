/** 結果の詳細。モバイルは下からのシート、デスクトップは右のパネル。
 *
 * UI 検証は幅ごとの画面写真、精密調査は出典、レビューは該当箇所つきの指摘を主に見せる。 */
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { BottomSheet, ConfirmDialog, Skeleton } from "../../components/ui";
import { IconChevronLeft, IconChevronRight, IconTrash, IconX } from "../../components/icons";
import { useAuth, useToasts } from "../../stores";
import {
  KIND_LABEL, SEVERITIES, SEVERITY_LABEL, SEVERITY_STYLE, VerdictBadge, reportImageUrl, reportsKey, timeAgo,
  type ReportKind, type Severity, type Verdict,
} from "./reportShared";

type CheckStatus = "pass" | "fail" | "skip";
type Stance = "supports" | "contradicts" | "context";
interface Check { viewport: string; theme: "light" | "dark" | ""; screenshot: string | null; status: CheckStatus; note: string }
interface Finding { severity: Severity; title: string; detail: string; location: string; viewport: string; screenshot: string | null }
interface Source {
  title: string; url: string; accessed_at: string; published_at: string; version: string;
  claim: string; stance: Stance; primary: boolean;
}
export interface ReportRecord {
  id: string; kind: ReportKind; title: string; summary: string; verdict: Verdict; target: string; created_at: number;
  checks?: Check[]; findings?: Finding[]; sources?: Source[];
  console_errors?: string[]; failed_requests?: string[]; unverified?: string[]; missing_images?: string[];
}
interface Shot { name: string; alt: string; caption: string; note: string }

const THEME_LABEL: Record<string, string> = { light: "ライト", dark: "ダーク" };
const CHECK: Record<CheckStatus, { label: string; cls: string; path: string }> = {
  pass: { label: "合格", cls: "text-emerald-700 dark:text-emerald-400", path: "m5 12 5 5 9-10" },
  fail: { label: "問題あり", cls: "text-amber-700 dark:text-amber-400", path: "M12 7v6m0 3.5v.5" },
  skip: { label: "未確認", cls: "text-zinc-500", path: "M5 12h14" },
};
const STANCE: Record<Stance, { label: string; badge: string; card: string; path: string }> = {
  supports: { label: "支持", badge: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300",
    card: "border-zinc-200 dark:border-zinc-700", path: "m5 12 5 5 9-10" },
  // 反証は赤にしない（誤りではなく、読むべき根拠）。黄の太い左線と記号で見分ける。
  contradicts: { label: "反証", badge: "bg-amber-100 text-amber-900 dark:bg-amber-950/60 dark:text-amber-200",
    card: "border-amber-300 border-l-4 bg-amber-50/50 dark:border-amber-700 dark:bg-amber-950/20", path: "M7 7l10 10M17 7 7 17" },
  context: { label: "背景", badge: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300",
    card: "border-zinc-200 dark:border-zinc-700", path: "M5 12h14" },
};

function Mark({ path }: { path: string }) {
  return <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden className="shrink-0"><path d={path} /></svg>;
}

function viewportWidth(viewport: string): number | null {
  const match = /^\s*(\d{2,5})/.exec(viewport ?? "");
  return match ? Number(match[1]) : null;
}

function shotLabel(viewport: string, theme: string): string {
  const width = viewportWidth(viewport);
  return [width ? `幅 ${width}px` : viewport, THEME_LABEL[theme]].filter(Boolean).join("・") || "画面写真";
}

/** ISO 形式の日時だけ読みやすくし、それ以外（版や年だけ等）は書かれたまま出す。 */
function showDate(value: string): string {
  if (!/^\d{4}-\d{2}-\d{2}T/.test(value)) return value;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("ja-JP", { dateStyle: "medium", timeStyle: "short" });
}

const isHttp = (url: string) => /^https?:\/\//i.test(url);
const heading = "text-xs font-semibold text-zinc-500";

export function ReportDetailSheet({ id, onClose }: { id: string; onClose: () => void }) {
  const qc = useQueryClient();
  const show = useToasts((state) => state.show);
  const canDelete = useAuth((state) => state.can("workflows.edit"));
  const [confirming, setConfirming] = useState(false);
  const [lightbox, setLightbox] = useState<number | null>(null);
  const query = useQuery({
    queryKey: ["opencode-report", id],
    queryFn: () => api<ReportRecord>(`/opencode/reports/${encodeURIComponent(id)}`),
  });
  const remove = useMutation({
    mutationFn: () => api(`/opencode/reports/${encodeURIComponent(id)}`, { method: "DELETE" }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: reportsKey });
      show("結果を削除しました");
      onClose();
    },
    onError: (error) => {
      setConfirming(false);
      show(error instanceof Error ? error.message : "削除できませんでした", "error");
    },
  });
  const report = query.data;
  const missing = useMemo(() => new Set(report?.missing_images ?? []), [report]);
  const has = (name: string | null | undefined): name is string => Boolean(name) && !missing.has(name as string);
  // 拡大表示で前後に送る順。画面ごとの写真 → 指摘だけが持つ写真。
  const shots = useMemo<Shot[]>(() => {
    const list: Shot[] = [];
    const seen = new Set<string>();
    for (const check of report?.checks ?? []) {
      if (!check.screenshot || missing.has(check.screenshot) || seen.has(check.screenshot)) continue;
      seen.add(check.screenshot);
      const label = shotLabel(check.viewport, check.theme);
      list.push({ name: check.screenshot, alt: `${label} の画面写真`, caption: `${label}・${CHECK[check.status]?.label ?? ""}`, note: check.note });
    }
    for (const finding of report?.findings ?? []) {
      if (!finding.screenshot || missing.has(finding.screenshot) || seen.has(finding.screenshot)) continue;
      seen.add(finding.screenshot);
      const label = shotLabel(finding.viewport, "");
      list.push({ name: finding.screenshot, alt: `${label} の画面写真（${finding.title}）`, caption: label, note: finding.title });
    }
    return list;
  }, [report, missing]);
  const openShot = (name: string) => {
    const index = shots.findIndex((shot) => shot.name === name);
    if (index >= 0) setLightbox(index);
  };
  const findings = useMemo(
    () => [...(report?.findings ?? [])].sort((a, b) => SEVERITIES.indexOf(a.severity) - SEVERITIES.indexOf(b.severity)),
    [report],
  );
  const kindLabel = report ? KIND_LABEL[report.kind] ?? report.kind : "結果";
  // 上に重ねた確認・拡大表示を Esc で閉じたとき、下のシートまで一緒に閉じない。
  const guardedClose = () => { if (!confirming && lightbox === null) onClose(); };

  return (
    <BottomSheet title={`${kindLabel}の結果`} onClose={guardedClose} sideOnDesktop
      headerActions={canDelete && report ? (
        <button type="button" onClick={() => setConfirming(true)} aria-label="この結果を削除"
          className="grid min-h-11 min-w-11 place-items-center rounded-lg text-zinc-400 hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-950/40">
          <IconTrash />
        </button>
      ) : undefined}>
      {query.isPending && <div className="space-y-3" aria-label="結果を読み込み中"><Skeleton className="h-6 w-2/3" /><Skeleton className="h-16" /><Skeleton className="h-40" /></div>}
      {query.isError && <div role="alert" className="space-y-2 text-sm text-zinc-600 dark:text-zinc-300">
        <p className="break-words">結果を読み込めませんでした。{query.error.message}</p>
        <button type="button" onClick={() => void query.refetch()} className="min-h-11 rounded-xl border border-zinc-300 px-3 text-sm dark:border-zinc-700">再試行</button>
      </div>}
      {report && (
        <div className="space-y-5 sm:w-full">
          <header className="space-y-2">
            <div className="flex flex-wrap items-center gap-2 text-xs text-zinc-500">
              <VerdictBadge verdict={report.verdict} />
              <span className="tabular-nums" title={new Date(report.created_at * 1000).toLocaleString("ja-JP")}>{timeAgo(report.created_at)}</span>
            </div>
            <h3 className="break-words text-base font-semibold leading-snug">{report.title || "（無題）"}</h3>
            {report.target && <p className="break-all font-mono text-xs text-zinc-500"><span className="font-sans">対象: </span>{report.target}</p>}
            {report.summary && <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-zinc-700 dark:text-zinc-300">{report.summary}</p>}
          </header>

          {(report.checks?.length ?? 0) > 0 && <Screens id={report.id} checks={report.checks ?? []} has={has} onOpen={openShot} />}

          {report.kind === "research" && (report.sources?.length ?? 0) > 0 && <Sources sources={report.sources ?? []} />}

          {findings.length > 0 && (
            <section className="space-y-2">
              <h4 className={heading}>指摘 <span className="tabular-nums">{findings.length}</span></h4>
              <ul className="space-y-2">
                {findings.map((finding, index) => (
                  <li key={index} className="space-y-1.5 rounded-xl border border-zinc-200 p-3 dark:border-zinc-700">
                    <p className="flex items-start gap-2">
                      <span className={`mt-0.5 shrink-0 whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-medium ${SEVERITY_STYLE[finding.severity] ?? SEVERITY_STYLE.info}`}>
                        {SEVERITY_LABEL[finding.severity] ?? finding.severity}
                      </span>
                      <span className="min-w-0 flex-1 break-words text-sm font-medium">{finding.title}</span>
                    </p>
                    {finding.detail && <p className="whitespace-pre-wrap break-words text-xs leading-relaxed text-zinc-600 dark:text-zinc-300">{finding.detail}</p>}
                    {finding.location && <CopyLocation text={finding.location} />}
                    {(finding.viewport || finding.screenshot) && (
                      <p className="flex flex-wrap items-center gap-x-3 text-xs text-zinc-500">
                        {finding.viewport && <span className="tabular-nums">{shotLabel(finding.viewport, "")}</span>}
                        {has(finding.screenshot) && (
                          <button type="button" onClick={() => openShot(finding.screenshot as string)}
                            className="min-h-11 text-accent-700 underline dark:text-accent-400">画面写真を見る</button>
                        )}
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          )}

          {report.kind !== "research" && (report.sources?.length ?? 0) > 0 && <Sources sources={report.sources ?? []} />}

          <LogBlock title="コンソールのエラー" lines={report.console_errors ?? []} />
          <LogBlock title="失敗した通信" lines={report.failed_requests ?? []} />

          {(report.unverified?.length ?? 0) > 0 && (
            <section className="space-y-2">
              <h4 className={heading}>確かめられなかったこと</h4>
              <ul className="list-disc space-y-1 pl-5 text-sm text-zinc-700 dark:text-zinc-300">
                {(report.unverified ?? []).map((item, index) => <li key={index} className="break-words">{item}</li>)}
              </ul>
            </section>
          )}

          {(report.missing_images?.length ?? 0) > 0 && (
            <p className="break-all text-[11px] text-zinc-400">
              取り込めなかった画面写真 <span className="tabular-nums">{report.missing_images?.length}</span> 枚: {(report.missing_images ?? []).join("、")}
            </p>
          )}
        </div>
      )}
      {lightbox !== null && report && shots.length > 0 && (
        <Lightbox id={report.id} shots={shots} index={Math.min(lightbox, shots.length - 1)} onIndex={setLightbox} onClose={() => setLightbox(null)} />
      )}
      {confirming && (
        <ConfirmDialog title="この結果を削除しますか？"
          message={`「${report?.title || kindLabel}」と画面写真を削除します。取り消せません。`}
          confirmLabel="削除する" busy={remove.isPending}
          onConfirm={() => remove.mutate()} onClose={() => setConfirming(false)} />
      )}
    </BottomSheet>
  );
}

/** 幅ごとにまとめた画面写真。幅の小さい順、同じ幅の中はライト → ダーク。 */
function Screens({ id, checks, has, onOpen }: {
  id: string; checks: Check[]; has: (name: string | null | undefined) => name is string; onOpen: (name: string) => void;
}) {
  const groups = useMemo(() => {
    const map = new Map<string, { width: number | null; label: string; items: Check[] }>();
    for (const check of checks) {
      const width = viewportWidth(check.viewport);
      const key = width ? String(width) : check.viewport || "";
      const group = map.get(key) ?? { width, label: width ? `幅 ${width}px` : check.viewport || "幅の指定なし", items: [] };
      group.items.push(check);
      map.set(key, group);
    }
    const order = { light: 0, dark: 1, "": 2 };
    return [...map.values()]
      .sort((a, b) => (a.width ?? Infinity) - (b.width ?? Infinity))
      .map((group) => ({ ...group, items: [...group.items].sort((a, b) => (order[a.theme] ?? 2) - (order[b.theme] ?? 2)) }));
  }, [checks]);
  return (
    <section className="space-y-3">
      <h4 className={heading}>画面 <span className="tabular-nums">{checks.length}</span></h4>
      <div className="grid gap-4 sm:grid-cols-2">
        {groups.map((group) => (
          <div key={group.label} role="group" aria-label={group.label}>
            <p className="mb-1.5 text-xs font-medium tabular-nums">{group.label}</p>
            <ul className="grid grid-cols-2 gap-2">
              {group.items.map((check, index) => {
                const state = CHECK[check.status] ?? CHECK.skip;
                const label = shotLabel(check.viewport, check.theme);
                return (
                  <li key={index} className="min-w-0">
                    {has(check.screenshot) ? (
                      <button type="button" onClick={() => onOpen(check.screenshot as string)} aria-label={`${label} の画面写真を拡大`}
                        className="block aspect-[4/3] w-full overflow-hidden rounded-lg border border-zinc-200 bg-zinc-100 dark:border-zinc-700 dark:bg-zinc-800">
                        <img src={reportImageUrl(id, check.screenshot)} alt={`${label} の画面写真`} loading="lazy" className="h-full w-full object-cover object-top" />
                      </button>
                    ) : (
                      <div className="grid aspect-[4/3] w-full place-items-center rounded-lg border border-dashed border-zinc-200 text-[11px] text-zinc-400 dark:border-zinc-700">写真なし</div>
                    )}
                    <p className={`mt-1 flex items-center gap-1 text-[11px] font-medium ${state.cls}`}>
                      <Mark path={state.path} />{state.label}
                      {check.theme && <span className="ml-auto font-normal text-zinc-500">{THEME_LABEL[check.theme]}</span>}
                    </p>
                    {check.note && <p className="mt-0.5 break-words text-[11px] leading-snug text-zinc-500">{check.note}</p>}
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}

function Sources({ sources }: { sources: Source[] }) {
  const [filter, setFilter] = useState<Stance | "all">("all");
  const count = (stance: Stance) => sources.filter((source) => source.stance === stance).length;
  const contradicting = count("contradicts");
  const tabs: [Stance | "all", string, number][] = [
    ["all", "すべて", sources.length], ["supports", "支持", count("supports")],
    ["contradicts", "反証", contradicting], ["context", "背景", count("context")],
  ];
  const shown = filter === "all" ? sources : sources.filter((source) => source.stance === filter);
  return (
    <section className="space-y-2">
      <h4 className={heading}>出典 <span className="tabular-nums">{sources.length}</span></h4>
      {contradicting > 0 && filter === "all" && (
        <p className="flex items-center gap-1.5 rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
          <Mark path={STANCE.contradicts.path} />結論に反する根拠が <span className="tabular-nums">{contradicting}</span> 件あります。
        </p>
      )}
      <div role="group" aria-label="出典の絞り込み" className="flex gap-1 rounded-xl bg-zinc-100 p-1 dark:bg-zinc-800">
        {tabs.map(([key, label, n]) => (
          <button key={key} type="button" aria-pressed={filter === key} disabled={key !== "all" && n === 0} onClick={() => setFilter(key)}
            className={`min-h-9 flex-1 rounded-lg px-1 text-xs font-medium tabular-nums disabled:opacity-40 ${filter === key ? "bg-white shadow-sm dark:bg-zinc-900" : "text-zinc-500"}`}>
            {label} {n}
          </button>
        ))}
      </div>
      <ul className="space-y-2">
        {shown.map((source, index) => {
          const stance = STANCE[source.stance] ?? STANCE.context;
          return (
            <li key={index} className={`space-y-1.5 rounded-xl border p-3 ${stance.card}`}>
              <p className="flex flex-wrap items-center gap-1.5">
                <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${stance.badge}`}><Mark path={stance.path} />{stance.label}</span>
                {source.primary && <span className="rounded-full border border-accent-500 px-2 py-0.5 text-[11px] font-medium text-accent-700 dark:text-accent-400">一次資料</span>}
              </p>
              <p className="break-words text-sm font-medium">{source.title || source.url}</p>
              {source.claim && <p className="break-words text-xs leading-relaxed text-zinc-600 dark:text-zinc-300"><span className="text-zinc-400">主張: </span>{source.claim}</p>}
              {(source.version || source.published_at || source.accessed_at) && (
                <p className="flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] tabular-nums text-zinc-500">
                  {source.version && <span>版 {source.version}</span>}
                  {source.published_at && <span>公開 {showDate(source.published_at)}</span>}
                  {source.accessed_at && <span>取得 {showDate(source.accessed_at)}</span>}
                </p>
              )}
              {source.url && (isHttp(source.url)
                ? <a href={source.url} target="_blank" rel="noopener noreferrer"
                    className="flex min-h-11 items-center break-all font-mono text-[11px] text-accent-700 underline dark:text-accent-400">{source.url}</a>
                : <p className="break-all font-mono text-[11px] text-zinc-500">{source.url}</p>)}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/** 該当箇所（file:line）。行そのものがコピーのボタン。 */
function CopyLocation({ text }: { text: string }) {
  const show = useToasts((state) => state.show);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      show("該当箇所をコピーしました");
    } catch {
      show("コピーできませんでした", "error");
    }
  };
  return (
    <button type="button" onClick={() => void copy()} aria-label={`該当箇所 ${text} をコピー`} title="タップでコピー"
      className="flex min-h-11 w-full items-center gap-2 rounded-lg bg-zinc-50 px-2.5 py-1.5 text-left hover:bg-zinc-100 dark:bg-zinc-800/60 dark:hover:bg-zinc-800">
      <span className="min-w-0 flex-1 break-all font-mono text-xs">{text}</span>
      <span className="shrink-0 text-[11px] text-zinc-500">コピー</span>
    </button>
  );
}

function LogBlock({ title, lines }: { title: string; lines: string[] }) {
  if (lines.length === 0) return null;
  return (
    <details className="group rounded-xl border border-zinc-200 dark:border-zinc-700">
      <summary className="flex min-h-11 cursor-pointer list-none items-center gap-2 px-3 text-sm [&::-webkit-details-marker]:hidden">
        <span className="flex-1">{title} <span className="tabular-nums text-zinc-500">{lines.length}</span></span>
        <IconChevronRight className="text-zinc-400 transition-transform group-open:rotate-90" />
      </summary>
      <ul className="max-h-64 space-y-1.5 overflow-y-auto border-t border-zinc-100 px-3 py-2 dark:border-zinc-800">
        {lines.map((line, index) => <li key={index} className="whitespace-pre-wrap break-all font-mono text-[11px] text-zinc-600 dark:text-zinc-300">{line}</li>)}
      </ul>
    </details>
  );
}

/** 画面写真の拡大表示。←→ で前後、Esc で閉じる。Tab はこの中だけを回る。 */
function Lightbox({ id, shots, index, onIndex, onClose }: {
  id: string; shots: Shot[]; index: number; onIndex: (index: number) => void; onClose: () => void;
}) {
  const root = useRef<HTMLDivElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const touchX = useRef<number | null>(null);
  const latest = useRef({ index, onIndex, onClose, count: shots.length });
  latest.current = { index, onIndex, onClose, count: shots.length };
  const step = (delta: number) => {
    const { index: current, count, onIndex: set } = latest.current;
    if (count > 1) set((current + delta + count) % count);
  };
  const stepRef = useRef(step);
  stepRef.current = step;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    closeButton.current?.focus();
    // capture で受けて止める。下のシートの Esc まで届くと、拡大表示と一緒にシートも閉じてしまう。
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        latest.current.onClose();
      } else if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        event.stopPropagation();
        event.preventDefault();
        stepRef.current(event.key === "ArrowLeft" ? -1 : 1);
      } else if (event.key === "Tab") {
        const focusable = Array.from(root.current?.querySelectorAll<HTMLElement>("button:not([disabled])") ?? []);
        if (focusable.length === 0) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        const current = document.activeElement;
        if (!root.current?.contains(current)) { event.preventDefault(); first.focus(); }
        else if (event.shiftKey && current === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && current === last) { event.preventDefault(); first.focus(); }
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      previous?.focus?.();
    };
  }, []);
  const shot = shots[index];
  const many = shots.length > 1;
  const navButton = "grid min-h-11 min-w-11 place-items-center rounded-full bg-white/10 text-xl text-white hover:bg-white/20 disabled:opacity-30";
  return createPortal(
    <div ref={root} role="dialog" aria-modal="true" aria-label="画面写真の拡大表示"
      className="safe-top safe-bottom fixed inset-0 z-[70] flex flex-col bg-black/90 text-white"
      onMouseDown={(event) => event.stopPropagation()}>
      <div className="flex shrink-0 items-center gap-2 px-3 py-2">
        <p className="min-w-0 flex-1 truncate text-sm">{shot.caption}</p>
        {many && <p className="shrink-0 text-xs tabular-nums text-white/70" aria-live="polite">{index + 1} / {shots.length}</p>}
        <button ref={closeButton} type="button" onClick={onClose} aria-label="拡大表示を閉じる" className={navButton}><IconX /></button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto overscroll-contain px-2"
        onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}
        onTouchStart={(event) => { touchX.current = event.touches.length === 1 ? event.touches[0].clientX : null; }}
        onTouchEnd={(event) => {
          if (touchX.current === null) return;
          const dx = event.changedTouches[0].clientX - touchX.current;
          touchX.current = null;
          if (Math.abs(dx) > 60) step(dx > 0 ? -1 : 1);
        }}>
        <img key={shot.name} src={reportImageUrl(id, shot.name)} alt={shot.alt} className="mx-auto max-w-full rounded-md sm:max-h-full sm:object-contain" />
      </div>
      <div className="flex shrink-0 items-center gap-3 px-3 py-2">
        <button type="button" onClick={() => step(-1)} disabled={!many} aria-label="前の画面写真" className={navButton}><IconChevronLeft /></button>
        <p className="line-clamp-3 min-w-0 flex-1 break-words text-center text-xs text-white/80">{shot.note}</p>
        <button type="button" onClick={() => step(1)} disabled={!many} aria-label="次の画面写真" className={navButton}><IconChevronRight /></button>
      </div>
    </div>,
    document.body,
  );
}
