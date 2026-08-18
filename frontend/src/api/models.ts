import { api } from "./client";

/** モデル置き場の候補になる実機ボリューム。マウント名ではなく uuid で参照する。 */
export interface StorageVolume {
  uuid: string;
  device: string;
  mountpoint: string;
  fstype: string;
  /** nvme / sata / usb など。M.2 の物理スロット番号はソフトから取れないため、これで識別させる。 */
  transport: string;
  model: string;
  rotational: boolean;
  total_bytes: number;
  free_bytes: number;
  writable: boolean;
  /** ルートファイルシステム。既定候補から外して警告を出す。 */
  is_system: boolean;
}

export interface ModelLibrary {
  id: string;
  label: string;
  volume_uuid: string;
  subpath: string;
  path: string;
  default: boolean;
  /** 参照先ボリュームが現在マウントされているか。false なら未接続。 */
  mounted: boolean;
  exists: boolean;
  total_bytes: number | null;
  free_bytes: number | null;
  gguf_count: number;
  gguf_bytes: number;
  /** どの instance からも参照されていない GGUF の件数。 */
  orphan_count: number;
}

export interface LibraryFile {
  name: string;
  path: string;
  size: number;
  used_by: string[];
  registered: boolean;
  suggest_alias: string;
}

export interface ModelLibraryInput {
  id: string;
  label: string;
  volume_uuid?: string;
  subpath?: string;
  path?: string;
  default?: boolean;
}

export const listStorageVolumes = () => api<StorageVolume[]>("/models/storage/volumes");

export const listModelLibraries = () =>
  api<{ libraries: ModelLibrary[] }>("/models/libraries").then((r) => r.libraries);

export const saveModelLibraries = (libraries: ModelLibraryInput[]) =>
  api<{ libraries: ModelLibrary[] }>("/models/libraries", { method: "PUT", json: libraries })
    .then((r) => r.libraries);

export const scanModelLibrary = (id: string) =>
  api<{ id: string; path: string; files: LibraryFile[] }>(
    `/models/libraries/${encodeURIComponent(id)}/scan`,
  );

/** 思考（reasoning）の語彙。バックエンドの models_mgmt/thinking.py と対応する。 */
export type ThinkMode = "auto" | "off" | "low" | "medium" | "high" | "xhigh" | "custom";

/** レベル→トークンバジェットの写像。レベルはプリセット、カスタムは直接指定。 */
export const THINK_LEVEL_BUDGETS: Partial<Record<ThinkMode, number>> = {
  off: 0, low: 1024, medium: 4096, high: 16384, xhigh: 32768,
};

export interface LlamaEndpoint {
  id: string;
  label: string;
  port: number;
  active_alias: string;
  base_url: string;
  aliases: string[];
  running_alias: string;
}

export const listLlamaEndpoints = () => api<LlamaEndpoint[]>("/models/llama/endpoints");

export const reorderLlamaInstances = (order: string[]) =>
  api("/models/llama/instances/reorder", { method: "POST", json: { order } });

export const duplicateLlamaInstance = (alias: string, newAlias: string) =>
  api(`/models/llama/instances/${encodeURIComponent(alias)}/duplicate`,
      { method: "POST", json: { alias: newAlias } });

export const deleteLlamaInstance = (alias: string, deleteFile: boolean) =>
  api<{ ok: boolean; gguf_deleted: boolean; reason: string }>(
    `/models/llama/instances/${encodeURIComponent(alias)}/delete`,
    { method: "POST", json: { delete_file: deleteFile } });

export interface HfRepo {
  repo: string; downloads: number; likes: number; gated: boolean;
}

export interface HfVariant {
  name: string;
  files: string[];
  size: number;
  /** 分割GGUF。1ファイルだけでは使えないのでまとめて扱う。 */
  sharded: boolean;
  shard_total: number;
  /** 分割が揃っているか。欠けていればダウンロードさせない。 */
  complete: boolean;
}

export const searchHfRepos = (q: string) =>
  api<HfRepo[]>(`/models/hf/search?q=${encodeURIComponent(q)}`);

export const listHfRepoFiles = (repo: string) =>
  api<{ repo: string; variants: HfVariant[]; libraries: ModelLibrary[] }>(
    `/models/hf/repos/${repo.split("/").map(encodeURIComponent).join("/")}/files`);

export const startHfDownload = (body: {
  repo: string; files: string[]; library_id?: string; expected_bytes?: number;
  alias?: string; role?: string; port?: number;
}) => api<{ job_id: string }>("/models/hf/download-jobs", { method: "POST", json: body });

export interface EndpointCapacity {
  id: string; label: string; port: number; running_alias: string;
  available: boolean; slots: number; busy: number;
  ctx_total: number; ctx_used: number; ctx_free: number; usable: number;
  deferred: number; accepting: boolean;
}

export interface OmoStatus {
  installed: boolean; model: string;
  concurrency: number; team_parallel: number; gated: boolean;
}

export const getLlamaCapacity = () =>
  api<{ endpoints: EndpointCapacity[]; omo: OmoStatus | null }>("/models/llama/capacity");
