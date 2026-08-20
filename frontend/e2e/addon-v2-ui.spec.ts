import { expect, test, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;
const manifestPath = new URL("../../tools/fake-addon/control-deck-addon.json", import.meta.url);

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page).not.toHaveURL(/\/login/);
}

async function deleteFakeAddon(page: Page) {
  await page.evaluate(async () => {
    await fetch("/api/v1/addons/fake-addon", {
      method: "DELETE",
      credentials: "same-origin",
      headers: { "X-Requested-With": "ControlDeck" },
    });
  });
}

test.afterEach(async ({ page, request }) => {
  await request.post("http://127.0.0.1:9130/test/health", {
    data: { status: "healthy", video_available: true },
  }).catch(() => undefined);
  await deleteFakeAddon(page).catch(() => undefined);
});

test("renders v2 contributions and state without a host reload", async ({ page, request }) => {
  test.setTimeout(90_000);
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  const fakeHealth = await request.get("http://127.0.0.1:9130/health").catch(() => null);
  test.skip(!fakeHealth?.ok(), "the fake add-on service must be running on port 9130");

  const manifest = await readFile(manifestPath, "utf8");
  await login(page);
  await deleteFakeAddon(page);
  await page.goto("/settings");

  await page.getByRole("button", { name: "manifest登録" }).click();
  await page.getByLabel("Extension manifest JSON").fill(manifest);
  await page.getByRole("button", { name: "検証して登録" }).click();
  const extensionsSection = page.getByRole("heading", { name: "拡張機能", exact: true })
    .locator("xpath=ancestor::section[1]");
  const extensionRow = extensionsSection.locator("div.rounded-xl.border")
    .filter({ hasText: "Control Deck Fake Add-on" }).first();
  await expect(extensionRow).toBeVisible();
  await extensionRow.getByRole("button", { name: "有効化" }).click();
  const permissionReview = page.getByRole("dialog", { name: "要求する権限を確認" });
  await expect(permissionReview).toContainText("files.pick");
  await permissionReview.getByRole("button", { name: "許可して有効化" }).click();
  await expect(extensionRow.getByRole("button", { name: "無効化" })).toBeVisible();

  await page.setViewportSize({ width: 320, height: 700 });
  await page.goto("/");
  await page.getByRole("button", { name: "More" }).click();
  const quickActions = page.getByRole("dialog", { name: "Quick Actions" });
  await expect(quickActions.getByRole("button", { name: "テスト拡張" })).toBeVisible();
  await expect(quickActions.getByRole("button", { name: "Quick fake generate" })).toBeVisible();
  await quickActions.getByRole("button", { name: "テスト拡張" }).click();
  await expect(page).toHaveURL(/\/x\/fake-addon\/workspace$/);
  await expect(page.getByRole("heading", { name: "Control Deck Fake Add-on" })).toBeVisible();
  const mobileLayout = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth }));
  expect(mobileLayout.document).toBeLessThanOrEqual(mobileLayout.viewport);

  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/");
  const addonNavigation = page.getByRole("link", { name: /テスト拡張/ });
  await expect(addonNavigation).toBeVisible();
  await page.keyboard.press("Control+k");
  await page.getByLabel("コマンド検索").fill("Fake generate");
  await page.getByRole("button", { name: "Fake generate", exact: true }).click();
  await expect(page).toHaveURL(/\/x\/fake-addon\/generate\?command=generate$/);
  await expect(page.getByText("操作の実行にはまだ対応していません。", { exact: false })).toBeVisible();

  await request.post("http://127.0.0.1:9130/test/health", {
    data: { status: "degraded", video_available: false },
  });
  await page.goto("/settings?extension=fake-addon");
  const details = page.getByRole("dialog", { name: "Control Deck Fake Add-on" });
  await details.getByRole("button", { name: "再確認" }).click();
  await expect(details.getByText("一部機能が利用できません")).toBeVisible();
  await details.getByRole("button", { name: "閉じる" }).click();

  const degradedNavigation = page.getByRole("link", { name: /テスト拡張.*一部機能が利用できません/ });
  await expect(degradedNavigation).toBeVisible();
  await degradedNavigation.click();
  await expect(page.getByText("一部の機能が停止しています。利用可能な機能はそのまま使えます。"))
    .toBeVisible();

  await page.getByRole("button", { name: "権限・詳細を開く" }).click();
  const openDetails = page.getByRole("dialog", { name: "Control Deck Fake Add-on" });
  await page.evaluate(() => { (window as Window & { addonReloadSentinel?: string }).addonReloadSentinel = "present"; });
  await openDetails.getByRole("button", { name: "無効化" }).click();
  await expect(page.getByRole("link", { name: /テスト拡張/ })).toHaveCount(0);
  expect(await page.evaluate(() => (window as Window & { addonReloadSentinel?: string }).addonReloadSentinel)).toBe("present");
});
