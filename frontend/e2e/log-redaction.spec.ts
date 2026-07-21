import { expect, test, type Page } from "@playwright/test";
import { mkdir, unlink, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;
const dataDirectory = process.env.CONTROL_DECK_E2E_DATA_DIR ?? join(homedir(), ".local", "share", "control-deck");
const headers = { "X-Requested-With": "ControlDeck" };

async function login(page: Page) {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  await page.goto("/login");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page).not.toHaveURL(/\/login/);
}

test("never renders application log secrets at mobile or desktop widths", async ({ page }) => {
  const secret = "E2E-known-environment-secret";
  const browserErrors: string[] = [];
  page.on("pageerror", (error) => browserErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  await login(page);
  const customLog = join(dataDirectory, `e2e-custom-${Date.now()}.log`);
  await writeFile(customLog, "safe custom source\nAPI_KEY=E2E-custom-secret\n", "utf8");
  const response = await page.request.post("/api/v1/apps", {
    headers,
    data: {
      name: "E2E redacted logs",
      application_type: "url_shortcut",
      url: "https://example.test",
      environment: { API_TOKEN: secret },
      log_files: [customLog],
    },
  });
  expect(response.status()).toBe(201);
  const app = await response.json() as { id: number };
  const journalResponse = await page.request.post("/api/v1/apps", {
    headers,
    data: {
      name: "E2E journal source",
      application_type: "systemd_service",
      systemd_unit_name: "control-deck-web.service",
      systemd_scope: "user",
    },
  });
  expect(journalResponse.status()).toBe(201);
  const journalApp = await journalResponse.json() as { id: number };
  const logDirectory = join(dataDirectory, "logs", String(app.id));
  await mkdir(logDirectory, { recursive: true });
  await writeFile(
    join(logDirectory, "stdout.log"),
    `safe visible line\npassword=E2E-literal-password\nAuthorization: Bearer E2E-auth-token\nopaque ${secret}\n`,
    "utf8",
  );

  try {
    for (const viewport of [{ width: 320, height: 700 }, { width: 1280, height: 800 }]) {
      await page.setViewportSize(viewport);
      await page.goto(`/logs?app=${app.id}&stream=stdout`);
      await expect(page.getByText("safe visible line")).toBeVisible();
      const text = await page.locator("body").innerText();
      expect(text).toContain("***");
      expect(text).not.toContain(secret);
      expect(text).not.toContain("E2E-literal-password");
      expect(text).not.toContain("E2E-auth-token");
      await page.getByLabel("ログソース").selectOption("file:0");
      await expect(page.getByText("safe custom source")).toBeVisible();
      expect(await page.locator("body").innerText()).not.toContain("E2E-custom-secret");
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
      expect(overflow).toBeLessThanOrEqual(1);
    }
    await page.setViewportSize({ width: 320, height: 700 });
    await page.goto(`/logs?app=${journalApp.id}&source=journal`);
    await expect(page.getByLabel("ログソース")).toHaveValue("journal");
    await expect.poll(async () => (await page.locator("body").innerText()).includes("control-deck-web") || (await page.locator("body").innerText()).includes("Uvicorn"), { timeout: 10_000 }).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
    const download = await page.request.get(`/api/v1/apps/${app.id}/logs/download?stream=stdout`);
    const downloaded = await download.text();
    expect(downloaded).toContain("safe visible line");
    expect(downloaded).not.toContain(secret);
    expect(downloaded).not.toContain("E2E-literal-password");
    expect(browserErrors).toEqual([]);
  } finally {
    await page.request.delete(`/api/v1/apps/${app.id}/logs?stream=all`, { headers });
    await page.request.delete(`/api/v1/apps/${app.id}`, { headers });
    await page.request.delete(`/api/v1/apps/${journalApp.id}`, { headers });
    await unlink(customLog).catch(() => undefined);
  }
});
