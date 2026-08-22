import { useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";

export type AddonStateName =
  | "installed_disabled" | "disable_pending" | "enabling" | "setup_required" | "healthy"
  | "degraded" | "unavailable" | "incompatible";

export interface AddonHealthAction {
  kind: "retry" | "open_route" | "open_logs" | "disable" | "documentation";
  route?: string | null;
}

export interface AddonAvailabilityDetail {
  state: "degraded" | "unavailable";
  reason_code: string;
  message: string;
  action: AddonHealthAction;
}

export interface AddonSetupItem {
  id: string;
  label: string;
  state: "ok" | "missing" | "error" | "checking";
  detail?: string | null;
  message?: string | null;
  action?: AddonHealthAction | null;
}

export interface AddonHealth {
  status: "healthy" | "degraded" | "unavailable" | "setup_required";
  contract_version: "2.0";
  reason_code?: string | null;
  message?: string | null;
  action?: AddonHealthAction | null;
  contributions: Record<string, "available" | "degraded" | "unavailable" | AddonAvailabilityDetail>;
  setup: AddonSetupItem[];
}

export type AddonLabel = string | { en?: string; ja?: string };

export interface EffectiveContribution {
  addon_id: string;
  id: string;
  label: AddonLabel;
  permission: string;
  availability: "available" | "degraded" | "unavailable";
  route?: string;
  path?: string;
  endpoint?: string;
  icon?: string | null;
  order?: number;
  mobile?: "embedded" | "companion" | "link_out";
  contexts?: Array<"file" | "project" | "workflow" | "job">;
}

export interface EffectiveAddon {
  id: string;
  name: string;
  state: AddonStateName;
  health: AddonHealth | null;
}

export interface EffectiveAddons {
  revision: number;
  etag: string;
  addons: EffectiveAddon[];
  contributions: Record<string, EffectiveContribution[]>;
}

export interface InstalledAddon {
  api_version: "2";
  id: string;
  name: string;
  version?: string;
  description?: string;
  publisher?: string;
  installed: boolean;
  enabled: boolean;
  state: AddonStateName;
  requested_capabilities?: string[];
  host_capabilities?: string[];
  granted_capabilities: string[];
  warnings: string[];
  health: AddonHealth | null;
  health_checked_at: number | null;
  contributions?: Record<string, Array<Partial<EffectiveContribution> & Pick<EffectiveContribution, "id" | "label">>>;
}

export interface AddonActivity {
  at: number;
  method: string;
  result: string;
  metadata: Record<string, number>;
}

export interface AddonBridgeSession {
  addon_id: string;
  view_id: string;
  bridge_version: "1.0";
  session_nonce: string;
  expires_in: number;
  allowed_methods: string[];
}

export function openAddonBridge(addonId: string, viewId: string) {
  return api<AddonBridgeSession>(`/addons/${encodeURIComponent(addonId)}/bridge/handshake`, {
    method: "POST",
    json: { bridge_version: "1.0", view_id: viewId },
  });
}

export function authorizeAddonBridgeCall(addonId: string, session: AddonBridgeSession, method: string, params: Record<string, unknown>) {
  return api<{ ok: true; method: string; has_permission?: boolean }>(`/addons/${encodeURIComponent(addonId)}/bridge/call`, {
    method: "POST",
    json: {
      bridge_version: "1.0",
      session_nonce: session.session_nonce,
      view_id: session.view_id,
      method,
      params,
    },
  });
}

export interface AddonFileGrant {
  grant_id: string;
  kind: "read" | "export";
  name: string;
  size: number | null;
  expires_at: number;
}

export function createAddonFileGrant(addonId: string, path: string, kind: "read" | "export") {
  return api<AddonFileGrant>(`/addons/${encodeURIComponent(addonId)}/file-grants`, {
    method: "POST",
    json: { path, kind },
  });
}

export interface AddonCommandResult {
  route: string | null;
  result: Record<string, unknown>;
}

/** 宣言された command / quick action を実行する。route が返れば host が遷移する。 */
export function invokeAddonCommand(
  addonId: string,
  contributionId: string,
  kind: "commands" | "quick_actions" = "quick_actions",
) {
  return api<AddonCommandResult>(
    `/addons/${encodeURIComponent(addonId)}/commands/${encodeURIComponent(contributionId)}/invoke`,
    { method: "POST", json: { kind, input: {} } },
  );
}

export function addonLabel(label: AddonLabel): string {
  const resolved = typeof label === "string" ? label : label.ja || label.en || "拡張機能";
  const characters = Array.from(resolved.replace(/[\u0000-\u001f\u007f]/g, "").trim());
  return characters.length > 24 ? `${characters.slice(0, 23).join("")}…` : characters.join("") || "拡張機能";
}

export function useEffectiveAddons(stream = false) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["addons-effective"],
    queryFn: () => api<EffectiveAddons>("/addons/effective"),
    staleTime: 30_000,
    refetchOnWindowFocus: true,
  });
  useEffect(() => {
    if (!stream) return;
    const source = new EventSource("/api/v1/addons/effective/events");
    source.addEventListener("addons.effective.changed", (event) => {
      try {
        const incoming = JSON.parse((event as MessageEvent<string>).data) as { etag?: string };
        const current = queryClient.getQueryData<EffectiveAddons>(["addons-effective"]);
        if (!current || current.etag !== incoming.etag) {
          void queryClient.invalidateQueries({ queryKey: ["addons-effective"] });
        }
      } catch {
        void queryClient.invalidateQueries({ queryKey: ["addons-effective"] });
      }
    });
    return () => source.close();
  }, [queryClient, stream]);
  return query;
}

export function useInstalledAddons() {
  return useQuery({ queryKey: ["addons"], queryFn: () => api<InstalledAddon[]>("/addons") });
}
