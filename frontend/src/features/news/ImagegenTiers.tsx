import type { ImagegenTier } from "../../api/news";

/** 大きさ（MB）を GB か MB で。 */
export function sizeText(megabytes: number): string {
  return megabytes >= 1000 ? `約 ${(megabytes / 1000).toFixed(1)}GB` : `約 ${megabytes}MB`;
}

/** 1 枚の目安の時間。 */
export function timeText(seconds: number): string {
  return seconds <= 1 ? "1 秒前後" : `約 ${seconds} 秒`;
}

const TIER_NAME: Record<string, string> = { small: "小", medium: "中", large: "大" };

/** 機能と拡張の「ニュースの画像生成（CPU）」: 精度の段ごとにモデルを導入・削除する（小は機能の最低限なので削除はアンインストールで）。 */
export function ImagegenTierList({ tiers, busy, onInstall, onRemove }: {
  tiers: ImagegenTier[]; busy: boolean; onInstall: (id: ImagegenTier["id"]) => void; onRemove: (id: ImagegenTier["id"]) => void;
}) {
  const btn = "min-h-10 rounded-xl px-3.5 text-xs font-medium disabled:opacity-40";
  return (
    <div data-testid="imagegen-tiers" className="space-y-2">
      <p className="text-xs font-semibold">モデルの精度ごとの導入</p>
      <p className="text-[11px] text-zinc-500">使う精度は 設定 → ニュース で選びます。選んだ精度が未導入なら、画像は作られません（ここで導入してください）。</p>
      <ul className="space-y-2">
        {tiers.map((t) => (
          <li key={t.id} data-testid={`imagegen-tier-${t.id}`} className="flex flex-wrap items-center gap-2 rounded-lg border border-zinc-200 p-2 dark:border-zinc-700">
            <div className="min-w-0 flex-1 basis-48">
              <p className="text-sm font-medium">{TIER_NAME[t.id] ?? t.label}
                <span className={`ml-2 rounded px-1.5 py-0.5 text-[10px] font-medium ${t.installed ? "bg-emerald-600/15 text-emerald-700 dark:text-emerald-400" : "bg-zinc-100 text-zinc-500 dark:bg-zinc-800"}`}>{t.installed ? "導入済み" : "未導入"}</span>
              </p>
              <p className="text-[11px] text-zinc-500">{t.note}</p>
              <p className="text-[11px] text-zinc-500">1 枚 {timeText(t.time_seconds)} · モデル {sizeText(t.megabytes)} · 生成中の RAM 約 {t.ram_gb}GB</p>
            </div>
            {t.installed
              ? t.id !== "small" && <button type="button" onClick={() => onRemove(t.id)} disabled={busy} aria-label={`${TIER_NAME[t.id]}のモデルを削除`}
                  className={`${btn} border border-zinc-200 text-zinc-700 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800`}>削除</button>
              : <button type="button" onClick={() => onInstall(t.id)} disabled={busy} aria-label={`${TIER_NAME[t.id]}のモデルを導入`}
                  className={`${btn} bg-accent-600 text-white hover:bg-accent-700`}>導入（{sizeText(t.megabytes)}）</button>}
          </li>
        ))}
      </ul>
    </div>
  );
}
