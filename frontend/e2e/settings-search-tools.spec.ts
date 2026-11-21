import { expect, test } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

/** 機能と拡張の「検索と収集」: Web 本文抽出（Trafilatura）の導入ボタン、取得先の設定（特許庁 API を含む）への入口、
 *  分類ごとの「まとめて導入」。導入・まとめて導入は PyPI / npm から取得するので、ここでは押さない（見える・押せることまで）。
 *  根拠: 2026-11-21 利用者「必要なコードやプラグインは、機能と拡張に導入ボタンと更新ボタンを付けて、個別管理・一括導入・更新」
 *  「特許庁などからデータをダウンロードする API の設定はどこ？見当たらない」。 */
for (const width of [1280, 320]) {
  test(`機能と拡張: 検索と収集（${width}px）`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height: width === 320 ? 640 : 900 });
    await establishSession(page, context);
    await page.goto("/settings/features");
    await expect(page.getByRole("heading", { name: /検索と収集/ })).toBeVisible();
    const card = page.locator("#feature-web-extract");
    await expect(card.getByText("Web 本文抽出（Trafilatura）")).toBeVisible();
    await expect(card.getByRole("button", { name: "導入", exact: true })).toBeEnabled();
    // 検索の部品にチェックを付けて、選んだものだけまとめて導入できる（押すと PyPI から取るので押さない）
    for (const name of ["robots.txt 解析（Protego）", "Web 収集用ブラウザ（Chromium）", "Web 本文抽出（Trafilatura）"]) {
      await expect(page.getByLabel(`${name} を選ぶ`)).toBeVisible();
    }
    await expect(page.locator("#feature-searxng").getByText("SearXNG（Web 検索エンジン）")).toBeVisible();
    const installPicked = page.getByRole("button", { name: /選んだものを導入（\d+）/ });
    const searchInstall = page.getByRole("button", { name: "選んだものを導入（0）" });
    await expect(searchInstall.first()).toBeDisabled();
    await page.getByLabel("Web 本文抽出（Trafilatura） を選ぶ").check();
    await page.getByLabel("robots.txt 解析（Protego） を選ぶ").check();
    await expect(page.getByRole("button", { name: "選んだものを導入（2）" })).toBeEnabled();
    await expect(installPicked.first()).toBeVisible();

    await page.getByRole("button", { name: "取得先の設定" }).click();
    const sheet = page.getByRole("dialog");
    await expect(sheet.getByText("特許庁 特許情報取得 API", { exact: false })).toBeVisible();
    await expect(sheet.getByText("トークン取得の URL（利用登録の後に特許庁から通知）")).toBeVisible();
    await expect(sheet.getByRole("link", { name: "利用登録の案内（特許庁）" })).toBeVisible();
    await expect(sheet.getByRole("button", { name: "保存", exact: true })).toBeDisabled();
    await card.scrollIntoViewIfNeeded().catch(() => {});
    await page.screenshot({ path: `test-results/settings-search-keys-${width}.png` });
    await page.keyboard.press("Escape");
    await card.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `test-results/settings-search-tools-${width}.png` });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
  });
}
