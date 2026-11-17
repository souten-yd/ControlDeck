import type { ComponentType, SVGProps } from "react";
import {
  IconBook,
  IconBranch,
  IconChart,
  IconChip,
  IconFile,
  IconFlask,
  IconGrid,
  IconHome,
  IconLogs,
  IconPackage,
  IconSettings,
  IconTerminal,
} from "./components/icons";
import { PRODUCT_NAMES } from "./constants/productNames";

/** ドロワー／サイドバーでのまとまり。並びは NAV_GROUPS の順。 */
export type NavigationGroup = "main" | "dev" | "ai" | "system";

export const NAV_GROUPS: { id: NavigationGroup; label: string }[] = [
  { id: "main", label: "メイン" },
  { id: "dev", label: "開発" },
  { id: "ai", label: "AI" },
  { id: "system", label: "管理" },
];

export interface NavigationItem {
  to: string;
  group: NavigationGroup;
  label: string;
  icon: ComponentType<SVGProps<SVGSVGElement>>;
  feature?: string;
  permission?: string;
  anyPermission?: string[];
}

export function canAccessNavigationItem(item: NavigationItem, can: (permission: string) => boolean): boolean {
  if (item.permission && !can(item.permission)) return false;
  if (item.anyPermission && !item.anyPermission.some(can)) return false;
  return true;
}

export const NAVIGATION: NavigationItem[] = [
  { to: "/", group: "main", label: "Home", icon: IconHome },
  { to: "/apps", group: "main", label: "Apps", icon: IconGrid, permission: "apps.view" },
  { to: "/workflows", group: "dev", label: "Workflows", icon: IconFlow, anyPermission: ["workflows.run", "workflows.edit"] },
  { to: "/applications", group: "dev", label: PRODUCT_NAMES.appStudio, icon: IconPackage, permission: "application_builder.view" },
  { to: "/project-lab", group: "dev", label: "Project Lab", icon: IconFlask, permission: "project_lab.view" },
  { to: "/remote", group: "main", label: "Remote", icon: IconRemote, permission: "remote_desktop.use" },
  { to: "/files", group: "main", label: "Files", icon: IconFile, permission: "files.view" },
  { to: "/terminal", group: "dev", label: "Terminal", icon: IconTerminal, permission: "terminal.use" },
  { to: "/assistant", group: "ai", label: "AI Assistant", icon: IconAssistant },
  { to: "/github", group: "dev", label: "GitHub", icon: IconBranch, permission: "apps.view" },
  { to: "/knowledge", group: "ai", label: "Knowledge", icon: IconBook, permission: "workflows.run" },
  { to: "/models", group: "ai", label: "Models", icon: IconChip, permission: "workflows.run" },
  { to: "/opencode-v2", group: "dev", label: "OpenCode", icon: IconCode, feature: "opencode-v2" },
  { to: "/logs", group: "system", label: "Logs", icon: IconLogs },
  { to: "/system", group: "system", label: "System", icon: IconChart },
  { to: "/settings", group: "system", label: "Settings", icon: IconSettings },
];

export function IconFlow(props: SVGProps<SVGSVGElement>) {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" width="1em" height="1em" aria-hidden {...props}><rect x="2" y="4" width="7" height="6" rx="1.5" /><rect x="15" y="14" width="7" height="6" rx="1.5" /><path d="M9 7h4a2 2 0 0 1 2 2v5" /></svg>;
}

export function IconCode(props: SVGProps<SVGSVGElement>) {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" width="1em" height="1em" aria-hidden {...props}><polyline points="16 18 22 12 16 6" /><polyline points="8 6 2 12 8 18" /></svg>;
}

export function IconRemote(props: SVGProps<SVGSVGElement>) {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" width="1em" height="1em" aria-hidden {...props}><rect x="2" y="4" width="20" height="13" rx="2" /><path d="M8 21h8M12 17v4" /></svg>;
}

export function IconAssistant(props: SVGProps<SVGSVGElement>) {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" width="1em" height="1em" aria-hidden {...props}><path d="M12 3l1.4 4.1L17.5 8.5l-4.1 1.4L12 14l-1.4-4.1-4.1-1.4 4.1-1.4L12 3z" /><path d="M18.5 14l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8.8-2.2z" /><path d="M5 15l.6 1.7 1.7.6-1.7.6L5 19.5l-.6-1.6-1.7-.6 1.7-.6L5 15z" /></svg>;
}
