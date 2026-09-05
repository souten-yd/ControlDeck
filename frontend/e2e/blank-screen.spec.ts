import { expect, test } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

test.beforeEach(async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials are required");
  await establishSession(page, context);
});

test("settings loads repeatedly without blanking", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => { if (message.type() === "error") errors.push(`console: ${message.text()}`); });

  for (let round = 0; round < 4; round += 1) {
    await page.goto("/settings", { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(1200);
    const length = await page.evaluate(() => document.getElementById("root")?.innerHTML.length ?? 0);
    expect(length, `round ${round} blanked; errors:\n${errors.join("\n")}`).toBeGreaterThan(500);
  }
  expect(errors, errors.join("\n---\n")).toEqual([]);
});

test("a throwing page shows a message instead of a blank screen", async ({ page }) => {
  // 配列を期待している所へ配列でないものを返し、描画中に確実に投げさせる
  await page.route("**/api/v1/auth/sessions", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: '{"oops":true}' }));

  await page.goto("/settings", { waitUntil: "domcontentloaded" });
  await expect(page.getByText("この画面を表示できませんでした")).toBeVisible({ timeout: 10_000 });
  // 画面全体は生きていて、他の画面へ移れること
  const length = await page.evaluate(() => document.getElementById("root")!.innerHTML.length);
  expect(length).toBeGreaterThan(1000);

  await page.goto("/", { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1200);
  await expect(page.getByText("この画面を表示できませんでした")).toHaveCount(0);
});

test("a feature turning off redirects instead of rebuilding the router", async ({ page }) => {
  await page.route("**/api/v1/meta", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    if (Array.isArray(body.enabled_features)) {
      body.enabled_features = body.enabled_features.filter((item: string) => item !== "opencode");
    }
    await route.fulfill({ response, body: JSON.stringify(body) });
  });

  await page.goto("/opencode", { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1500);
  expect(new URL(page.url()).pathname).toBe("/");
  const length = await page.evaluate(() => document.getElementById("root")!.innerHTML.length);
  expect(length).toBeGreaterThan(500);
});
