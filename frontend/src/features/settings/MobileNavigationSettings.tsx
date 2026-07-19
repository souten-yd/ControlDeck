import { useMemo } from "react";
import { useMeta } from "../../api/hooks";
import { NAVIGATION } from "../../navigation";
import { useAuth } from "../../stores";
import { MOBILE_NAV_MAX_ITEMS, useMobileNavigation } from "../../stores/mobileNavigation";

export function MobileNavigationSettings() {
  const can = useAuth((state) => state.can);
  const { data: meta } = useMeta();
  const paths = useMobileNavigation((state) => state.paths);
  const setPaths = useMobileNavigation((state) => state.setPaths);
  const reset = useMobileNavigation((state) => state.reset);
  const enabledFeatures = useMemo(() => new Set(meta?.enabled_features ?? []), [meta]);
  const available = NAVIGATION.filter((item) =>
    (!item.feature || enabledFeatures.has(item.feature)) && (!item.permission || can(item.permission)),
  );
  const availablePaths = new Set(available.map((item) => item.to));
  const selected = paths.filter((path) => availablePaths.has(path));

  const updateSelected = (next: string[]) => {
    const unavailable = paths.filter((path) => !availablePaths.has(path));
    setPaths([...next, ...unavailable].slice(0, MOBILE_NAV_MAX_ITEMS));
  };
  const toggle = (path: string) => {
    if (selected.includes(path)) updateSelected(selected.filter((item) => item !== path));
    else if (selected.length < MOBILE_NAV_MAX_ITEMS) updateSelected([...selected, path]);
  };
  const move = (index: number, offset: -1 | 1) => {
    const target = index + offset;
    if (target < 0 || target >= selected.length) return;
    const next = [...selected];
    [next[index], next[target]] = [next[target], next[index]];
    updateSelected(next);
  };

  return (
    <section aria-labelledby="bottom-navigation-settings-title" className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900 md:p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 id="bottom-navigation-settings-title" className="text-sm font-semibold text-zinc-500">Bottom Navigation</h2>
          <p className="mt-1 text-xs leading-relaxed text-zinc-400">この端末の下部メニューを最大6件まで選択できます。Moreは常に右端へ表示されます。</p>
        </div>
        <button type="button" onClick={reset} className="min-h-11 shrink-0 rounded-xl px-3 text-xs font-medium text-accent-600 hover:bg-accent-50 dark:hover:bg-accent-600/10">Reset</button>
      </div>

      <div className="mt-4 rounded-xl bg-zinc-50 p-2 dark:bg-zinc-950">
        <div className="mb-2 flex items-center justify-between px-1">
          <span className="text-xs font-medium">表示順</span>
          <span className="num text-xs text-zinc-400">{selected.length} / {MOBILE_NAV_MAX_ITEMS}</span>
        </div>
        {selected.length === 0 ? <p className="rounded-lg border border-dashed border-zinc-300 p-4 text-center text-xs text-zinc-400 dark:border-zinc-700">Moreのみ表示します。下の候補から機能を選択してください。</p> : (
          <ol className="space-y-1">
            {selected.map((path, index) => {
              const item = available.find((candidate) => candidate.to === path)!;
              const Icon = item.icon;
              return <li key={path} className="flex min-h-12 items-center gap-2 rounded-xl border border-zinc-200 bg-white px-2 dark:border-zinc-800 dark:bg-zinc-900">
                <span className="num w-5 text-center text-xs text-zinc-400">{index + 1}</span>
                <Icon className="shrink-0 text-lg text-zinc-500" />
                <span className="min-w-0 flex-1 truncate text-sm font-medium">{item.label}</span>
                <button type="button" onClick={() => move(index, -1)} disabled={index === 0} aria-label={`${item.label}を上へ移動`} title="Move up" className="grid h-11 w-11 place-items-center rounded-lg text-lg disabled:opacity-25">↑</button>
                <button type="button" onClick={() => move(index, 1)} disabled={index === selected.length - 1} aria-label={`${item.label}を下へ移動`} title="Move down" className="grid h-11 w-11 place-items-center rounded-lg text-lg disabled:opacity-25">↓</button>
              </li>;
            })}
          </ol>
        )}
      </div>

      <p className="mb-2 mt-4 text-xs font-medium text-zinc-500">機能を選択</p>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {available.map((item) => {
          const active = selected.includes(item.to);
          const disabled = !active && selected.length >= MOBILE_NAV_MAX_ITEMS;
          const Icon = item.icon;
          return <button key={item.to} type="button" onClick={() => toggle(item.to)} disabled={disabled} aria-pressed={active} className={`flex min-h-12 min-w-0 items-center gap-2 rounded-xl border px-3 text-left transition disabled:cursor-not-allowed disabled:opacity-35 ${active ? "border-accent-500 bg-accent-50 text-accent-700 dark:bg-accent-600/10 dark:text-accent-300" : "border-zinc-200 hover:border-zinc-400 dark:border-zinc-700"}`}>
            <Icon className="shrink-0 text-lg" />
            <span className="min-w-0 flex-1 truncate text-xs font-medium">{item.label}</span>
            <span aria-hidden className="shrink-0 text-sm">{active ? "✓" : "+"}</span>
          </button>;
        })}
      </div>
    </section>
  );
}
