import { expect, test } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

/** iPhone: キーボードが出ても、触った入力欄が見える範囲に出る（AI アシスタント以外の画面でも）。
 *  根拠: 2026-11-21 利用者「設定の API キーの入力欄をタップするとキーボードが上がって入力欄が消え、貼り付けられない」。
 *  実機（iOS 18.7、設定の画面）: innerHeight 932 のまま、visual viewport だけ 517 に縮み、殻は 932 のままだった。
 *  殻を見える範囲に合わせる useKeyboardViewportFit を AI アシスタントだけが呼んでいたため。
 *  Chromium はキーボードで visual viewport だけを縮められないので、visualViewport を偽物にして iOS と同じ値を作る。 */
test("iPhone: キーボードで visual viewport が縮むと、設定の入力欄が見える範囲に出る", async ({ page, context }) => {
  test.skip(!hasSession(), "ログイン情報がありません");
  await page.addInitScript(() => {
    const target = new EventTarget();
    const state = { height: 0, offsetTop: 0 };
    // Object.assign は getter を値として写すので defineProperties で付ける
    const fake = Object.defineProperties(target, {
      height: { get: () => state.height || window.innerHeight },
      width: { get: () => window.innerWidth },
      offsetTop: { get: () => state.offsetTop },
      offsetLeft: { value: 0 }, pageTop: { value: 0 }, pageLeft: { value: 0 }, scale: { value: 1 },
    });
    Object.defineProperty(window, "visualViewport", { configurable: true, get: () => fake });
    (window as unknown as { __keyboard: (h: number) => void }).__keyboard = (h: number) => {
      state.height = h;
      target.dispatchEvent(new Event("resize"));
    };
  });
  await page.setViewportSize({ width: 430, height: 932 });
  await establishSession(page, context);
  await page.goto("/settings/keys");
  // いちばん下の組（開発）の欄を開く
  const card = page.locator("li", { hasText: "OpenCode Zen（無料のモデル）" });
  await card.scrollIntoViewIfNeeded();
  await card.getByRole("button", { name: "設定" }).click();
  const field = card.getByLabel("API キー");
  await expect(field).toBeVisible();
  // 欄を画面の下の方（キーボードが出ると隠れる位置）に置いて触る
  await field.evaluate((el) => el.scrollIntoView({ block: "end" }));
  expect(await field.evaluate((el) => el.getBoundingClientRect().bottom)).toBeGreaterThan(517);
  await field.evaluate((el) => (el as HTMLInputElement).focus({ preventScroll: true }));
  // キーボード: innerHeight は 932 のまま、見える高さだけ 517（実機の値）
  await page.evaluate(() => (window as unknown as { __keyboard: (h: number) => void }).__keyboard(517));
  await expect.poll(() => page.evaluate(() => document.documentElement.classList.contains("cd-keyboard-fit"))).toBe(true);
  expect(await page.evaluate(() => getComputedStyle(document.getElementById("root")!).height)).toBe("517px");
  await expect.poll(() => field.evaluate((el) => {
    const r = el.getBoundingClientRect();
    return r.top >= 0 && r.bottom <= 517;
  })).toBe(true);
  await expect(field).toBeFocused();
  await field.fill("zen-e2e-paste");                                   // 貼り付け（入力）できる
  await expect(field).toHaveValue("zen-e2e-paste");
  // キーボードを閉じたら殻は元へ戻る
  await page.evaluate(() => (window as unknown as { __keyboard: (h: number) => void }).__keyboard(932));
  await expect.poll(() => page.evaluate(() => document.documentElement.classList.contains("cd-keyboard-fit"))).toBe(false);
});
