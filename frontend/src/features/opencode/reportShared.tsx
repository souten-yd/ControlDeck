/** 結果（UI 検証・精密調査・レビュー）の一覧と詳細で共有する型・ラベル・バッジ。 */
import { IconBookOpen, IconBranch, IconImages } from "../../components/icons";

export type ReportKind = "ui_verify" | "research" | "review";
export type Verdict = "pass" | "fail" | "partial";
export type Severity = "critical" | "major" | "minor" | "info";

export interface ReportSummary {
  id: string;
  kind: ReportKind;
  title: string;
  verdict: Verdict;
  target: string;
  created_at: number;
  session: string;
  counts: Record<Severity, number>;
  sources: number;
  checks: number;
  thumbnail: string | null;
}

export const reportsKey = ["opencode-reports"];
export const KIND_LABEL: Record<ReportKind, string> = { ui_verify: "UI 検証", research: "精密調査", review: "レビュー" };
export const SEVERITIES: Severity[] = ["critical", "major", "minor", "info"];
export const SEVERITY_LABEL: Record<Severity, string> = { critical: "重大", major: "重要", minor: "軽微", info: "参考" };
/** 赤は重大だけ。重要は黄、残りは灰色。 */
export const SEVERITY_STYLE: Record<Severity, string> = {
  critical: "bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-300",
  major: "bg-amber-50 text-amber-800 dark:bg-amber-950/40 dark:text-amber-300",
  minor: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  info: "bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400",
};

export function reportImageUrl(id: string, name: string): string {
  return `/api/v1/opencode/reports/${encodeURIComponent(id)}/images/${encodeURIComponent(name)}`;
}

export function KindIcon({ kind, className }: { kind: ReportKind; className?: string }) {
  const Icon = kind === "ui_verify" ? IconImages : kind === "research" ? IconBookOpen : IconBranch;
  return <Icon className={className} />;
}

const VERDICT: Record<Verdict, { label: string; cls: string; path: string }> = {
  pass: { label: "合格", cls: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300", path: "m5 12 5 5 9-10" },
  fail: { label: "要修正", cls: "bg-amber-50 text-amber-800 dark:bg-amber-950/40 dark:text-amber-300", path: "M12 8v5m0 3.5v.5M10.3 3.9 2.6 17.5A2 2 0 0 0 4.3 20.5h15.4a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" },
  partial: { label: "一部未確認", cls: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300", path: "M5 12h14" },
};

/** 判定は色・記号・文字の3つで示す。 */
export function VerdictBadge({ verdict }: { verdict: Verdict }) {
  const v = VERDICT[verdict] ?? VERDICT.partial;
  return (
    <span className={`inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-medium ${v.cls}`}>
      <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d={v.path} /></svg>
      {v.label}
    </span>
  );
}

export function SeverityChips({ counts }: { counts?: Partial<Record<Severity, number>> }) {
  const shown = SEVERITIES.filter((key) => (counts?.[key] ?? 0) > 0);
  if (shown.length === 0) return null;
  return <>
    {shown.map((key) => (
      <span key={key} className={`whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-medium tabular-nums ${SEVERITY_STYLE[key]}`}>
        {SEVERITY_LABEL[key]} {counts?.[key]}
      </span>
    ))}
  </>;
}

export function timeAgo(ts?: number | null): string {
  if (!ts) return "";
  const s = Date.now() / 1000 - ts;
  if (s < 60) return "たった今";
  if (s < 3600) return `${Math.floor(s / 60)} 分前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 時間前`;
  if (s < 86400 * 30) return `${Math.floor(s / 86400)} 日前`;
  return new Date(ts * 1000).toLocaleDateString("ja-JP");
}
