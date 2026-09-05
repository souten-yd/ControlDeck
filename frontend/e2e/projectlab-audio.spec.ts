import { expect, test } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

/** 音声の成果物は再生器を出す。
 *
 * 以前は Centered（文言用の grid）に入れており、place-items-center が中身を内容幅
 * まで縮めていた。audio は内容幅を持たないので幅 0 になり、携帯では押せなかった。
 * 読み込みは成功していたぶん、見た目だけでは気づけない壊れ方だった。
 */
test("an audio artifact gets a player wide enough to press", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials are required");
  await establishSession(page, context);
  await page.goto("/project-lab", { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(3000);

  const audio = page.locator("audio").first();
  if (!(await audio.count())) {
    test.skip(true, "音声を含むプロジェクトが無い");
  }
  await expect(audio).toBeVisible();
  const width = await audio.evaluate((el) => el.getBoundingClientRect().width);
  expect(width, "再生器の幅が 0 だと押せない").toBeGreaterThan(100);
});
