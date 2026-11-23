import { expect, test, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

/** 設定 → 表示の「メニューの表示」: メニュー（PC のサイドバー・携帯のドロワー）の項目を 1 つずつ隠せる。
 *  隠した項目は「よく使う」からも消える。Settings は隠せない（隠すと戻れない）。選択は端末に残る。
 *  Add-on の項目も同じ一覧から選べる（effective addons を差し替えて確かめる）。
 *  根拠: 2026-11-23 利用者「設定で表示っていう項目があれば、そこの中にメインメニューの項目を任意に
 *  表示、非表示の選択ができるようにしてほしい。新しく導入されたプラグインや今後導入される機能、今開発中の
 *  パテントなども含めて…汎用的に開発すること」。 */
const addonFixture = {
  revision: 1, etag: "nav-visibility", addons: [],
  contributions: { navigation: [{ addon_id: "mock-tool", id: "main", label: "Mock Tool", permission: "", availability: "available", route: "/x/mock-tool/main" }] },
};

/** メニューの中身。PC はサイドバー、携帯は ☰ で開くドロワー。 */
async function menu(page: Page, width: number) {
  if (width >= 768) return page.locator("[data-shell-sidebar]");
  await page.getByRole("button", { name: "メニューを開く" }).click();
  return page.getByRole("dialog", { name: "メニュー" });
}
async function expectLinks(page: Page, width: number, expected: Record<string, number>) {
  const scope = await menu(page, width);
  for (const [name, count] of Object.entries(expected)) {
    await expect(scope.getByRole("link", { name, exact: true })).toHaveCount(count);
  }
  if (width < 768) await page.keyboard.press("Escape");
}

for (const width of [1280, 320]) {
  test(`メニューの項目を隠せる（${width}px）`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height: width === 320 ? 700 : 900 });
    await page.route(/\/api\/v1\/addons\/effective$/, (route) => route.fulfill({ json: addonFixture }));
    // Files を「よく使う」へ固定した状態から始める（保存済みなら触らない）
    await page.addInitScript(() => {
      try {
        if (localStorage.getItem("cd-mobile-navigation-v1") === null) {
          localStorage.setItem("cd-mobile-navigation-v1", JSON.stringify(["/files", "/apps"]));
        }
      } catch { /* 無くても確かめは続く */ }
    });
    await establishSession(page, context);
    await page.goto("/settings/appearance");
    await expect(page.getByRole("heading", { name: "メニューの表示" })).toBeVisible();

    await expectLinks(page, width, { Patent: 1, Files: 1, "Mock Tool": 1, Settings: 1 });

    for (const name of ["Patent", "Files", "Mock Tool"]) {
      await page.getByRole("checkbox", { name }).uncheck();
    }
    await expectLinks(page, width, { Patent: 0, Files: 0, "Mock Tool": 0, Settings: 1 });

    // Settings は隠せない
    await expect(page.getByRole("checkbox", { name: /^Settings/ })).toBeDisabled();
    // 「よく使う」の候補にも非表示と出る
    await expect(page.getByRole("button", { name: /Files（非表示）/ })).toBeVisible();

    // 再読み込みしても選択が残る
    await page.reload();
    await expect(page.getByRole("checkbox", { name: "Patent" })).not.toBeChecked();
    await expectLinks(page, width, { Patent: 0, Files: 0, "Mock Tool": 0, Settings: 1 });
    await page.getByRole("checkbox", { name: "Patent" }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: `test-results/navigation-visibility-${width}.png` });

    // 検索（Ctrl K）からは隠した項目も開ける
    await page.keyboard.press("Control+k");
    await page.getByLabel("コマンド検索").fill("Patent");
    await expect(page.getByText("Open Patent")).toBeVisible();
    await page.keyboard.press("Escape");

    await page.getByRole("button", { name: "すべて表示に戻す" }).click();
    await expect(page.getByRole("checkbox", { name: "Patent" })).toBeChecked();
    await expectLinks(page, width, { Patent: 1, Files: 1, "Mock Tool": 1, Settings: 1 });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
  });
}
