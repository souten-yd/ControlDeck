import { expect, test, type Page } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

async function login(page: Page) {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  await page.goto("/login");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page).not.toHaveURL(/\/login/);
}

test("normal API and metrics WebSocket remain available at mobile and desktop widths", async ({ page }) => {
  const browserErrors: string[] = [];
  const limitedResponses: string[] = [];
  const sockets: string[] = [];
  page.on("pageerror", (error) => browserErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  page.on("response", (response) => {
    if (response.status() === 429) limitedResponses.push(response.url());
  });
  page.on("websocket", (socket) => sockets.push(socket.url()));

  await login(page);
  for (const viewport of [{ width: 320, height: 700 }, { width: 1280, height: 800 }]) {
    await page.setViewportSize(viewport);
    await page.goto("/");
    await expect(page.locator("main")).toBeVisible();
    await expect.poll(() => sockets.some((url) => url.includes("/api/v1/system/metrics/stream"))).toBe(true);
    const layout = await page.evaluate(() => ({
      viewport: innerWidth,
      document: document.documentElement.scrollWidth,
      body: document.body.scrollWidth,
    }));
    expect(layout.document).toBeLessThanOrEqual(layout.viewport);
    expect(layout.body).toBeLessThanOrEqual(layout.viewport);
  }

  expect(limitedResponses).toEqual([]);
  expect(browserErrors).toEqual([]);
});
