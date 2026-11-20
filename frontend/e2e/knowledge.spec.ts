import { expect, test } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

/** Knowledge: 探す・資料・規格のタブ。規格は入手経路から保存・AI 利用の判定を見せ、購入品は番号と題名だけで登録する。
 *  根拠: 2026-11-21 利用者「規格検索の RAG」「利用者が知らなくていい情報は隠し、必要なら開けるように」。 */
for (const width of [1280, 320]) {
  test(`Knowledge: 規格を登録して番号で探す（${width}px）`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height: width === 320 ? 640 : 900 });
    await establishSession(page, context);
    await page.goto("/knowledge");
    await expect(page.getByRole("button", { name: "探す", exact: true }).first()).toBeVisible();

    await page.getByRole("button", { name: "規格の管理" }).click();
    await page.getByRole("button", { name: "規格を登録" }).first().click();
    const sheet = page.getByRole("dialog");
    const number = `IEC 61000-4-${width === 320 ? 3 : 2}:2025`;
    await sheet.getByPlaceholder("規格番号（例: JIS C 61000-4-2:2012）").fill(number);
    await sheet.getByPlaceholder("題名").fill("イミュニティ試験");
    await expect(sheet.getByText("AI には使わない")).toBeVisible();
    await expect(sheet.getByText("IEC Webstore")).toHaveCount(0);           // 理由は既定で隠す
    await sheet.getByRole("button", { name: "理由" }).click();
    await expect(sheet.getByText(/IEC Webstore/)).toBeVisible();
    await sheet.getByRole("button", { name: "登録", exact: true }).click();

    await expect(page.getByText(number.split(":")[0]).first()).toBeVisible({ timeout: 15_000 });
    await page.getByPlaceholder("規格番号・条項の言葉で探す").fill(number.split(":")[0]);
    await page.getByRole("button", { name: "探す", exact: true }).last().click();
    await expect(page.getByText(/イミュニティ試験/).first()).toBeVisible();
    await page.waitForTimeout(400);
    await page.screenshot({ path: `test-results/knowledge-standards-${width}.png`, fullPage: true });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
  });
}
