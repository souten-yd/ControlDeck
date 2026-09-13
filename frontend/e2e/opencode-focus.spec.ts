import { expect, test } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

/** 画面を開いただけで software keyboard が上がらないこと。
 *
 * 以前は入力欄に autoFocus が付いていて、OpenCode を開くだけで keyboard が
 * せり上がり、プロジェクト一覧も開始ボタンも隠れていた。v1/v2 は同じ
 * コンポーネントなので両方の route で縛る。 */
const active = (page: import("@playwright/test").Page) => page.evaluate(() => {
  const el = document.activeElement as HTMLInputElement | null;
  return `${el?.tagName ?? "(none)"}${el?.placeholder ? ` [${el.placeholder.slice(0, 16)}]` : ""}`;
});

for (const path of ["/opencode", "/opencode-v2"]) {
  test(`${path}: 開いただけでは入力欄へフォーカスが入らず、タップで入る`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials are required");
    await page.setViewportSize({ width: 320, height: 680 });
    await establishSession(page, context);
    await page.goto(path, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(2500);
    const onOpen = await active(page);
    console.log(`${path} 開いた直後:`, onOpen);
    expect(onOpen).not.toContain("INPUT");

    await page.getByPlaceholder("プロジェクト名").click();
    await page.waitForTimeout(300);
    const onTap = await active(page);
    console.log(`${path} タップ後   :`, onTap);
    expect(onTap).toContain("INPUT");
  });
}
