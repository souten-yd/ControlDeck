import { expect, test } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

const CSRF = { "X-Requested-With": "ControlDeck" };
const MARK = "COPY_ME_5823";

for (const width of [1280, 320]) {
  test.describe(`${width}px`, () => {
    test.use({ isMobile: width === 320, hasTouch: width === 320 });
    test(`terminal text can be selected and copied (${width}px)`, async ({ page, context }) => {
      test.skip(!hasSession(), "E2E credentials are required");
      test.setTimeout(60000);
      await context.grantPermissions(["clipboard-read", "clipboard-write"]);
      await page.setViewportSize({ width, height: 800 });
      await establishSession(page, context);
      // テスト用のフック（__controlDeckTerminalTest）を有効にする
      await page.addInitScript(() => localStorage.setItem("control-deck:terminal-geometry-debug", "1"));
      const response = await context.request.post("/api/v1/terminals", { headers: CSRF });
      expect(response.status()).toBe(201);
      const { id } = await response.json();
      try {
        await page.goto("/terminal");
        await page.locator("li").filter({ hasText: `#${id.slice(0, 6)}` }).getByRole("button", { name: "Connect", exact: true }).click();
        await expect.poll(() => page.evaluate(() => window.__controlDeckTerminalTest?.isReplaying())).toBe(false);
        await page.evaluate((mark) => window.__controlDeckTerminalTest!.sendInputForTest(`echo ${mark}\r`), MARK);
        await expect.poll(() => page.evaluate(() => window.__controlDeckTerminalTest!.captureRenderState().visibleBufferRows.join("\n")),
          { timeout: 15000 }).toContain(MARK);

        if (width >= 768) {
          // PC: 文字をダブルクリックで選び、Ctrl+C でコピー（選んでいるときは ^C を送らない）
          await page.evaluate(() => navigator.clipboard.writeText(""));
          const screen = page.locator(".xterm-screen").first();
          const box = (await screen.boundingBox())!;
          const rows = await page.evaluate(() => window.__controlDeckTerminalTest!.captureRenderState().visibleBufferRows);
          const row = rows.findIndex((line: string) => line.trim() === "COPY_ME_5823");
          expect(row).toBeGreaterThanOrEqual(0);
          const cellHeight = box.height / rows.length;
          await page.mouse.dblclick(box.x + 30, box.y + cellHeight * (row + 0.5));
          await page.keyboard.press("Control+c");
          await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe(MARK);
          await expect(page.getByText("コピーしました")).toBeVisible();
        }

        // 携帯・PC 共通: 「選択・コピー」で長押し（ドラッグ）で選べる全画面を開き、選んだ部分を写す
        await page.getByRole("button", { name: "文字を選んでコピー" }).click();
        const dialog = page.getByRole("dialog", { name: "文字を選んでコピー" });
        await expect(dialog).toBeVisible();
        const pre = dialog.locator("pre");
        await expect(pre).toContainText(MARK);
        const layout = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
        expect(layout).toBe(0);
        await pre.evaluate((node, mark) => {
          const text = node.firstChild as Text;
          const start = text.data.lastIndexOf(mark);
          const range = document.createRange();
          range.setStart(text, start);
          range.setEnd(text, start + mark.length);
          const selection = window.getSelection()!;
          selection.removeAllRanges();
          selection.addRange(range);
        }, MARK);
        await page.evaluate(() => navigator.clipboard.writeText(""));
        await dialog.getByRole("button", { name: "選択範囲をコピー" }).click();
        await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe(MARK);
        await expect(dialog).toBeHidden();
      } finally {
        await context.request.delete(`/api/v1/terminals/${id}`, { headers: CSRF });
      }
    });
  });
}
