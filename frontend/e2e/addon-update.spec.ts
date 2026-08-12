import { expect, test, type Page } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page).not.toHaveURL(/\/login/);
}

test("updates the installed OpenCode addon from the settings tab", async ({ page }) => {
  test.skip(!username || !password, "E2E credentials are required");
  const browserErrors: string[] = [];
  page.on("pageerror", (error) => browserErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  await login(page);

  for (const viewport of [{ width: 320, height: 700 }, { width: 1280, height: 800 }]) {
    await page.setViewportSize(viewport);
    await page.goto("/settings");
    await expect(page.getByRole("button", { name: "更新", exact: true })).toBeVisible();
    const layout = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth }));
    expect(layout.document).toBeLessThanOrEqual(layout.viewport);
  }

  // 更新はサーバー側jobで進むため、完了トーストまで待って結果版数を確認する。
  await page.getByRole("button", { name: "更新", exact: true }).click();
  await expect(page.getByText(/更新中…/)).toBeVisible();
  await expect(page.getByText(/へ更新しました|すでに最新版です/)).toBeVisible({ timeout: 180_000 });
  await expect(page.getByText(/^v\d+\.\d+\.\d+$/).first()).toBeVisible();
  expect(browserErrors).toEqual([]);
});
