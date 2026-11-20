// 目的:
// - 携帯幅で、左から右へのフリックでメニューが開き、右から左で閉じる。設定 → 操作性で切ると働かない。
//   縦のスクロール・横にスクロールできる所（設定のカテゴリの並び）で始めた指では開かない。
// - 題名を押すと本文の先頭へ戻る。
// - ホームのグラフ: 受け取りが途切れたら（画面を閉じていた間）、サーバーの履歴を読み直して埋める。
//   v0.98 の後: 真ん中からゆっくり引いても指に付いて開く。左右の端の指は iPhone の「戻る」「進む」にさせない。
// 根拠: 2026-11-20 利用者「左から右へフリックでメニューを出し、右から左で閉じたい。オンオフは設定で」
//       v0.98 の後「Claude は真ん中から右へ引いても出る。いまは左端からだと戻る、右端からだと進むになる」
//       「ブラウザを閉じてまた開くと、閉じていた間のグラフが更新されない」。
import { expect, test, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

test.use({ hasTouch: true, isMobile: true, viewport: { width: 390, height: 800 } });

/** 指 1 本で引く（touchstart → touchmove を steps 回 → touchend）。start の位置の要素から始める。
 *  touchstart が止められたか（端で iPhone の「戻る」にさせないため）を返す */
async function flick(page: Page, from: { x: number; y: number }, to: { x: number; y: number }, opts: { steps?: number; stepMs?: number } = {}) {
  return page.evaluate(async ({ from, to, steps, stepMs }) => {
    const target = document.elementFromPoint(from.x, from.y) ?? document.body;
    const touch = (x: number, y: number) => new Touch({ identifier: 1, target, clientX: x, clientY: y });
    const start = new TouchEvent("touchstart", { bubbles: true, cancelable: true, touches: [touch(from.x, from.y)], changedTouches: [touch(from.x, from.y)] });
    target.dispatchEvent(start);
    for (let i = 1; i <= steps; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, stepMs));
      const x = from.x + ((to.x - from.x) * i) / steps;
      const y = from.y + ((to.y - from.y) * i) / steps;
      target.dispatchEvent(new TouchEvent("touchmove", { bubbles: true, cancelable: true, touches: [touch(x, y)], changedTouches: [touch(x, y)] }));
    }
    target.dispatchEvent(new TouchEvent("touchend", { bubbles: true, touches: [], changedTouches: [touch(to.x, to.y)] }));
    return start.defaultPrevented;
  }, { from, to, steps: opts.steps ?? 8, stepMs: opts.stepMs ?? 16 });
}

