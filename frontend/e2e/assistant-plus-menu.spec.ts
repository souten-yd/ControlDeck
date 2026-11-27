// 目的: ＋のメニューが押した「＋」の近く（入力欄の真上）に出ることを守る。画面からはみ出さない・
//       中身が長いときはメニューの中だけがスクロールする・キーボードで殻が縮んでいる間も同じ位置。
// 根拠: 2026-11-27 利用者「＋のメニューが飛ぶ（iPhone。メニューが画面の上の方に出て、下の入力欄の＋から
//       離れる）」。原因は window.innerHeight からの位置計算で、iOS の fixed の基準とずれるため。
//       PlusMenu は fixed の探り要素で基準を実測する直しをした（ここは Chromium で置き方を確かめる。
//       低い画面ではみ出さない・中だけスクロールは assistant-plan.spec.ts の short screen の試験が見る）。
import { expect, test, type Locator, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

const msg = (id: string, role: "user" | "assistant", content: string) => ({
  id, role, content, thinking: "", status: "completed", job_id: null, model: "", error: "", meta: {},
  created_at: "2026-11-27T00:00:00Z",
});

async function openAssistant(page: Page) {
  await page.route(/\/api\/v1\/chat\/conversations\/plus1\/messages$/, (route) =>
    route.fulfill({ json: { conversation: { id: "plus1", title: "＋の位置" }, messages: [msg("u1", "user", "やあ"), msg("a1", "assistant", "はい")] } }));
  await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "plus1"));
  await page.goto("/assistant");
  const shell = page.locator("[data-assistant-shell]");
  const plus = shell.locator("[data-assistant-plus]");
  await expect(plus).toBeVisible();
  return { shell, plus };
}

/** ＋を押してメニューを開く。＋とメニューの矩形（画面座標）を返す */
async function openMenu(page: Page, plus: Locator) {
  await plus.click();
  const menuEl = page.locator("body > div[role='menu']");
  await expect(menuEl).toBeVisible();
  const rects = await page.evaluate(() => {
    const menu = document.querySelector("body > div[role='menu']")!.getBoundingClientRect();
    const plus = document.querySelector("[data-assistant-plus]")!.getBoundingClientRect();
    return { menu: { top: menu.top, bottom: menu.bottom, left: menu.left, right: menu.right, height: menu.height },
             plus: { top: plus.top } };
  });
  return { menuEl, ...rects };
}

for (const width of [390, 1280]) {
  test(`plus menu opens right above the button (${width}px)`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials required");
    await page.setViewportSize({ width, height: 844 });
    await establishSession(page, context);
    const { plus } = await openAssistant(page);
    const { menuEl, menu, plus: p } = await openMenu(page, plus);

    // 下端が＋の上端の近く（±24px）
    expect(menu.bottom, "下端は＋の上端の近く").toBeGreaterThan(p.top - 25);
    expect(menu.bottom).toBeLessThan(p.top + 25);
    // 画面からはみ出さない
    expect(menu.top).toBeGreaterThanOrEqual(0);
    expect(menu.bottom).toBeLessThanOrEqual(844);
    expect(menu.left).toBeGreaterThanOrEqual(0);
    expect(menu.right).toBeLessThanOrEqual(width);
    await expect(menuEl).toHaveAttribute("aria-label", "添付・機能・権限");

    // 外側を押したら閉じる（既存の動きを壊していないこと）
    await page.locator("body").click({ position: { x: 8, y: 8 } });
    await expect(menuEl).toBeHidden();
    await plus.click();
    await expect(menuEl).toBeVisible();
    await page.screenshot({ path: `test-results/plus-menu-${width}.png` });
    await page.emulateMedia({ colorScheme: "dark" });
    await page.screenshot({ path: `test-results/plus-menu-${width}-dark.png` });
  });
}

test("plus menu stays inside the keyboard-shrunk area", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials required");
  // iOS のキーボードは html.cd-keyboard-fit で殻を縮める（useKeyboardViewportFit）。同じ状態を作って確かめる
  await page.setViewportSize({ width: 390, height: 844 });
  await establishSession(page, context);
  const { plus } = await openAssistant(page);
  await page.evaluate(() => {
    const root = document.documentElement;
    root.style.setProperty("--cd-app-height", "420px");
    root.style.setProperty("--cd-app-top", "0px");
    root.classList.add("cd-keyboard-fit");
  });
  const { menu, plus: p } = await openMenu(page, plus);

  // 見えている範囲（0〜420px）からはみ出さない。収まるなら＋のすぐ上、収まらないなら下端を範囲の下に合わせる
  expect(menu.top).toBeGreaterThanOrEqual(0);
  expect(menu.bottom).toBeLessThanOrEqual(420);
  expect(menu.left).toBeGreaterThanOrEqual(0);
  expect(menu.right).toBeLessThanOrEqual(390);
  if (menu.height < p.top - 8 - 12) {
    expect(menu.bottom, "収まるときは＋のすぐ上").toBeGreaterThan(p.top - 25);
    expect(menu.bottom).toBeLessThan(p.top + 25);
  } else {
    expect(Math.abs(menu.bottom - 416), "収まらないときは下端を見えている範囲の下に").toBeLessThanOrEqual(6);
  }
});
