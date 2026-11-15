import { api } from "./client";

/** フロンティアモデル（外部の API プロバイダ）。キーそのものは返ってこない（末尾 4 文字だけ）。 */
export interface CloudProvider {
  id: string;
  name: string;
  note: string;
  keys_url: string;
  base_url: string;
  configured: boolean;
  /** 保存したキーの末尾 4 文字。 */
  key_hint: string;
  enabled: boolean;
  /** 使うと決めたモデル。ゲートウェイでは `cloud:<provider>/<model>`。 */
  models: string[];
  /** 0 は上限なし。 */
  monthly_budget_usd: number;
  spent_this_month_usd: number;
}

export interface CloudState {
  providers: CloudProvider[];
  /** 呼び出し元ごとに、外部モデルへ送ってよいか。既定はすべて false。 */
  egress: Record<string, boolean>;
  caller_labels: Record<string, string>;
}

export interface CloudProviderPatch {
  /** 空文字はキーの削除（同時に無効になる）。 */
  api_key?: string;
  enabled?: boolean;
  models?: string[];
  monthly_budget_usd?: number;
  base_url?: string;
}

export interface CloudRemoteModel {
  id: string;
  name: string;
  context_length: number | null;
  free: boolean;
}

export interface CloudUsageRow {
  at: number;
  caller: string;
  provider: string;
  model: string;
  status: number;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: number | null;
}

export const CLOUD_QUERY_KEY = ["cloud-models"] as const;

export const getCloudState = () => api<CloudState>("/models/cloud");

export const saveCloudProvider = (provider: string, patch: CloudProviderPatch) =>
  api<CloudState>(`/models/cloud/${encodeURIComponent(provider)}`, { method: "PUT", json: patch });

export const saveCloudEgress = (patch: Record<string, boolean>) =>
  api<CloudState>("/models/cloud/egress", { method: "PUT", json: patch });

export const listCloudRemoteModels = (provider: string) =>
  api<{ models: CloudRemoteModel[] }>(`/models/cloud/${encodeURIComponent(provider)}/remote-models`);

export const getCloudUsage = (days = 30) =>
  api<{ rows: CloudUsageRow[] }>(`/models/cloud-usage?days=${days}`);

/** OpenCode / AI アシスタントが選ぶときの名前。 */
export const cloudGatewayId = (provider: string, model: string) => `cloud:${provider}/${model}`;
