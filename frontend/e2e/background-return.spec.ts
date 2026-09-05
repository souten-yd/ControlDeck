import { expect, test } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

/** 携帯で背面に回して戻すと「再接続中」が居座っていた。
 *  戻ったときは socket の readyState を信用せず、必ず繋ぎ直す。 */
test("returning from the background reconnects quickly", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials are required");
  await establishSession(page, context);

  await page.goto("/terminal", { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(4000);
  const reconnecting = () => page.evaluate(() => document.body.innerText.includes("再接続中"));
  expect(await reconnecting()).toBe(false);

  const setHidden = (hidden: boolean) => page.evaluate((value) => {
    Object.defineProperty(document, "visibilityState", { value: value ? "hidden" : "visible", configurable: true });
    Object.defineProperty(document, "hidden", { value, configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
  }, hidden);

  // 背面のあいだに経路を殺す。socket は OPEN のまま取り残されることがある。
  await setHidden(true);
  await context.setOffline(true);
  await page.waitForTimeout(6000);
  await context.setOffline(false);
  await setHidden(false);

  // 45 秒の watchdog を待たずに戻ること
  await page.waitForTimeout(6000);
  expect(await reconnecting(), "復帰から6秒経っても再接続中のまま").toBe(false);
  expect(await page.locator("li").count()).toBeGreaterThan(0);
});
