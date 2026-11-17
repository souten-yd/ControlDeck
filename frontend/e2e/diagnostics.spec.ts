import { expect, test } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;
const shots = process.env.CONTROL_DECK_E2E_SHOTS;

for (const width of [1280, 390]) {
  test(`diagnostics page shows self-test, restarts and a redacted report (${width}px)`, async ({ page }) => {
    test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
    test.setTimeout(120_000);
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/");
    await page.getByLabel("ユーザー名").fill(username!);
    await page.getByLabel("パスワード").fill(password!);
    await page.getByRole("button", { name: "ログイン", exact: true }).click();
    await expect(page.getByLabel("ユーザー名")).toBeHidden();
    await page.goto("/diagnostics");
    await expect(page.getByRole("heading", { name: "自己診断" })).toBeVisible({ timeout: 60_000 });
    for (const title of ["本体の再起動", "OOM で強制終了", "応答の詰まり（画面が遅くなる原因）", "テスト"]) {
      await expect(page.getByRole("heading", { name: new RegExp(title.replace(/[()（）]/g, ".")) })).toBeVisible();
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    const report = await page.evaluate(async () => (await fetch("/api/v1/diagnostics/report?format=md", { credentials: "same-origin" })).text());
    expect(report).toContain("# ControlDeck 診断レポート");
    expect(report).not.toMatch(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/);
    if (shots) await page.screenshot({ path: `${shots}/diagnostics-${width}.png`, fullPage: true });
  });
}
