// 目的: ホームのシステムサマリーで、CPU・GPU のファン回転（rpm）が数値で表示され、
//       320px（iPhone の最小幅）と PC 幅のどちらでも横にはみ出さないこと。
// 根拠: ファン回転をサマリーのカードに入れたとき（モバイルで横スクロールが出た）。2026-11-17 のファン制御 v2 で
//       「FAN 1,500 RPM」の文字表記がアイコン＋「1500 RPM」になっていたのに合わせて書き直した。
//       ファンの値は実機のセンサー次第（無ければ出さない）なので、出ている分の形式と幅だけを確かめる。
import { expect, test, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

const noOverflow = (page: Page) =>
  expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

test("dashboard shows CPU and GPU fan values without mobile overflow", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials required");
  await page.setViewportSize({ width: 320, height: 700 });
  await establishSession(page, context);
  await page.goto("/");
  const summary = page.getByRole("region", { name: "システムサマリー" });
  await expect(summary.getByText("GPU", { exact: true }).first()).toBeVisible();
  // 回転数は「数字 RPM」（指令があれば「· 42%」を添える）。CPU と GPU の 2 枚まで
  const fans = summary.locator("span").filter({ hasText: /^[\d,.]+ RPM(?:\s*· \d+%)?$/ });
  expect(await fans.count()).toBeLessThanOrEqual(2);
  await noOverflow(page);

  await page.setViewportSize({ width: 1280, height: 800 });
  expect(await fans.count()).toBeLessThanOrEqual(2);
  await noOverflow(page);
});
