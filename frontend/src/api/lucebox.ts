import { api } from "./client";

/** ROCmトラック。初期値は rocm10（AMDLucebox の本番候補ビルド）。 */
export type LuceboxTrack = "rocm10" | "rocm7";

export interface LuceboxInstance {
  alias: string;
  runtime: "lucebox";
  role: "llm";
  model_path: string;
  draft_path: string;
  port: number;
  max_ctx: number;
  draft_block_size: number;
  cache_type_k: string;
  cache_type_v: string;
  fa_window: number;
  ddtree: boolean;
  ddtree_budget: number;
  default_max_tokens: number;
  draft_residency: "auto" | "persistent" | "request-scoped";
  fast_rollback: boolean;
  /** 送信直前に temperature を 0 へ固定し、投機デコードを常に効かせる。 */
  prefer_speculative: boolean;
  /** 生成したツール呼び出しの先まで prefix キャッシュを延ばす（エージェント用）。 */
  agent_turn_cache: boolean;
  auto_start: boolean;
  idle_exclude: boolean;
  order: number;
  loaded: boolean;
  runtime_status: string;
  unit: string;
  base_url: string;
  selected: boolean;
  health?: { ok: boolean };
}

export interface LuceboxEnvironment {
  gpu_supported: boolean;
  gfx: string;
  gfx_targets: number[];
  kfd: boolean;
  rocm_version: string;
  rocm_major: number | null;
  available: boolean;
  reason: string;
}

export interface LuceboxStatus {
  installed: boolean;
  tag: string;
  track: LuceboxTrack;
  track_label: string;
  tracks: Array<{ id: LuceboxTrack; label: string; rocm_major: number; summary: string }>;
  recommended_track: LuceboxTrack;
  default_track: LuceboxTrack;
  sha256: string;
  upstream: string;
  installed_at: string;
  installed_versions: Array<{ tag: string; track: LuceboxTrack; label: string; current: boolean }>;
  server_path: string | null;
  instances: LuceboxInstance[];
  selected_alias: string;
  environment: LuceboxEnvironment;
  warning: string;
  /** alias -> ツール利用に関する警告（fa_window>0 など）。空なら問題なし。 */
  tool_warnings: Record<string, string>;
  experimental: boolean;
  /** AMDLucebox の実測プロファイルに合わせた推奨初期値。新規登録の既定に使う。 */
  defaults: Omit<LuceboxInstance, "runtime" | "role" | "loaded" | "runtime_status" | "unit" | "base_url" | "selected">;
}

export type LuceboxInstanceInput = Partial<Pick<LuceboxInstance,
  "alias" | "model_path" | "draft_path" | "port" | "max_ctx" | "draft_block_size"
  | "cache_type_k" | "cache_type_v" | "fa_window" | "ddtree" | "ddtree_budget"
  | "default_max_tokens" | "draft_residency" | "fast_rollback" | "prefer_speculative"
  | "agent_turn_cache" | "auto_start" | "idle_exclude" | "order">>;

export const getLuceboxStatus = () => api<LuceboxStatus>("/models/lucebox/status");

export const listLuceboxInstances = () => api<LuceboxInstance[]>("/models/lucebox/instances");

export const createLuceboxInstance = (body: LuceboxInstanceInput) =>
  api<LuceboxInstance>("/models/lucebox/instances", { method: "POST", json: body });

export const updateLuceboxInstance = (alias: string, body: LuceboxInstanceInput) =>
  api<LuceboxInstance>(`/models/lucebox/instances/${encodeURIComponent(alias)}`,
                       { method: "PUT", json: body });

export const deleteLuceboxInstance = (alias: string, deleteFile: boolean) =>
  api<{ ok: boolean; gguf_deleted: boolean; reason: string }>(
    `/models/lucebox/instances/${encodeURIComponent(alias)}/delete`,
    { method: "POST", json: { delete_file: deleteFile } });

export const reorderLuceboxInstances = (order: string[]) =>
  api("/models/lucebox/instances/reorder", { method: "POST", json: { order } });

export const switchLuceboxVersion = (tag: string, track: LuceboxTrack) =>
  api<{ tag: string; track: string; server: string }>("/models/lucebox/switch",
                                                      { method: "POST", json: { tag, track } });
