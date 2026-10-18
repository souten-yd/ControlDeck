import { test, expect } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

for (const width of [1280, 320]) {
  test(`persistent login survives restored browser context (${width}px)`, async ({ page, context, browser }) => {
    test.skip(!hasSession(), "requires a test account or issued session");
    await page.setViewportSize({ width, height: 800 });
    await establishSession(page, context);
    await page.goto("/");
    await expect(page).not.toHaveURL(/\/login/);
    expect((await context.request.get("/api/v1/auth/me")).status()).toBe(200);
    const cookie = (await context.cookies()).find((item) => item.name === "cd_session")!;
    expect(cookie.httpOnly).toBe(true);
    expect(cookie.sameSite).toBe("Lax");
    expect(cookie.expires - Date.now() / 1000).toBeGreaterThan(364 * 86400);
    expect(cookie.expires - Date.now() / 1000).toBeLessThanOrEqual(366 * 86400);

    // Restore only persistent browser state, without logging in again.
    const restored = await browser.newContext({
      storageState: await context.storageState(),
      viewport: { width, height: 800 },
    });
    try {
      const reopened = await restored.newPage();
      await reopened.goto(new URL("/", page.url()).href);
      await expect(reopened).not.toHaveURL(/\/login/);
      await reopened.reload();
      await expect(reopened).not.toHaveURL(/\/login/);
      const status = await reopened.evaluate(async () => (await fetch("/api/v1/auth/me")).status);
      expect(status).toBe(200);
      const overflow = await reopened.evaluate(() => document.documentElement.scrollWidth > innerWidth);
      expect(overflow).toBe(false);
    } finally {
      await restored.close();
    }
  });
}
