import { expect, test } from "@playwright/test";
import { establishSession } from "./support/session";

for (const width of [320, 1280]) {
  test(`common VRAM margin persists at ${width}px`, async ({ page, context }) => {
    await page.setViewportSize({ width, height: 900 });
    await establishSession(page, context);
    const errors: string[] = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto("/models");
    await page.getByRole("button", { name: "LLM 共通設定", exact: true }).click();
    const sheet = page.getByRole("dialog", { name: "LLM 共通設定", exact: true });
    const margin = sheet.getByLabel("VRAM マージン（GiB）", { exact: true });
    await expect(margin).toBeVisible();
    await margin.fill("3.25");
    await expect(sheet.getByText("RAM の余裕とは別です。", { exact: false })).toBeVisible();
    await margin.scrollIntoViewIfNeeded();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `/tmp/vram-margin-${width}.png` });
    const saved = page.waitForResponse(response => response.url().endsWith("/models/runtime-policy") && response.request().method() === "PUT");
    await sheet.getByRole("button", { name: "保存", exact: true }).click();
    const response = await saved;
    expect(response.ok()).toBe(true);
    expect((await response.json()).policy.vram_margin_gib).toBe(3.25);
    await page.reload();
    await page.getByRole("button", { name: "LLM 共通設定", exact: true }).click();
    await expect(page.getByLabel("VRAM マージン（GiB）", { exact: true })).toHaveValue("3.25");
    expect(errors).toEqual([]);
  });
}
