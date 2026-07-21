import { expect, test, type Page } from "@playwright/test";
import { appendFile, mkdir } from "node:fs/promises";
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

test("creates a durable application log ERROR alert at mobile and desktop widths", async ({ page }) => {
  test.setTimeout(70_000);
  const browserErrors: string[] = [];
  page.on("pageerror", (error) => browserErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  await login(page);

  const appResponse = await page.request.post("/api/v1/apps", {
    headers,
    data: {
      name: "E2E log alert target",
      application_type: "url_shortcut",
      url: "https://example.test",
    },
  });
  expect(appResponse.status()).toBe(201);
  const app = await appResponse.json() as { id: number };
  let ruleId: number | null = null;

  try {
    for (const [index, viewport] of [{ width: 320, height: 700 }, { width: 1280, height: 800 }].entries()) {
      await page.setViewportSize(viewport);
      await page.goto("/settings");
      const section = page.locator("section").filter({ has: page.getByRole("heading", { name: "アラートルール" }) });
      if (index === 0) {
        await section.getByRole("button", { name: "追加" }).click();
      } else {
        await section.locator("li").filter({ hasText: "E2E new log ERROR" }).getByRole("button", { name: "編集" }).click();
      }
      const dialog = page.getByRole("dialog", { name: index === 0 ? "アラートルールを追加" : "ルールを編集" });
      if (index === 0) {
        await dialog.getByLabel("ルール名").fill("E2E new log ERROR");
        await dialog.getByLabel("監視条件").selectOption("app_log_error");
        await dialog.getByLabel("対象アプリ").selectOption(String(app.id));
      }
      await expect(dialog.getByLabel("監視条件")).toHaveValue("app_log_error");
      await expect(dialog.getByLabel("対象アプリ")).toHaveValue(String(app.id));
      await expect(dialog.getByLabel("しきい値")).toHaveCount(0);
      await expect(dialog.getByLabel("継続時間")).toHaveCount(0);
      expect(await dialog.evaluate((element) => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1);
      if (index === 0) {
        await dialog.getByRole("button", { name: "保存" }).click();
        await expect(dialog).toHaveCount(0);
        const rules = await (await page.request.get("/api/v1/alert-rules")).json() as Array<{ id: number; name: string }>;
        ruleId = rules.find((rule) => rule.name === "E2E new log ERROR")?.id ?? null;
        expect(ruleId).not.toBeNull();
      } else {
        await dialog.getByRole("button", { name: "閉じる" }).click();
      }
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
      expect(overflow).toBeLessThanOrEqual(1);
    }

    const logDirectory = join(dataDirectory, "logs", String(app.id));
    await mkdir(logDirectory, { recursive: true });
    await appendFile(join(logDirectory, "stderr.log"), "ERROR E2E-private-log-body\n", "utf8");
    await expect.poll(async () => {
      const response = await page.request.get("/api/v1/alert-events?limit=200");
      const events = await response.json() as Array<{ rule_name: string; message: string }>;
      return events.find((event) => event.rule_name === "E2E new log ERROR")?.message ?? null;
    }, { timeout: 25_000, intervals: [500, 1000, 2000] }).toBe("ログ ERROR = 1.0");
    expect(browserErrors).toEqual([]);
  } finally {
    if (ruleId !== null) await page.request.delete(`/api/v1/alert-rules/${ruleId}`, { headers });
    await page.request.delete(`/api/v1/apps/${app.id}/logs?stream=all`, { headers });
    await page.request.delete(`/api/v1/apps/${app.id}`, { headers });
  }
});
