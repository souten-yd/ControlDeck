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
  /** 全slot合算の生成速度。並列で回したときに全体でどれだけ出ているかを見る。 */
  tokens_per_second: number;
  /** 直近1リクエストの速度。合計との差が並列化の効き具合になる。 */
  tokens_per_second_single: number;
  /** いま何をしているか。前処理と生成は速さの桁が違う（実測 ROCm で
   * 前処理 約 1,000 tok/s、生成 約 50 tok/s）ので、同じ「tok/s」として
   * 出すと読めない。 */
  phase: "idle" | "prefill" | "think" | "generate";
  /** プロンプトを読む速さ。生成の速さとは別に数える。 */
  prefill_tokens_per_second: number;
  /** 読んでいる最中の slot 数。 */
  prefilling: number;
  /** 読むべきプロンプトの総量と、そのうち読み終えた量。 */
  prompt_tokens: number; prompt_tokens_done: number;
  /** Strata / Maya-S / Radiance: ControlDeck times the requests it relays (no KV usage). */
  runtime?: string; timed_by_controldeck?: boolean;
  /** Strata / Maya-S / Radiance: 実行中（無ければ直前）の 1 本の「プロンプト + 生成」。KV の使用量ではない。 */
  ctx_active?: number | null;
}

/** 動いているモデルが無いときの、最後に見えていたローカル LLM（at は最後に見えた時刻、UNIX 秒）。 */
export type LastLocal = Pick<EndpointCapacity, "running_alias"> & Partial<EndpointCapacity> & { at?: number };

/** LLM が処理していた間の PC 全体の電力と電気代（PSU のコンセント側換算）。 */
export interface LlmPower {
  enabled: boolean;
  busy?: boolean;
  /** 処理中だけ。 */
  now_w?: number | null;
  /** 今日、処理していた間の平均（積算した電力量 ÷ 積算した時間）。 */
  avg_w?: number | null;
  /** 平均の電力での 1 時間あたり。 */
  yen_per_hour?: number | null;
  today_yen?: number;
  today_seconds?: number;
}


/** 起動に失敗して再試行待ちのモデル。読み込み中と紛らわしいので理由付きで分けて出す。 */
export interface FailedInstance {
  alias: string;
  port: number;
  error: string;
}

export const getLlamaCapacity = () =>
  api<{ endpoints: EndpointCapacity[]; failed?: FailedInstance[]; power?: LlmPower; last_local?: LastLocal | null }>(
    "/models/llama/capacity");

// ---- ランタイム共通の目録・取得・登録・設定（backend/app/models_mgmt/runtime_catalog.py・
// runtime_settings.py・schemas/models.py の契約。docs/PLAN-model-catalog.md「run3」） ----

/** 目録の 1 行。size_gib が null のときは大きさを推測しない（取得の開始時に HF から読む）。 */
export interface RuntimeCatalogEntry {
  id: string;
  name: string;
  kind: "model" | "drafter";
  format: string;
  repo: string;
  quantization: string;
  size_gib: number | null;
  memory_gib: number | null;
  uncensored: boolean;
  supported: boolean;
  note: string;
  acquire: "hf" | "runtime" | "none";
  source: string;
  fit: "fits" | "tight" | "no" | "unknown" | string;
  /** 登録先の系列（Strata の family・Maya の tier） */
  family?: string;
  files?: string[];
}

/** 目録のモデルの量子化（カードの中で選ぶ行）。id は取得・登録の API に渡す目録の行の id。 */
export interface RuntimeCatalogVariant {
  id: string;
  label: string;
  quantization: string;
  acquire: "hf" | "runtime" | "maya" | "none";
  size_gib: number | null;
  memory_gib: number | null;
  fit: "fits" | "tight" | "no" | "unknown" | string;
  supported: boolean;
  reason: string;
  repo: string;
  format: string;
  kind: "model" | "drafter";
  family: string;
  files: string[];
  state: "available" | "downloaded" | "registered";
  uncensored: boolean;
  /** 量子化ごとに違う説明（カードの説明と違うときだけ） */
  note: string;
  /** 第三者の版のとき作った人（例: SC117） */
  maker?: string;
  /** ランタイム本体のおすすめ（Strata） */
  recommended?: boolean;
  /** 登録済みの名前（Maya） */
  registered?: string;
  /** Maya の取得先のフォルダ名 */
  folder_name?: string;
}

