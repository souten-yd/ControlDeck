import { expect, test } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;
const enabled = process.env.CONTROL_DECK_RELEASE_BUNDLE_E2E === "1";

test("manages an installed release bundle and generates through its Add-on", async ({ page, request }) => {
  test.setTimeout(240_000);
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
  const embedded = page.frameLocator('iframe[title="Media Forge — workspace"]');
  await expect(embedded.locator("html")).toHaveAttribute("data-bridge", "ready");
  await embedded.getByRole("button", { name: "Models", exact: true }).click();
  await expect(embedded.locator(".info-card").filter({ hasText: "FLUX.2-klein-4B" }))
    .toContainText("available · installed · Apache-2.0");

  const mediaUrl = process.env.CONTROL_DECK_RELEASE_BUNDLE_MEDIA_URL ?? "http://127.0.0.1:9130";
  const assetsBefore = await request.get(`${mediaUrl}/api/v1/assets`).then((response) => response.json());
  await embedded.getByRole("button", { name: "Create", exact: true }).click();
  await embedded.getByLabel("作りたい画像").fill("Cute orange fox mascot icon, flat anime style, dark blue background");
  await embedded.getByRole("button", { name: "生成する" }).click();
  await expect(embedded.getByText("running", { exact: false })).toBeVisible({ timeout: 30_000 });
  await expect.poll(async () => {
    const value = await request.get(`${mediaUrl}/api/v1/assets`).then((response) => response.json());
    return value.items.length;
  }, { timeout: 180_000 }).toBe(assetsBefore.items.length + 1);
  const assetsAfter = await request.get(`${mediaUrl}/api/v1/assets`).then((response) => response.json());
  const newest = assetsAfter.items[0];
  await embedded.getByRole("button", { name: "Library", exact: true }).click();
  const asset = embedded.locator(".asset-card").filter({ hasText: newest.id });
  await expect(asset.locator("img")).toBeVisible();
  await asset.getByRole("button", { name: "Provenance" }).click();
  await expect(embedded.locator("#provenance")).toContainText("black-forest-labs/FLUX.2-klein-4B");
  await expect(embedded.locator("#provenance")).toContainText('"media-forge": "0.1.1"');
  expect(runtimeErrors).toEqual([]);
});
