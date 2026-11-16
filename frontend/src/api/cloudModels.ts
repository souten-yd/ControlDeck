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
  /** 入力の扱い（OpenRouter だけ。ほかは null）。ゲートウェイが要求ごとに強制する。 */
  data_policy?: CloudDataPolicy | null;
}

/** zdr = 保存しない提供元だけ / deny = 収集・学習する提供元へは送らない（既定）/ allow = 制限しない。 */
export type CloudDataPolicy = "zdr" | "deny" | "allow";

/** zdr = 保存しない経路がある / may_store = 保存されることがある / may_train = 学習・記録に使われうる（無料）/ direct = そのプロバイダの規約。 */
export type CloudPrivacy = "zdr" | "may_store" | "may_train" | "direct";

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
  data_policy?: CloudDataPolicy;
}

export interface CloudRemoteModel {
  id: string;
  name: string;
  context_length: number | null;
  free: boolean;
  /** 公開日（unix 秒）。 */
  created?: number | null;
  /** USD / 100 万トークン。 */
  price_input?: number | null;
  price_output?: number | null;
  privacy?: CloudPrivacy;
  /** 提供元（ID の "/" より前）。 */
  vendor?: string;
  /** パラメータ数（10 億単位）。名前から読み取れないものは null。 */
  params_b?: number | null;
  /** 実際に動くパラメータ数（MoE の a3b など）。 */
  active_params_b?: number | null;
  /** 入力できる種類（text / image / audio / file …）。 */
  modalities?: string[];
  /** 道具の呼び出し（function calling）に対応。 */
  tools?: boolean;
  /** 推論（思考）に対応。 */
  reasoning?: boolean;
}

/** プロバイダ側の残高と使用額（OpenRouter だけ）。ControlDeck が数えた使用額とは別物。 */
export type CloudAccount =
  | { supported: false }
  | {
    supported: true;
    /** アカウントの残高（入金 − 使用、USD）。取得できなければ null。 */
    balance: number | null;
    credits_total: number | null;
    credits_used: number | null;
    /** キーの利用上限（USD）。null は上限なし。 */
    limit: number | null;
    limit_remaining: number | null;
    usage: number | null;
    usage_monthly: number | null;
    usage_daily: number | null;
    is_free_tier: boolean;
    /** 無料モデルの今日の残り回数。 */
    free_requests_remaining: number | null;
  };

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

export const getCloudAccount = (provider: string) =>
  api<CloudAccount>(`/models/cloud/${encodeURIComponent(provider)}/account`);

export const getCloudUsage = (days = 30) =>
  api<{ rows: CloudUsageRow[] }>(`/models/cloud-usage?days=${days}`);

/** OpenCode / AI アシスタントが選ぶときの名前。 */
export const cloudGatewayId = (provider: string, model: string) => `cloud:${provider}/${model}`;