/** 目録のモデル（画面の 1 枚のカード）。2026-11-28 に量子化ごとのカードから作り直した。 */
export interface RuntimeCatalogModel {
  id: string;
  name: string;
  description: string;
  /** 開発者向けのメモ（エキスパートだけ表示） */
  note_expert: string;
  uncensored: boolean;
  /** 無検閲の量子化がある・検閲ありの量子化がある（両方あればカードの中で切り替える） */
  has_uncensored?: boolean;
  has_censored?: boolean;
  tags?: string[];
  gated: boolean;
  kind: "model" | "drafter";
  recommended: boolean;
  recommended_variant: string;
  size_min_gib: number | null;
  size_max_gib: number | null;
  variants: RuntimeCatalogVariant[];
}

export interface RuntimeCatalogDetailsVariant {
  id: string;
  size_gib: number | null;
  fit: string;
  supported: boolean;
  reason: string;
  state: "available" | "downloaded" | "registered";
  local_path: string;
}

export interface RuntimeCatalogDetails {
  model_id: string;
  variants: RuntimeCatalogDetailsVariant[];
  hardware: { ram_total_gib: number | null; vram_total_gib: number | null };
  libraries: ModelLibrary[];
}

/** カードを開いたときだけ読む（HF の大きさ。backend が 24 時間覚える）。 */
export const getRuntimeCatalogDetails = (runtime: string, modelId: string) =>
  api<RuntimeCatalogDetails>(
    `/models/catalog/${encodeURIComponent(runtime)}/details?model=${encodeURIComponent(modelId)}`);

export interface RuntimeCatalogBlock {
  id: string;
  name: string;
  installed: boolean;
  formats: string[];
  acquire: "hf" | "runtime" | "none";
  entries: RuntimeCatalogEntry[];
  /** モデルごとにまとめた目録（画面はこちらを使う） */
  models?: RuntimeCatalogModel[];
  /** 取得の既定の置き場（Maya の「取得して登録」のフォルダの親） */
  download_root?: string;
  /** 動的な全一覧（Maya の probe など）への既存 API。 */
  catalog_ref?: string;
  /** HF 検索の既存 API（目録に推奨行が無いランタイム）。 */
  search_ref?: string;
}

export interface RuntimeCatalogResponse {
  runtimes: RuntimeCatalogBlock[];
  hardware?: { ram_total_gib: number; vram_total_gib: number };
}

export const getRuntimeCatalog = () => api<RuntimeCatalogResponse>("/models/catalog");

export interface RuntimeCatalogFiles {
  repo: string;
  files: Array<{ path: string; size: number }>;
  total_bytes: number;
  libraries: ModelLibrary[];
}

export const getRuntimeCatalogFiles = (runtime: string, entryId: string) =>
  api<RuntimeCatalogFiles>(
    `/models/catalog/${encodeURIComponent(runtime)}/entries/${encodeURIComponent(entryId)}/files`);

/** 取得後に登録する場合の欄（backend schemas/models.py::AcquireRegister）。
 *  runtime ごとに使える欄が違う。Maya は登録そのものが無いので渡さない。 */
export interface AcquireRegister {
  alias: string;
  role?: "llm" | "embedding" | "reranker";
  endpoint_id?: string;
  port?: number;
  context?: number;
  ctx_size?: number;
  n_parallel?: number;
  max_num_seqs?: number;
  kv_cache?: string;
  quantization?: string;
  gpu_memory_utilization?: number;
  prefill_chunk?: number;
  reasoning_effort?: string;
}

export interface AcquireJobBody {
  runtime: string;
  entry_id: string;
  library_id?: string;
  files?: string[];
  register?: AcquireRegister;
}

