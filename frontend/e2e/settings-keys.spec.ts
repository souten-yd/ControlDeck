import { expect, test } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

/** Settings →「API キー」: 外部のサービスのキーを 1 か所で。値は出さず、設定済みか・末尾の数文字だけ。消すのは確認つき。
 *  根拠: 2026-11-21 利用者「EPO や特許庁の API のキーはどこで設定できる？設定の中にキーをまとめて。OpenRouter のキーも。消さないように」。 */
for (const width of [1280, 320]) {
  test(`Settings: API キー（${width}px）`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height: width === 320 ? 640 : 900 });
    await establishSession(page, context);
    await page.goto("/settings/keys");
    for (const name of ["クラウドの AI", "特許・論文の公開データ", "モデルのダウンロード", "Workflow のシークレット"]) {
      await expect(page.getByRole("heading", { name, exact: true })).toBeVisible();
    }
    await expect(page.locator("li", { hasText: "OpenRouter" }).first()).toBeVisible();
    await expect(page.getByText("特許庁 特許情報取得 API", { exact: false })).toBeVisible();

    const card = page.locator("li", { hasText: "USPTO ODP（米国の出願）" });
    await card.getByRole("button", { name: "設定" }).click();
    const secret = `uspto-e2e-${width}-abcd`;
    await card.getByLabel("API キー").fill(secret);
    await card.getByRole("button", { name: "保存", exact: true }).click();
    await expect(page.getByText("USPTO ODP（米国の出願） のキーを保存しました")).toBeVisible();
    await expect(card.getByText("設定済み")).toBeVisible();
    await expect(page.getByText(secret)).toHaveCount(0);            // 値は画面に出ない
    await page.screenshot({ path: `test-results/settings-keys-${width}.png`, fullPage: true });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);

    await card.getByRole("button", { name: "変更" }).click();
    await card.getByRole("button", { name: "削除" }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "削除する" }).click();
    await expect(card.getByText("未設定")).toBeVisible();
  });
}
