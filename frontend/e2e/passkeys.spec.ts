import { expect, test } from "@playwright/test";

for (const width of [1280, 320]) {
  test(`password-only remembered account and passkey lifecycle ${width}`, async ({ page, context }) => {
    test.skip(!process.env.CONTROL_DECK_PASSKEY_E2E_USER || !process.env.CONTROL_DECK_PASSKEY_E2E_PASSWORD,
      "requires a disposable passkey test account");
    test.setTimeout(60_000);
    await page.setViewportSize({ width, height: 900 });
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const cdp = await context.newCDPSession(page);
    await cdp.send("WebAuthn.enable");
    await cdp.send("WebAuthn.addVirtualAuthenticator", { options: {
      protocol: "ctap2", transport: "internal", hasResidentKey: true,
      hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true,
    } });
    await page.goto("/login");
    await page.getByLabel("ユーザー名").fill(process.env.CONTROL_DECK_PASSKEY_E2E_USER!);
    await page.getByLabel("パスワード", { exact: true }).fill(process.env.CONTROL_DECK_PASSKEY_E2E_PASSWORD!);
    await page.getByRole("button", { name: "ログイン", exact: true }).click();
    await expect(page).not.toHaveURL(/\/login/);
    await page.goto("/settings/account");
    const row = page.getByText("顔認証・パスキー", { exact: true }).locator("../..");
    await row.getByRole("button", { name: "管理", exact: true }).click();
    await page.getByLabel("パスキーの名前").fill(`test-${width}`);
    await page.getByLabel("現在のパスワード", { exact: true }).fill(process.env.CONTROL_DECK_PASSKEY_E2E_PASSWORD!);
    await page.getByRole("button", { name: "パスキーを登録", exact: true }).click();
    await expect(page.getByRole("listitem").filter({ hasText: `test-${width}` })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
    await page.request.post("/api/v1/auth/logout", { headers: { "X-Requested-With": "ControlDeck" } });
    await page.goto("/login");
    await expect(page.getByLabel("ユーザー名")).toBeHidden();
    await page.getByRole("button", { name: "顔認証・パスキーでログイン", exact: true }).click();
    await expect(page).not.toHaveURL(/\/login/);
    await page.goto("/settings/account");
    await page.getByText("顔認証・パスキー", { exact: true }).locator("../..").getByRole("button", { name: "管理", exact: true }).click();
    page.once("dialog", (dialog) => dialog.accept());
    await page.getByRole("listitem").filter({ hasText: `test-${width}` }).getByRole("button", { name: "削除", exact: true }).click();
    await expect(page.getByRole("listitem").filter({ hasText: `test-${width}` })).toHaveCount(0);
    await page.request.post("/api/v1/auth/logout", { headers: { "X-Requested-With": "ControlDeck" } });
    await page.goto("/login");
    await page.getByRole("button", { name: "顔認証・パスキーでログイン", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("パスキーでログインできませんでした");
    await expect(page.getByLabel("ユーザー名")).toBeHidden();
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
    await page.getByLabel("パスワード", { exact: true }).fill(process.env.CONTROL_DECK_PASSKEY_E2E_PASSWORD!);
    await page.getByRole("button", { name: "ログイン", exact: true }).click();
    await expect(page).not.toHaveURL(/\/login/);
    expect(errors).toEqual([]);
  });
}
