import { expect, test, type Page } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

async function login(page: Page) {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  await page.goto("/login");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page).not.toHaveURL(/\/login/);
}

test("shows only catalogued health commands without accepting argv", async ({ page }) => {
  await page.route("**/api/v1/apps/health-commands", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify([{ id: "fixed-self-check", label: "Fixed self-check" }]),
  }));
  await login(page);

  for (const viewport of [{ width: 320, height: 700 }, { width: 1280, height: 800 }]) {
    await page.setViewportSize(viewport);
    await page.goto("/apps?add=1");
    const drawer = page.getByRole("dialog", { name: /アプリを追加/ });
    await drawer.getByPlaceholder("My LLM Server").fill("Command health preview");
    await drawer.getByLabel("既存 systemd サービス").check();
    await drawer.getByRole("button", { name: "次へ" }).click();
    await drawer.getByPlaceholder("my-service.service").fill("preview-health.service");
    await drawer.getByLabel("ヘルスチェック種別").selectOption("command");
    await drawer.getByLabel("許可コマンド").selectOption("fixed-self-check");
    await expect(drawer.getByLabel("許可コマンド")).toHaveValue("fixed-self-check");
    await expect(drawer).toContainText("固定されたargvだけをsystemd user unitで実行します");
    await expect(drawer.locator('input[placeholder*="argv" i], input[placeholder*="command" i]')).toHaveCount(0);
    const overflow = await drawer.evaluate((element) => element.scrollWidth - element.clientWidth);
    expect(overflow).toBeLessThanOrEqual(1);
    await drawer.getByRole("button", { name: "閉じる" }).click();
  }
});
