import { expect, test } from "@playwright/test";

for (const viewport of [
  { width: 320, height: 700 },
  { width: 390, height: 844 },
]) {
  test(`reload and text focus keep the ${viewport.width}px viewport contained`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.goto("/login");
    await page.reload();

    const username = page.getByLabel("ユーザー名");
    await expect(username).toBeVisible();
    await username.focus();
    await username.fill("mobile-viewport-check");

    const layout = await page.evaluate(() => {
      const root = document.querySelector<HTMLElement>("#root")!;
      const input = document.activeElement as HTMLInputElement;
      return {
        documentClientWidth: document.documentElement.clientWidth,
        documentScrollWidth: document.documentElement.scrollWidth,
        bodyScrollWidth: document.body.scrollWidth,
        rootRight: root.getBoundingClientRect().right,
        viewportWidth: window.innerWidth,
        inputFontSize: Number.parseFloat(getComputedStyle(input).fontSize),
      };
    });

    expect(layout.inputFontSize).toBeGreaterThanOrEqual(16);
    expect(layout.documentScrollWidth).toBeLessThanOrEqual(layout.documentClientWidth);
    expect(layout.bodyScrollWidth).toBeLessThanOrEqual(layout.documentClientWidth);
    expect(layout.rootRight).toBeLessThanOrEqual(layout.viewportWidth + 0.5);
  });
}
