import { expect, test } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

test("llama.cpp instance save excludes read-only status fields", async ({ page }) => {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");

  await page.goto("/models");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
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
  await expect(common.getByText("全ランタイム共通")).toBeVisible();
  await expect(common.getByText("Deep Research専用CTX")).toHaveCount(0);
  await expect(common.getByLabel("統合する根拠文字数上限")).toHaveValue("90000");
  await expect(common.getByLabel("レポート総出力token上限")).toHaveValue("32768");
  await expect(common.getByLabel("Deep Research生成timeout（秒）")).toHaveValue("1800");
  await common.getByRole("button", { name: "閉じる" }).click();

  const status = await page.evaluate(async () => (await fetch("/api/v1/models/llama/status")).json());
  const alias = status.selected_alias as string;
  await page.getByRole("listitem").first().getByRole("button").first().click();
  const sheet = page.getByRole("dialog", { name: `${alias} · モデル個別設定` });
  await expect(sheet.getByText("Deep Research専用CTX", { exact: true })).toBeVisible();
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
