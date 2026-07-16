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

  await page.getByRole("button", { name: "LLM ランタイム設定" }).click();
  const sheet = page.getByRole("dialog", { name: "LLM ランタイム設定" });
  await expect(sheet.getByText("llama.cppモデル個別設定")).toBeVisible();
  await sheet.getByRole("button", { name: "保存", exact: true }).click();
  await expect.poll(() => submitted).not.toBeNull();

  expect(submitted).toMatchObject({ model_path: expect.any(String), alias: expect.any(String), port: expect.any(Number) });
  for (const key of ["selected", "loaded", "unit", "runtime_status", "base_url", "last_used_at"]) {
    expect(submitted).not.toHaveProperty(key);
  }

  await page.unroute("**/api/v1/models/llama/instances/*");
  await page.route("**/api/v1/models/llama/instances/*", (route) => route.fulfill({
    status: 422,
    contentType: "application/json",
    body: JSON.stringify({ detail: [{ loc: ["body", "alias"], msg: "入力値を確認してください" }] }),
  }));
  await sheet.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByText("alias: 入力値を確認してください")).toBeVisible();
});
