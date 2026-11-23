import { Toggle } from "../models/formControls";
import { useNavigationCandidates } from "../../navigationEntries";
import { NAV_ALWAYS_VISIBLE, useNavigationVisibility } from "../../stores/navigationVisibility";

/** メニュー（サイドバー・ドロワー）に出す項目を 1 つずつ選ぶ。項目の一覧はメニューと同じ
 *  useNavigationCandidates から作るので、新しい機能・Add-on・プラグインも自動で並ぶ。 */
export function NavigationVisibilitySettings() {
  const { sections } = useNavigationCandidates();
  const hiddenKeys = useNavigationVisibility((state) => state.hidden);
  const setHidden = useNavigationVisibility((state) => state.setHidden);
  const reset = useNavigationVisibility((state) => state.reset);

  return (
    <section aria-labelledby="navigation-visibility-title" className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900 md:p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 id="navigation-visibility-title" className="text-sm font-semibold text-zinc-500">メニューの表示</h2>
          <p className="mt-1 text-xs leading-relaxed text-zinc-400">この端末のメニューに出す項目を選べます。隠した項目も検索（Ctrl K）からは開けます。新しく入った機能・拡張機能は初めから表示されます。</p>
        </div>
        <button type="button" onClick={reset} disabled={hiddenKeys.length === 0} className="min-h-11 shrink-0 rounded-xl px-3 text-xs font-medium text-accent-600 hover:bg-accent-50 disabled:opacity-40 dark:hover:bg-accent-600/10">すべて表示に戻す</button>
      </div>
      {sections.map((section) => (
        <div key={section.id} className="mt-4">
          <h3 className="mb-2 text-xs font-medium text-zinc-500">{section.label}</h3>
          <div className="grid gap-2 sm:grid-cols-2">
            {section.entries.map((entry) => {
              const Icon = entry.icon;
              const locked = NAV_ALWAYS_VISIBLE.includes(entry.key);
              return <Toggle
                key={entry.key}
                label={<span className="flex items-center gap-2"><Icon aria-hidden className="shrink-0 text-lg text-zinc-500" />{entry.label}</span>}
                hint={locked ? "隠せません" : undefined}
                value={!hiddenKeys.includes(entry.key)}
                disabled={locked}
                onChange={(visible) => setHidden(entry.key, !visible)}
              />;
            })}
          </div>
        </div>
      ))}
    </section>
  );
}
