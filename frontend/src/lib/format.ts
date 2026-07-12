export function formatBytes(n: number | null | undefined, digits = 1): string {
  if (n == null) return "N/A";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(i === 0 ? 0 : digits)} ${units[i]}`;
}

export function formatBps(n: number | null | undefined): string {
  if (n == null) return "N/A";
  return `${formatBytes(n)}/s`;
}

export function formatUptime(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  const s = Math.floor(seconds);
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return `${d}日 ${h}時間`;
  if (h > 0) return `${h}時間 ${m}分`;
  if (m > 0) return `${m}分`;
  return `${s}秒`;
}

export function formatPercent(v: number | null | undefined): string {
  if (v == null) return "N/A";
  return `${v.toFixed(0)}%`;
}
