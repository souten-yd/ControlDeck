import { expect, test } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

const CSRF = { "X-Requested-With": "ControlDeck" };

for (const width of [1280, 320]) {
  test.describe(`${width}px input device`, () => {
    test.use({ isMobile: width === 320, hasTouch: width === 320 });
    test(`normal terminal retains long scrollback after reload (${width}px)`, async ({ page, context }) => {
      test.skip(!hasSession(), "E2E credentials are required");
      test.setTimeout(60000);
      await page.setViewportSize({ width, height: 800 });
      await establishSession(page, context);
      await page.addInitScript(() => localStorage.setItem("control-deck:terminal-geometry-debug", "1"));
      const response = await context.request.post("/api/v1/terminals", { headers: CSRF });
      expect(response.status()).toBe(201);
      const { id } = await response.json();
      const connect = async () => {
        await page.goto("/terminal");
        await page.locator("li").filter({ hasText: `#${id.slice(0, 6)}` }).getByRole("button", { name: "Connect", exact: true }).click();
        await expect.poll(() => page.evaluate(() => window.__controlDeckTerminalTest?.isReplaying())).toBe(false);
      };
      try {
        await connect();
        await page.evaluate(() => window.__controlDeckTerminalTest!.sendInputForTest(
          "python3 -c 'for i in range(12000): print(\"SCROLL_ROW_%04d\" % i)'\r",
        ));
        await expect.poll(() => page.evaluate(() => window.__controlDeckTerminalTest!.captureRenderState().visibleBufferRows.join("\n")), { timeout: 15000 }).toContain("SCROLL_ROW_11999");
        const scrollToStart = async () => {
          const screen = page.locator(".xterm-screen").first();
          if (width === 320) {
            await screen.evaluate((target) => {
              const box = target.getBoundingClientRect();
              const x = box.x + box.width / 2;
              for (const [type, ratio] of [["touchstart", 0.2], ["touchmove", 0.8], ["touchend", 0.8]] as const) {
                const touch = new Touch({ identifier: 1, target, clientX: x, clientY: box.y + box.height * ratio });
                target.dispatchEvent(new TouchEvent(type, { touches: type === "touchend" ? [] : [touch], changedTouches: [touch], bubbles: true, cancelable: true }));
              }
            });
            const track = page.locator("[data-terminal-history-track]");
            await expect.poll(() => track.getAttribute("aria-valuemax")).not.toBe("0");
            await track.evaluate((target) => {
              const box = target.getBoundingClientRect();
              const touch = new Touch({ identifier: 2, target, clientX: box.x + 2, clientY: box.y });
              for (const type of ["touchstart", "touchend"]) target.dispatchEvent(new TouchEvent(type, { touches: type === "touchend" ? [] : [touch], changedTouches: [touch], bubbles: true, cancelable: true }));
            });
          } else {
            await screen.hover();
            await page.mouse.wheel(0, -1000000);
            const track = page.locator("[data-terminal-history-track]");
            await expect.poll(() => track.getAttribute("aria-valuemax")).not.toBe("0");
            // Browsers clamp a single wheel event's delta. Drag the authoritative
            // scrollbar to reach the oldest retained line in one operation.
            const box = (await track.boundingBox())!;
            await page.mouse.move(box.x + 2, box.y + 2);
            await page.mouse.down();
            await page.mouse.up();
          }
          await expect.poll(() => page.evaluate(() => window.__controlDeckTerminalTest!.captureRenderState().visibleBufferRows.join("\n")), { timeout: 15000 }).toContain("SCROLL_ROW_0000");
        };
        await scrollToStart();
        await page.screenshot({ path: test.info().outputPath(`scroll-${width}-before-reload.png`) });
        await page.locator(".xterm-screen").first().click();
        await page.keyboard.type("echo SCROLL_INPUT_OK");
        await page.keyboard.press("Enter");
        await expect.poll(() => page.evaluate(() => window.__controlDeckTerminalTest!.captureRenderState().visibleBufferRows.join("\n"))).toContain("SCROLL_INPUT_OK");
        // A fresh page uses tmux capture-pane rather than the in-memory xterm buffer.
        await connect();
        await scrollToStart();
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await page.screenshot({ path: test.info().outputPath(`scroll-${width}-after-reload.png`) });
      } finally {
        test.setTimeout(test.info().timeout + 10000);
        await context.request.delete(`/api/v1/terminals/${id}`, { headers: CSRF });
      }
    });
  });
}