test("flick opens and closes the menu, and can be turned off", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials required");
  await establishSession(page, context);
  await page.evaluate(() => localStorage.removeItem("cd-ux-prefs-v1"));
  await page.goto("/");
  const drawer = page.locator("[data-nav-drawer]");
  await expect(drawer).toHaveCount(0);
  await flick(page, { x: 60, y: 400 }, { x: 70, y: 600 });                // 縦のスクロール
  await expect(drawer).toHaveCount(0);
  await flick(page, { x: 60, y: 400 }, { x: 260, y: 420 });               // 左 → 右
  await expect(drawer).toHaveAttribute("data-state", "open");
  await flick(page, { x: 370, y: 400 }, { x: 150, y: 410 });              // 暗い所で右 → 左
  await expect(drawer).toHaveCount(0);

  // 真ん中からゆっくり引いても開く（離すまで 1 秒以上かけても、引いた量で決める）
  await flick(page, { x: 180, y: 400 }, { x: 330, y: 410 }, { steps: 20, stepMs: 60 });
  await expect(drawer).toHaveAttribute("data-state", "open");
  await flick(page, { x: 370, y: 400 }, { x: 150, y: 410 });
  await expect(drawer).toHaveCount(0);

  // 少しだけ引いて止めて離したら、開かずに戻る
  await flick(page, { x: 100, y: 400 }, { x: 140, y: 402 }, { steps: 10, stepMs: 60 });
  await page.waitForTimeout(700);
  await expect(drawer).toHaveCount(0);

  // 左端から引いても開く。端の指は止める（iPhone の「戻る」にさせない）
  expect(await flick(page, { x: 6, y: 400 }, { x: 250, y: 410 })).toBe(true);
  await expect(drawer).toHaveAttribute("data-state", "open");
  await flick(page, { x: 370, y: 400 }, { x: 150, y: 410 });
  await expect(drawer).toHaveCount(0);
  // 右端も止める（「進む」にさせない）。真ん中の指は止めない（ふつうのスクロール・タップ）
  expect(await flick(page, { x: 386, y: 400 }, { x: 386, y: 400 }, { steps: 0 })).toBe(true);
  expect(await flick(page, { x: 180, y: 400 }, { x: 180, y: 400 }, { steps: 0 })).toBe(false);

  // 横にスクロールできるカテゴリの並びで始めた指では開かない
  await page.goto("/settings/experience");
  const chips = page.locator("[data-settings-categories]");
  expect(await chips.evaluate((el) => el.scrollWidth > el.clientWidth)).toBe(true);
  const box = (await chips.boundingBox())!;
  await flick(page, { x: box.x + 40, y: box.y + box.height / 2 }, { x: box.x + 240, y: box.y + box.height / 2 });
  await page.waitForTimeout(300);
  await expect(drawer).toHaveCount(0);

  // 設定で切ると働かない
  const panel = page.locator("[data-experience-settings]");
  await panel.getByLabel("フリックでメニューを開閉").click();
  await expect(panel.getByLabel("フリックでメニューを開閉")).not.toBeChecked();
  expect(await flick(page, { x: 6, y: 600 }, { x: 260, y: 610 })).toBe(false);   // 切ると端も元どおり
  await flick(page, { x: 60, y: 600 }, { x: 260, y: 610 });
  await page.waitForTimeout(300);
  await expect(drawer).toHaveCount(0);
  await page.reload();
  await expect(page.locator("[data-experience-settings]").getByLabel("フリックでメニューを開閉")).not.toBeChecked();   // 端末に覚える
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  await page.screenshot({ path: "test-results/ux-experience-390.png" });
});

test("tapping the title scrolls back to the top", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials required");
  await establishSession(page, context);
  await page.goto("/settings/experience");
  await page.evaluate(() => {
    const main = document.querySelector<HTMLElement>("[data-shell-main]")!;
    const spacer = document.createElement("div");
    spacer.style.height = "3000px";
    main.appendChild(spacer);
    main.scrollTop = 1500;
  });
  await page.locator("[data-tap-to-top]").first().click();
  await expect.poll(() => page.evaluate(() => document.querySelector<HTMLElement>("[data-shell-main]")!.scrollTop)).toBe(0);
});

test("home graph reloads server history after the stream was interrupted", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials required");
  await establishSession(page, context);
  let historyCalls = 0;
  await page.route(/\/api\/v1\/system\/metrics\/history/, async (route) => {
    historyCalls += 1;
    await route.continue();
  });
  // 本物の値を流し、4 つ目から時刻を 2 分進める（その間ブラウザを閉じていたのと同じ）
  let count = 0;
  await page.routeWebSocket(/\/system\/metrics\/stream/, (ws) => {
    const server = ws.connectToServer();
    server.onMessage((message) => {
      count += 1;
      try {
        const data = JSON.parse(String(message));
        if (count >= 4 && data.timestamp) data.timestamp = new Date(Date.parse(data.timestamp) + 120_000).toISOString();
        ws.send(JSON.stringify(data));
      } catch {
        ws.send(message);
      }
    });
  });
  await page.goto("/");
  await expect.poll(() => count, { timeout: 20_000 }).toBeGreaterThanOrEqual(4);
  await expect.poll(() => historyCalls, { timeout: 10_000 }).toBeGreaterThanOrEqual(2);
});
