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

test("system scope service can only be selected from the installed catalog", async ({ page }) => {
  await page.route("**/api/v1/apps/system-services", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify([{
      id: "remote-desktop", label: "Remote Desktop", unit: "xrdp.service",
      actions: ["start", "stop", "restart"],
    }]),
  }));
  await login(page);

  for (const viewport of [{ width: 320, height: 700 }, { width: 1280, height: 800 }]) {
    await page.setViewportSize(viewport);
    await page.goto("/apps?add=1");
    const drawer = page.getByRole("dialog", { name: /アプリを追加/ });
    await drawer.getByPlaceholder("My LLM Server").fill("Remote Desktop");
    await drawer.getByLabel("既存 systemd サービス").check();
    await drawer.getByRole("button", { name: "次へ" }).click();
    await drawer.getByRole("button", { name: "システム", exact: true }).click();
    const service = drawer.getByLabel("許可済みシステムサービス");
    await expect(service).toHaveValue("remote-desktop");
    await expect(service.locator("option:checked")).toContainText("xrdp.service");
    await expect(drawer.getByPlaceholder("my-service.service")).toHaveCount(0);
    await expect(drawer).toContainText("root所有allowlist");
    expect(await drawer.evaluate((element) => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1);
    await drawer.getByRole("button", { name: "閉じる" }).click();
  }
});
