import { expect, test } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

/** Patent（特許調査・検討）画面。案件の作成 → 概要 → 期限 → 請求項 → AI 支援を、PC 幅と 320px で通す。
 *  根拠: 2026-11-21 利用者「PatentBuddy のアドオンは不要。Patent 機能をつけて」。
 *  検証用インスタンスにはローカルのモデルが無いので、AI 支援は「外部へ送らない」案内が出ることを確かめる。 */
for (const width of [1280, 320]) {
  test(`Patent: 案件を作って期限と請求項を扱う（${width}px）`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height: width === 320 ? 640 : 900 });
    await establishSession(page, context);
    await page.goto("/patents");
    await expect(page.getByRole("heading", { name: "Patent" })).toBeVisible();

    const title = `E2E インバータ ${width}`;
    await page.getByRole("button", { name: "案件を作成" }).first().click();
    await page.getByPlaceholder("発明の名称").fill(title);
    await page.getByRole("button", { name: "作成", exact: true }).click();
    const sheet = page.getByRole("dialog");
    await expect(sheet.getByText(title).first()).toBeVisible();

    await sheet.getByLabel("課題", { exact: true }).fill("スイッチング損失が大きい");
    await sheet.getByRole("button", { name: "保存", exact: true }).click();
    await expect(page.getByText("保存しました")).toBeVisible();

    await sheet.getByRole("button", { name: "ヒアリング" }).click();
    await expect(sheet.getByRole("button", { name: "話して記録する" })).toBeVisible();
    await sheet.getByPlaceholder("話した内容がここに入ります。手で書き足したり直したりもできます。").fill("インバータの損失を減らしたい。ゲートの駆動を工夫した。");
    await sheet.getByRole("button", { name: "直した内容を保存" }).click();
    await expect(sheet.getByText("文字", { exact: false }).first()).toBeVisible();

    await sheet.getByRole("button", { name: "期限" }).click();
    await sheet.locator('input[type="date"]').fill("2026-01-31");
    await sheet.getByRole("button", { name: "出来事を記録" }).click();
    await expect(sheet.getByText("審査請求期限（出願日から3年）")).toBeVisible();

    await sheet.getByRole("button", { name: "請求項" }).click();
    await sheet.locator("textarea").first().fill("【請求項１】\nスイッチング素子と、制御部と、を備えるインバータ。");
    await sheet.getByRole("button", { name: "新しい版で保存" }).click();
    await expect(sheet.getByText("構成要件（最新版）")).toBeVisible();
    await expect(sheet.getByText("第 1 版（全 1 版）")).toBeVisible();

    await sheet.getByRole("button", { name: /^AI/ }).click();
    await expect(sheet.getByText("ローカル専用のモデルが使えないため実行できません。案件の内容はクラウドへ送りません。")).toBeVisible();
    await expect(sheet.getByRole("button", { name: /^AI/ })).toHaveAttribute("aria-pressed", "true");
    await page.waitForTimeout(400);   // タブの色の切り替え（transition）が済んでから撮る
    await page.screenshot({ path: `test-results/patents-${width}.png`, fullPage: true });

    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
  });
}
