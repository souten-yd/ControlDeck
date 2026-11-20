/** 設定 → 操作性。端末ごとの便利機能のオンオフ（stores/uxPrefs）。
 *
 * 2026-11-20 利用者「フリックでメニューを開閉したい。オンオフは設定で、ユーザーエクスペリエンスのような項目を。
 * そういう便利機能が他にも思い浮かべば追加して」。 */
import { useUxPrefs, type UxPrefs } from "../../stores/uxPrefs";

const ITEMS: { key: keyof UxPrefs; label: string; detail: string }[] = [
  { key: "swipeNav", label: "フリックでメニューを開閉",
    detail: "携帯の画面で、左から右へフリックするとメニューが開き、右から左へフリックすると閉じます。横にスクロールできる所・入力欄・ターミナルでは働きません" },
  { key: "keepAwake", label: "読み上げ・音声入力の間は画面を消さない",
    detail: "AI アシスタントの読み上げ・音声入力・会話モードの間、画面が自動で消えないようにします（iPhone は画面が消えると読み上げが止まるため）" },
  { key: "tapTitleToTop", label: "題名を押すと先頭へ戻る",
    detail: "画面上部の題名を押すと、いま見ている一覧や本文の先頭までスクロールします" },
];

export function ExperienceSettings() {
  const prefs = useUxPrefs();
  return (
    <section className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900 md:p-5" data-experience-settings>
      <h2 className="text-sm font-semibold">操作性</h2>
      <p className="mt-1 text-xs text-zinc-500">この端末だけに効きます（携帯と PC で別々に選べます）。</p>
      <div className="mt-3 space-y-2">
        {ITEMS.map((item) => (
          <label key={item.key} className="flex items-center justify-between gap-4 rounded-xl border border-zinc-200 px-4 py-3 dark:border-zinc-700">
            <span className="min-w-0">
              <span className="block text-sm">{item.label}</span>
              <span className="block text-xs text-zinc-400">{item.detail}</span>
            </span>
            <input type="checkbox" checked={prefs[item.key]} aria-label={item.label}
              onChange={(e) => prefs.set({ [item.key]: e.target.checked })} className="h-5 w-5 shrink-0 accent-current" />
          </label>
        ))}
      </div>
    </section>
  );
}
