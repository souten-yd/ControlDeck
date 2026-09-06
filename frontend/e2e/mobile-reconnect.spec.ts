import { expect, test } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

/** 携帯で別のアプリへ移って戻る動き。OS が経路を切っても socket は OPEN の
 *  まま残るので、readyState を信用すると復帰が何十秒も遅れる。 */
test("returning from the background reconnects within seconds", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials are required");
  await establishSession(page, context);
  await page.setViewportSize({ width: 390, height: 780 });
  await page.goto("/terminal", { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(4000);

  const badge = () => page.evaluate(() => document.body.innerText.includes("再接続中"));
  expect(await badge()).toBe(false);

  const setHidden = (hidden: boolean) => page.evaluate((value) => {
    Object.defineProperty(document, "visibilityState", { value: value ? "hidden" : "visible", configurable: true });
    Object.defineProperty(document, "hidden", { value, configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
  }, hidden);

  await setHidden(true);
  await context.setOffline(true);
  await page.waitForTimeout(5000);
  await context.setOffline(false);
  await setHidden(false);

  // 数秒で戻ること
  for (let waited = 0; waited < 8000; waited += 500) {
    if (!(await badge())) { console.log(`復帰まで ${waited}ms`); return; }
    await page.waitForTimeout(500);
  }
  expect(await badge(), "8秒経っても再接続中のまま").toBe(false);
});

test("a brief blip does not flash the badge", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials are required");
  await establishSession(page, context);
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(4000);

  await context.setOffline(true);
  await page.waitForTimeout(700);
  await context.setOffline(false);
  await page.waitForTimeout(800);
  const flashed = await page.evaluate(() => document.body.innerText.includes("再接続中"));
  console.log("一瞬の途切れで表示に出たか:", flashed);
  expect(flashed, "すぐ戻る途切れで再接続中を出している").toBe(false);
});
