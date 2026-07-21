import { expect, test, type Page } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;
const replacement = process.env.CONTROL_DECK_E2E_NEW_PASSWORD;

async function login(page: Page, credential: string) {
  await page.goto("/login");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(credential);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page).not.toHaveURL(/\/login/);
}

test("changes password and revokes the current session without viewport overflow", async ({ page }) => {
  test.skip(!username || !password || !replacement, "E2E credentials are required");
  const browserErrors: string[] = [];
  page.on("pageerror", (error) => browserErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  await login(page, password!);

  const viewports = [{ width: 320, height: 700 }, { width: 1280, height: 800 }];
  for (const [index, viewport] of viewports.entries()) {
    await page.setViewportSize(viewport);
    await page.goto("/settings");
    await page.getByRole("button", { name: "パスワードを変更" }).click();
    const dialog = page.getByRole("dialog", { name: "パスワードを変更" });
    await expect(dialog).toBeVisible();
    const layout = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth }));
    expect(layout.document).toBeLessThanOrEqual(layout.viewport);
    if (index === 0) {
      await dialog.getByRole("button", { name: "閉じる" }).click();
      continue;
    }
    await dialog.getByLabel("現在のパスワード").fill(password!);
    await dialog.getByLabel("新しいパスワード（8文字以上）").fill(replacement!);
    await dialog.getByLabel("新しいパスワード（確認）").fill(replacement!);
    await dialog.getByRole("button", { name: "変更して全端末からログアウト" }).click();
    await expect(page).toHaveURL(/\/login/);
  }

  await login(page, replacement!);
  expect(browserErrors).toEqual([]);
});
