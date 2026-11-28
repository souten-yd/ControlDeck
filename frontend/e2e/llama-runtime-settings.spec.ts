/** 目的: llama.cpp の個別設定で保存すると、読み取り専用の状態項目（model_path・loaded など）を
 *        除外した本文だけが PUT されること。422 の詳細が画面に出ること。
 *        Ollama のロード状態が表示とライフサイクルボタンに反映されること。
 *  根拠: 状態項目を送ると保存 API が extra inputs で 422 になる不具合の回帰確認。
 *  #24: 共通 UI の現在のラベルと一致しており変更不要（回帰のみ）。 */
import { expect, test } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

test("llama.cpp instance save excludes read-only status fields", async ({ page }) => {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");

  await page.goto("/models");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン", exact: true }).click();
  await expect(page.getByLabel("ユーザー名")).toBeHidden();
  await page.goto("/models");

  let submitted: Record<string, unknown> | null = null;
  await page.route("**/api/v1/models/llama/instances/*", async (route) => {
    if (route.request().method() !== "PUT") return route.continue();
    submitted = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
  });

  await page.getByRole("button", { name: "LLM 共通設定" }).click();
  const common = page.getByRole("dialog", { name: "LLM 共通設定" });
  await expect(common.getByText("起動と停止", { exact: true })).toBeVisible();
  // 「Deep Research」を含む見出しは 2 つ（コンテキストの量と、根拠・レポートの設定）。後者を開く
  await common.locator("summary", { hasText: "根拠の量" }).click();
  await expect(common.getByText("Deep Research専用CTX")).toHaveCount(0);
  await expect(common.getByText("チャット・ワークフロー生成の出力token上限")).toHaveCount(0);
  await expect(common.getByLabel("根拠の文字数上限")).toHaveValue("90000");
  await expect(common.getByLabel("レポートの長さ上限（トークン）")).toHaveValue("32768");   // 既定（runtime_policy.py）。262144 も選べること（次の行）;
  await expect(common.getByLabel("レポートの長さ上限（トークン）").locator("option[value='262144']")).toHaveCount(1);
  expect(["300", "600", "1200", "1800", "3600"]).toContain(await common.getByLabel("制限時間（秒）").inputValue());
  await common.getByRole("button", { name: "閉じる" }).click();

  const status = await page.evaluate(async () => (await fetch("/api/v1/models/llama/status")).json());
  const alias = status.selected_alias as string;
  // 検証用の環境（./deck.sh e2e）には llama.cpp のモデルが登録されていない。個別設定の保存は登録のある環境でだけ確かめる
  test.skip(!alias, "llama.cpp のモデルが登録されていない環境");
  await page.getByRole("button", { name: `${alias}の個別設定を開く` }).click();
  const sheet = page.getByRole("dialog", { name: `${alias} · 個別設定`, exact: true });
  // 使う頻度の低い項目は「詳細」に畳んである。
  await sheet.locator("summary", { hasText: "詳細" }).click();
  await expect(sheet.getByText("Deep Research専用CTX", { exact: true })).toBeVisible();
  await expect(sheet.getByLabel("コンテキスト長（CTX）").locator("option[value='262144']")).toHaveCount(1);
  await expect(sheet.getByLabel("最大出力トークン").locator("option[value='262144']")).toHaveCount(1);
  await expect(sheet.getByText("通常CTXへ自動復元")).toBeVisible();
  await sheet.getByRole("button", { name: "保存", exact: true }).click();
  await expect.poll(() => submitted).not.toBeNull();

  expect(submitted).toMatchObject({ ctx_size: expect.any(Number), deep_research_ctx_size: expect.any(Number) });
  for (const key of ["model_path", "alias", "port", "auto_start", "idle_exclude", "selected", "loaded", "unit", "runtime_status", "base_url", "last_used_at"]) {
    expect(submitted).not.toHaveProperty(key);
  }

  await page.unroute("**/api/v1/models/llama/instances/*");
  await page.route("**/api/v1/models/llama/instances/*", (route) => route.fulfill({
    status: 422,
    contentType: "application/json",
    body: JSON.stringify({ detail: [{ loc: ["body", "deep_research_ctx_size"], msg: "入力値を確認してください" }] }),
  }));
  await sheet.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByText("deep_research_ctx_size: 入力値を確認してください")).toBeVisible();
});

test("Ollama running state updates the indicator and lifecycle button", async ({ page }) => {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");

  await page.goto("/models");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン", exact: true }).click();
  await expect(page.getByLabel("ユーザー名")).toBeHidden();

  let loaded = false;
  await page.route("**/api/v1/models/runtime-environment", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ policy: { selected_runtime: "ollama", selected_backend: "rocm" }, runtimes: [] }),
  }));
  await page.route("**/api/v1/models/status", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ available: true, version: "test", base_url: "http://127.0.0.1:11434" }),
  }));
  await page.route("**/api/v1/models/running", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify(loaded ? [{ model: "qwen3.6-27b-q5_k_m", digest: "abc", size_vram: 123 }] : []),
  }));
  await page.route("**/api/v1/models", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify([{ name: "qwen3.6-27b-q5_k_m:latest", digest: "abc", size: 456, loaded: false, parameter_size: "27B", quantization: "Q5", family: "qwen", expires_at: null, vram: null }]),
  }));

  await page.goto("/models");
  const row = page.getByRole("listitem").filter({ hasText: "qwen3.6-27b" });
  await expect(row.locator('[title="未ロード"]')).toBeVisible();
  await expect(row.getByRole("button", { name: "ロード", exact: true })).toBeVisible();

  loaded = true;
  await expect(row.locator('[title="ロード中"]')).toBeVisible({ timeout: 5_000 });
  await expect(row.getByRole("button", { name: "アンロード", exact: true })).toBeVisible();

  await page.setViewportSize({ width: 1280, height: 800 });
  await expect(row.locator('[title="ロード中"]')).toBeVisible();
  await expect(row.getByRole("button", { name: "アンロード", exact: true })).toBeVisible();
});
