import { expect, test } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;
const enabled = process.env.CONTROL_DECK_RELEASE_BUNDLE_E2E === "1";

test("manages an installed release bundle and exposes its Add-on without reload", async ({ page }) => {
  test.skip(!enabled || !username || !password, "installed release-bundle E2E environment is required");
  const runtimeErrors: string[] = [];
  page.on("pageerror", (error) => runtimeErrors.push(error.message));

  await page.goto("/login");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page).not.toHaveURL(/\/login$/);
  await page.goto("/settings");

  const section = page.getByRole("heading", { name: "オプション機能" }).locator("xpath=ancestor::section[1]");
  const row = section.locator("div.rounded-xl.border").filter({ hasText: "Media Forge" }).first();
  await expect(row).toContainText("v0.1.1");
  await expect(row).toContainText("導入済み（無効）");
  await expect(row).not.toContainText("PREVIEW");
  await row.getByRole("button", { name: "有効化" }).click();
  await expect(row.getByRole("button", { name: "無効化" })).toBeVisible();

  await page.setViewportSize({ width: 1280, height: 800 });
  await expect(page.getByRole("link", { name: /Media/ }).first()).toBeVisible();
  await page.getByRole("link", { name: /Media/ }).first().click();
  await expect(page).toHaveURL(/\/x\/media-forge\/workspace$/);
  await expect(page.getByText("セットアップが必要", { exact: true })).toBeVisible();
  await expect(page.getByText("Media Forge environment", { exact: true })).toBeVisible();
  expect(runtimeErrors).toEqual([]);
});
