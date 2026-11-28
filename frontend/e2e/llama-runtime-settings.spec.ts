/** 目的: llama.cpp の個別設定（共通 RuntimeSettings）で保存すると、読み取り専用の状態項目
 *        （loaded・runtime_status・base_url など）を除いた本文だけが PUT されること。
 *        422 の詳細が画面に出ること。Ollama のロード状態が表示とライフサイクルボタンに反映されること。
 *  根拠: 状態項目を送ると保存 API が extra inputs で 422 になる不具合の回帰確認。
 *        レビュー #25 / 2026-11-28 の要修正: 旧個別パネルの「詳細」見出し・旧ラベルは共通
 *        RuntimeSettings の「エキスパート」（capability の expert 欄）へ置き換わったので追随した。
 *        隔離環境には llama.cpp の登録が無いため、ステータスは page.route で模擬して skip せず実行する。 */
import { expect, test } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

test("llama.cpp instance save excludes read-only status fields", async ({ page }) => {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");

  await page.addInitScript(() => {
    try { localStorage.setItem("control-deck:commander-expert", "1"); } catch { /* 無視 */ }
  });
  const instance = {
    alias: "e2e-llama", model_path: "/data1tb/LLM/model.gguf", port: 8080, ctx_size: 4096,
    deep_research_ctx_size: 0, n_predict: 2048, n_parallel: 1, role: "llm", loaded: false,
    base_url: "http://127.0.0.1:8080/v1", runtime_status: "STOPPED", unit: "", selected: false,
    last_used_at: null,
  };
  await page.route("**/api/v1/models/llama/status", route => route.fulfill({ json: {
    installed: true, backend: "rocm", selected_alias: instance.alias, instances: [instance],
  } }));
  // 一覧は選択中のランタイムで切り替わる。隔離環境の既定は Ollama なので llama.cpp へ寄せる。
  await page.route("**/api/v1/models/runtime-environment", async route => {
    const env = await (await route.fetch()).json();
    env.policy = { ...env.policy, selected_runtime: "llama.cpp", selected_backend: "rocm" };
    await route.fulfill({ json: env });
  });
  // 一覧に individual settings へのボタンを出すため、登録済みモデルも模擬する。
  await page.route("**/api/v1/models/providers/*/models", route => route.fulfill({ json:
    route.request().url().includes("/llama.cpp/")
      ? [{ id: instance.alias, name: instance.alias, size_bytes: 0, loaded: false,
        details: { runtime: "llama.cpp" } }]
      : [] }));

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
  expect(alias).toBe("e2e-llama");
  await page.getByRole("button", { name: `${alias}の個別設定を開く` }).click();
  const sheet = page.getByRole("dialog", { name: `${alias} · 個別設定`, exact: true });
  // 共通 UI では使う頻度の低い数値は expert 欄（旧「詳細」）として同じ画面に出る。
  await expect(sheet.getByLabel("Deep Research 用の長さ（数値入力）", { exact: true })).toBeVisible();
  await expect(sheet.getByLabel("最大出力トークン（数値入力）", { exact: true })).toHaveValue("2048");
  await sheet.getByLabel("Deep Research 用の長さ（数値入力）", { exact: true }).fill("16384");
  await sheet.getByTestId("runtime-settings-save").click();
  await expect.poll(() => submitted).not.toBeNull();

  expect(submitted).toMatchObject({ ctx_size: 4096, deep_research_ctx_size: 16384, n_predict: 2048 });
  for (const key of ["loaded", "runtime_status", "base_url", "unit", "last_used_at", "selected"]) {
    expect(submitted).not.toHaveProperty(key);
  }

  await page.unroute("**/api/v1/models/llama/instances/*");
  await page.route("**/api/v1/models/llama/instances/*", (route) => route.fulfill({
    status: 422,
    contentType: "application/json",
    body: JSON.stringify({ detail: [{ loc: ["body", "deep_research_ctx_size"], msg: "入力値を確認してください" }] }),
  }));
  // 保存に成功するとシートは閉じる（共通 UI の onDone）。422 の表示は開き直して確かめる。
  await page.getByRole("button", { name: `${alias}の個別設定を開く` }).click();
  const reopened = page.getByRole("dialog", { name: `${alias} · 個別設定`, exact: true });
  await reopened.getByTestId("runtime-settings-save").click();
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