export const startRuntimeAcquire = (body: AcquireJobBody) =>
  api<{ job_id: string; repo: string }>("/models/acquire-jobs", { method: "POST", json: body });

/** 共通の一覧（GET /models/instances）。既存の各ランタイム API はそのまま。 */
export interface RuntimeInstance {
  runtime: string;
  alias: string;
  role?: string;
  model_path: string;
  format: string;
  size_bytes: number;
  loaded: boolean;
  port: number;
  ctx_size: number;
  n_parallel: number;
  base_url: string;
  selected?: boolean;
  display_name?: string;
  error?: string;
}

export const listRuntimeInstances = () =>
  api<{ instances: RuntimeInstance[] }>("/models/instances");

/** ファイルから登録する前の読み取り（GET /models/register/inspect）。 */
export interface RuntimeRegisterView {
  runtime: string;
  eligible: boolean;
  reason: string;
  register_endpoint: string;
}

export interface RegisterInspect {
  path: string;
  kind: "file" | "folder" | "unknown" | string;
  format: "gguf" | "safetensors" | "rad" | "unknown" | string;
  alias_suggestion: string;
  size_bytes: number;
  metadata: Record<string, unknown>;
  runtimes: RuntimeRegisterView[];
}

export const inspectRegisterPath = (path: string) =>
  api<RegisterInspect>(`/models/register/inspect?path=${encodeURIComponent(path)}`);

/** 設定の形（GET /models/settings/capabilities）。欄の id は保存 API の引数名と同じ。 */
export interface CapabilityField {
  id: string;
  label: string;
  type: "bool" | "select" | "int" | "float" | "text" | "path" | "ref" | "dict" | string;
  default: unknown;
  expert: boolean;
  choices?: Array<string | number>;
  /** 別の欄の値ごとの選択肢（Strata の量子化は系列ごと） */
  choices_by?: { field: string; map: Record<string, Array<string | number>> };
  /** 選択肢の表示名（無ければ値そのもの） */
  choice_labels?: Record<string, string>;
  min?: number;
  max?: number;
  step?: number;
  note?: string;
}

export interface CapabilitySection {
  id: "model" | "context" | "parallel" | "speed" | "launch" | "expert" | string;
  label: string;
  fields: CapabilityField[];
}

export interface RuntimeCapabilities {
  sections: CapabilitySection[];
  /** 常時オンで切る欄が無いもの（Sly の prefix cache など）。 */
  always_on?: Array<{ id: string; note: string }>;
  /** 既存保存 API が受け付ける引数名。capability に無い既存値を落とさないために使う。 */
  save_keys?: string[];
  endpoints?: { instances?: string };
}

export const getRuntimeCapabilities = () =>
  api<{ runtimes: Record<string, RuntimeCapabilities> }>("/models/settings/capabilities");

/** 取得ジョブの進捗と操作（/jobs）。停止は .part を残し、取消は消す。 */
export interface ModelJob {
  id: string;
  kind: string;
  title: string;
  status: string;
  phase?: string | null;
  wait_reason?: string | null;
  progress: { status?: string; completed?: number | null; total?: number | null };
  error: string;
  can_resume?: boolean;
  result?: Record<string, unknown>;
}

export const getModelJob = (jobId: string) =>
  api<ModelJob>(`/jobs/${encodeURIComponent(jobId)}`);

/** 取得ジョブの一覧（画面を開き直したあと停止中の Job を再表示する）。 */
export const listModelJobs = (kind: string) =>
  api<ModelJob[]>(`/jobs?kind=${encodeURIComponent(kind)}&limit=30`);

export const stopModelJob = (jobId: string) =>
  api<{ ok: boolean; status: string; can_resume: boolean }>(
    `/jobs/${encodeURIComponent(jobId)}/stop`, { method: "POST" });

export const resumeModelJob = (jobId: string) =>
  api<{ ok: boolean; job_id: string }>(
    `/jobs/${encodeURIComponent(jobId)}/resume`, { method: "POST" });

export const cancelModelJob = (jobId: string) =>
  api<{ ok: boolean }>(`/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" });
