import { expect, test, type Page } from "@playwright/test";

/** Models 画面の「切り替え待ち」（ゲートウェイの載せ替えの調停役 /models/swap-queue）。
 * 目的: 別のモデルの要求が待っているときだけ 1 行で出て、待ちが無ければ出ないこと。320px で横にはみ出さないこと。
 * 根拠: 2026-11-22 利用者「載せ替えをインテリジェントに」。調停役は要求を断らず待たせるので、待っている理由を画面で見せる。 */
const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

async function login(page: Page) {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  await page.goto("/login");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/);
}

const waiting = {
  loaded: ["qwen-flash-next"], running: { "qwen-flash-next": 1 }, swapping: null,
  queue: [{ alias: "maya-deepseek-xs", waiting: 3, oldest_wait_seconds: 42, needs_swap: true }],
  next: { alias: "maya-deepseek-xs", reason: "待ち 3 件・最長 42 秒" }, recent_swaps: [], settings: { aging_seconds: 30, prefetch: true },
};
const idle = { ...waiting, running: {}, queue: [], next: null };

for (const width of [320, 1280]) {
  for (const scheme of ["light", "dark"] as const) {
    test(`切り替え待ちの表示 (${width}px ${scheme})`, async ({ page }) => {
      await page.setViewportSize({ width, height: 800 });
      await page.emulateMedia({ colorScheme: scheme });
      let state: unknown = waiting;
      await page.route("**/api/v1/models/swap-queue", (route) => route.fulfill({ json: state }));
      await login(page);
      await page.goto("/models");
      const notice = page.getByTestId("swap-queue-notice");
      await expect(notice).toHaveText("切り替え待ち: maya-deepseek-xs 3 件（最長 42 秒）");
      expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
      await page.screenshot({ path: `test-results/models-swap-queue-${width}-${scheme}.png` });
      state = idle;
      await expect(notice).toHaveCount(0, { timeout: 8000 });
    });
  }
}
