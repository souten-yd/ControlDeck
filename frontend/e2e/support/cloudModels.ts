import type { Page, Route } from "@playwright/test";

/** フロンティアモデルの API を差し替える。外部へは 1 バイトも送らない。
 *
 * 状態は呼び出し側が持つ object を書き換えていくので、「キーを保存 → 有効 → モデルを選ぶ」
 * の続きを 1 本の spec で通せる。受け取った PUT の body は puts に残す。 */
export interface MockCloud {
  providers: Array<Record<string, unknown> & { id: string; models: string[] }>;
  egress: Record<string, boolean>;
  caller_labels: Record<string, string>;
}

export function emptyCloud(): MockCloud {
  const provider = (id: string, name: string, note: string, keys_url: string, base_url: string) => ({
    id, name, note, keys_url, base_url, configured: false, key_hint: "", enabled: false,
    models: [] as string[], monthly_budget_usd: 0, spent_this_month_usd: 0,
  });
  return {
    providers: [
      provider("openrouter", "OpenRouter", "多数のモデル（Claude・GPT・Gemini・オープンモデル）を 1 つのキーで。無料のモデルもある", "https://openrouter.ai/settings/keys", "https://openrouter.ai/api/v1"),
      provider("openai", "OpenAI", "API キー（従量課金）。ChatGPT のプランは Codex CLI で使う", "https://platform.openai.com/api-keys", "https://api.openai.com/v1"),
      provider("anthropic", "Anthropic（Claude）", "API キー（従量課金）。Claude のプランは Claude Code で使う", "https://console.anthropic.com/settings/keys", "https://api.anthropic.com/v1"),
      provider("gemini", "Google Gemini", "API キー", "https://aistudio.google.com/apikey", "https://generativelanguage.googleapis.com/v1beta/openai"),
      provider("custom", "OpenAI 互換（任意）", "OpenAI 互換の API を持つ任意の送信先（HTTPS のみ）", "", ""),
    ],
    egress: { opencode: false, assistant: false, workflows: false, api: false },
    caller_labels: { opencode: "OpenCode", assistant: "AI アシスタント", workflows: "Workflow", api: "ゲートウェイを直接使う外部クライアント" },
  };
}

/** OpenRouter 相当の件数（数千件）。先頭に見分けやすい行を置く。 */
export function remoteModels(count = 1500) {
  const models = [
    { id: "anthropic/claude-test", name: "Claude Test", context_length: 200000, free: false },
    { id: "meta/llama-test:free", name: "Llama Test (free)", context_length: 131072, free: true },
  ];
  for (let i = 0; i < count; i += 1) {
    models.push({ id: `vendor-${i % 40}/model-${i}`, name: `Model ${i}`, context_length: 8192 * (1 + (i % 16)), free: i % 50 === 0 });
  }
  return models;
}

export async function mockCloud(page: Page, state: MockCloud, options: { usage?: unknown[]; remote?: unknown[] } = {}) {
  const puts: Array<{ path: string; body: Record<string, unknown> }> = [];
  const json = (route: Route, body: unknown, status = 200) =>
    route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
  await page.route("**/api/v1/models/cloud", (route) => json(route, state));
  await page.route("**/api/v1/models/cloud/*", (route) => {
    if (route.request().method() !== "PUT") return json(route, { detail: "not mocked" }, 404);
    const id = new URL(route.request().url()).pathname.split("/").pop()!;
    const body = route.request().postDataJSON() as Record<string, unknown>;
    puts.push({ path: id, body });
    const entry = state.providers.find((p) => p.id === id);
    if (!entry) return json(route, { detail: "プロバイダが見つかりません" }, 404);
    if ("api_key" in body) {
      const key = String(body.api_key ?? "");
      entry.configured = key !== "";
      entry.key_hint = key.slice(-4);
      if (!key) entry.enabled = false;
    }
    if (body.enabled === true && !entry.configured) return json(route, { detail: "先に API キーを設定してください" }, 400);
    for (const field of ["enabled", "models", "monthly_budget_usd", "base_url"]) {
      if (field in body) entry[field] = body[field];
    }
    return json(route, state);
  });
  // 後から足した route が先に当たるので、/cloud/* より後ろに置く。
  await page.route("**/api/v1/models/cloud/egress", (route) => {
    const body = route.request().postDataJSON() as Record<string, boolean>;
    puts.push({ path: "egress", body });
    Object.assign(state.egress, body);
    return json(route, state);
  });
  await page.route("**/api/v1/models/cloud/*/remote-models", (route) => json(route, { models: options.remote ?? remoteModels() }));
  await page.route("**/api/v1/models/cloud-usage?*", (route) => json(route, { rows: options.usage ?? [] }));
  return puts;
}
