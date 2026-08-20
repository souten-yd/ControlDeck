import { useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";

export type AddonStateName =
  | "installed_disabled" | "enabling" | "setup_required" | "healthy"
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
  endpoint?: string;
  icon?: string | null;
  order?: number;
  mobile?: "embedded" | "companion" | "link_out";
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
  contributions?: Record<string, EffectiveContribution[]>;
}

export interface AddonActivity {
  at: number;
  method: string;
  result: string;
  metadata: Record<string, number>;
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
