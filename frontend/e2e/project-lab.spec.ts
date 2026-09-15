import { expect, test, type Page } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page).not.toHaveURL(/\/login/);
}

/** ページ自体はスクロールさせない設計なので、縦横ともはみ出していないことを確認する。 */
async function expectNoPageScroll(page: Page) {
  const layout = await page.evaluate(() => ({
    width: innerWidth,
    height: innerHeight,
    scrollWidth: document.documentElement.scrollWidth,
    scrollHeight: document.documentElement.scrollHeight,
  }));
  expect(layout.scrollWidth).toBeLessThanOrEqual(layout.width);
  expect(layout.scrollHeight).toBeLessThanOrEqual(layout.height + 1);
}

test("previews artifacts and runs a source file without page scrolling", async ({ page }) => {
  test.skip(!username || !password, "E2E credentials are required");
  const browserErrors: string[] = [];
  page.on("pageerror", (error) => browserErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  await login(page);

  for (const viewport of [{ width: 320, height: 700 }, { width: 1280, height: 800 }]) {
    await page.setViewportSize(viewport);
    await page.goto("/project-lab");
    await expect(page.getByRole("button", { name: "その他の操作" })).toBeVisible();
    await expectNoPageScroll(page);
  }

  await page.setViewportSize({ width: 320, height: 700 });
  await page.goto("/project-lab");

  // HTML成果物はscriptが動いた状態で表示される（sandbox iframe内のDOMを確認する）。
  const frame = page.frameLocator("iframe[title$='preview']");
  await expect(frame.locator("body")).not.toBeEmpty();

  // ファイルシートは検索・絞り込みができ、選択でプレビューが切り替わる。
  await page.getByRole("button", { name: /プレビュー|\./ }).first().click();
  const sheet = page.getByRole("dialog", { name: "ファイル" });
  await expect(sheet).toBeVisible();
  // 形式での絞り込みは、この企画に実際にある拡張子だけを並べる。固定の一覧だと
  // 一つも無い形式が選べてしまい、選んだ先が必ず空になる。
  const kinds = sheet.getByLabel("ファイル形式で絞り込み");
  if (await kinds.count()) {
    const options = await kinds.locator("option").allInnerTexts();
    expect(options[0]).toBe("すべての形式");
    expect(options.length).toBeGreaterThan(1);
    // 並びは多い順。同数なら名前順で、開くたびに変わらない。
    const counts = options.slice(1).map((text) => Number(text.replace(/^.*（(\d+)）$/u, "$1")));
    expect(counts).toEqual([...counts].sort((left, right) => right - left));
    const picked = options[1].replace(/（\d+）$/u, "");
    await kinds.selectOption(picked);
    // 選んだ形式だけが残る。
    const rows = sheet.getByRole("button").filter({ hasText: /·/ });
    for (const text of await rows.allInnerTexts()) {
      expect(text.toUpperCase()).toContain(`.${picked}`);
    }
    await kinds.selectOption("all");
  }
  await sheet.getByRole("button", { name: "コード" }).click();
  const runnable = sheet.getByRole("button").filter({ hasText: /\.(py|js|mjs)$/ }).first();
  if (await runnable.count()) {
    await runnable.click();
    await page.getByRole("button", { name: "実行", exact: true }).click();
    const runSheet = page.getByRole("dialog", { name: "実行" });
    await expect(runSheet).toBeVisible();
    await expect(runSheet.getByText(/SUCCEEDED|RUNNING|QUEUED/)).toBeVisible({ timeout: 60_000 });
  } else {
    await page.getByRole("button", { name: "閉じる" }).click();
  }
  expect(browserErrors).toEqual([]);
});
