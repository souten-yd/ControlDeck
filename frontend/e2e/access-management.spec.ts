import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;
const managedUsername = "e2e-managed-account";
const roleName = "e2e_app_reader";

async function login(page: Page) {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  await page.goto("/login");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page).not.toHaveURL(/\/login/);
}

async function apiLogin(request: APIRequestContext, credential: string) {
  return request.post("/api/v1/auth/login", {
    headers: { "X-Requested-With": "ControlDeck" },
    data: { username: managedUsername, password: credential },
  });
}

test("creates a custom role and manages a user at mobile and desktop widths", async ({ page, request }) => {
  const browserErrors: string[] = [];
  page.on("pageerror", (error) => browserErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  await login(page);

  await page.setViewportSize({ width: 320, height: 700 });
  await page.goto("/settings");
  await expect(page.getByText("ユーザーとロール", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
  await page.getByRole("button", { name: "Customロールを追加" }).click();
  const roleDialog = page.getByRole("dialog", { name: "Customロールを追加" });
  await roleDialog.getByLabel("Customロール名").fill(roleName);
  await roleDialog.getByRole("checkbox", { name: "apps.view" }).check();
  await roleDialog.getByRole("button", { name: "保存" }).click();
  await expect(page.getByText(roleName, { exact: true })).toBeVisible();

  await page.setViewportSize({ width: 1280, height: 800 });
  await page.getByRole("button", { name: "ユーザー追加" }).click();
  const userDialog = page.getByRole("dialog", { name: "ユーザーを追加" });
  await userDialog.getByLabel("管理ユーザー名").fill(managedUsername);
  await userDialog.getByLabel("管理表示名").fill("E2E Managed User");
  await userDialog.getByLabel("管理パスワード").fill("e2e-managed-password-old");
  await userDialog.getByLabel("管理ロール").selectOption({ label: `${roleName}（1権限）` });
  await userDialog.getByRole("button", { name: "保存" }).click();
  const userRow = page.locator("li").filter({ hasText: managedUsername });
  await expect(userRow).toContainText("E2E Managed User");
  await userRow.getByRole("button", { name: "編集" }).click();
  const editDialog = page.getByRole("dialog", { name: "ユーザーを編集" });
  await editDialog.getByLabel("管理表示名").fill("E2E Updated User");
  await editDialog.getByLabel("管理パスワード").fill("e2e-managed-password-new");
  await editDialog.getByRole("button", { name: "保存" }).click();
  await expect(userRow).toContainText("E2E Updated User");
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);

  expect((await apiLogin(request, "e2e-managed-password-old")).status()).toBe(401);
  expect((await apiLogin(request, "e2e-managed-password-new")).status()).toBe(200);
  expect((await request.get("/api/v1/apps")).status()).toBe(200);
  expect(browserErrors).toEqual([]);
});
