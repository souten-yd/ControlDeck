import type { ComponentType, SVGProps } from "react";
import {
  IconBook,
  IconBranch,
  IconChart,
  IconChip,
  IconFile,
  IconGrid,
  IconHome,
  IconLogs,
  IconSettings,
  IconTerminal,
} from "./components/icons";
import { PRODUCT_NAMES } from "./constants/productNames";

export interface NavigationItem {
  to: string;
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
  { to: "/", label: "Home", icon: IconHome },
  { to: "/apps", label: "Apps", icon: IconGrid },
  { to: "/workflows", label: "Workflows", icon: IconFlow, anyPermission: ["workflows.run", "workflows.edit"] },
  { to: "/applications", label: PRODUCT_NAMES.appStudio, icon: IconGrid, permission: "application_builder.view" },
  { to: "/project-lab", label: "Project Lab", icon: IconCode, permission: "project_lab.view" },
  { to: "/remote", label: "Remote", icon: IconRemote },
  { to: "/files", label: "Files", icon: IconFile },
  { to: "/terminal", label: "Terminal", icon: IconTerminal },
  { to: "/assistant", label: "AI Assistant", icon: IconAssistant },
  { to: "/github", label: "GitHub", icon: IconBranch },
  { to: "/knowledge", label: "Knowledge", icon: IconBook },
  { to: "/models", label: "Models", icon: IconChip },
  { to: "/opencode", label: "OpenCode", icon: IconCode, feature: "opencode" },
  { to: "/logs", label: "Logs", icon: IconLogs },
  { to: "/system", label: "System", icon: IconChart },
  { to: "/settings", label: "Settings", icon: IconSettings },
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
