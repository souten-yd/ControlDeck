import type { ComponentType, ReactNode, SVGProps } from "react";
import { useMeta } from "./api/hooks";
import { addonLabel, useEffectiveAddons } from "./api/addons";
import { addonIcon, IconPlugin } from "./components/icons";
import { AddonStatusChip, addonStateMessage } from "./features/addons/AddonStatus";
import { canAccessNavigationItem, NAV_GROUPS, NAVIGATION } from "./navigation";
import { useAuth } from "./stores";

type NavIcon = ComponentType<SVGProps<SVGSVGElement>>;

/** ドロワー／サイドバーの 1 行。内部の画面も、別タブで開くプラグインも同じ形にする。
 *  key は本体が行き先のパス、Add-on が `addon-<id>-<cid>`、プラグインが `plugin-<id>`。 */
export interface NavEntry {
  key: string;
  to: string;
  label: string;
  icon: NavIcon;
  external?: boolean;
  title?: string;
  trailing?: ReactNode;
}

export interface NavSection {
  id: string;
  label: string;
  entries: NavEntry[];
}

/** メニューに出しうる全項目を、まとまり（本体・拡張機能）ごとに返す。
 *  権限・feature・2 要素登録待ちの絞り込みまでを済ませ、「よく使う」・非表示の適用はしない。
 *  ドロワー／サイドバーも設定 → 表示の選択欄もこれを使うので、新しい機能・Add-on・プラグインは
 *  ここに出れば、個別の対応なしに表示の選択へ並ぶ。 */
export function useNavigationCandidates(): { sections: NavSection[]; visibleNav: typeof NAVIGATION } {
  const user = useAuth((s) => s.user);
  const can = useAuth((s) => s.can);
  const { data: meta } = useMeta();
  const { data: effectiveAddons } = useEffectiveAddons(true);
  const addonById = new Map((effectiveAddons?.addons ?? []).map((addon) => [addon.id, addon]));
  const addonNavigation = effectiveAddons?.contributions.navigation ?? [];
  const enabledFeatures = new Set(meta?.enabled_features ?? []);
  const enrollmentPending = Boolean(user?.totp_required && !user.totp_enabled);
  const visibleNav = NAVIGATION.filter((item) =>
    (!enrollmentPending || item.to === "/settings")
    && (!item.feature || enabledFeatures.has(item.feature))
    && canAccessNavigationItem(item, can));

  const extensions: NavEntry[] = [
    ...addonNavigation.map((contribution): NavEntry => {
      const addon = addonById.get(contribution.addon_id);
      const label = addonLabel(contribution.label);
      return {
        key: `addon-${contribution.addon_id}-${contribution.id}`,
        to: contribution.route || `/x/${contribution.addon_id}/${contribution.id}`,
        label,
        icon: addonIcon(contribution.icon, contribution.addon_id),
        title: `${label}${addon && addon.state !== "healthy" ? ` — ${addonStateMessage(addon.state)}` : ""}`,
        trailing: addon ? <AddonStatusChip state={addon.state} compact /> : undefined,
      };
    }),
    ...(meta?.plugin_navigation ?? []).filter((plugin) => can(plugin.permission)).map((plugin): NavEntry => ({
      key: `plugin-${plugin.id}`, to: plugin.url, label: plugin.label, icon: IconPlugin, external: true,
    })),
  ];
  const sections: NavSection[] = [];
  for (const group of NAV_GROUPS) {
    const entries = visibleNav.filter((item) => item.group === group.id)
      .map((item): NavEntry => ({ key: item.to, to: item.to, label: item.label, icon: item.icon }));
    if (entries.length) sections.push({ id: group.id, label: group.label, entries });
    /* 拡張機能は AI の次、システムの前。 */
    if (group.id === "ai" && extensions.length && !enrollmentPending) {
      sections.push({ id: "extensions", label: "拡張機能", entries: extensions });
    }
  }
  return { sections, visibleNav };
}
