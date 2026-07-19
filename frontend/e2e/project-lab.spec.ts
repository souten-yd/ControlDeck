import { expect, test } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;
const projectId = process.env.CONTROL_DECK_E2E_PROJECT;

test("discovers CodeDEV artifacts and previews them safely from mobile to desktop", async ({ page }) => {
  test.skip(!username || !password || !projectId, "E2E credentials and CONTROL_DECK_E2E_PROJECT are required");
  const runtimeErrors: string[] = [];
  page.on("console", (message) => message.type() === "error" && runtimeErrors.push(message.text()));
  page.on("pageerror", (error) => runtimeErrors.push(error.message));
  await page.setViewportSize({ width: 320, height: 700 });
  await page.goto("/project-lab");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByLabel("ユーザー名")).toBeHidden();
  runtimeErrors.length = 0;

  await page.goto("/project-lab");
  await expect(page.getByRole("heading", { name: "Project Lab" })).toBeVisible();
  await page.getByRole("button", { name: /Codex Project Lab E2E/ }).click();
  await expect(page.getByRole("heading", { name: "Codex Project Lab E2E" })).toBeVisible();
  await expect(page.getByText("成果物preview · 明示実行")).toBeVisible();
  await page.getByRole("button", { name: /result.json/ }).click();
  await expect(page.getByText('"api_token": "***"', { exact: false })).toBeVisible();
  await expect(page.getByText("must-not-leak")).toHaveCount(0);
  await page.getByRole("button", { name: /metrics.csv/ }).click();
  await expect(page.getByRole("cell", { name: "cpu" })).toBeVisible();
  await page.getByRole("button", { name: /index.html/ }).click();
  const frame = page.frameLocator('iframe[title="index.html preview"]');
  await expect(frame.getByRole("heading", { name: "Project Lab Preview" })).toBeVisible();

  for (const viewport of [{ width: 320, height: 700 }, { width: 390, height: 844 }, { width: 1280, height: 800 }]) {
    await page.setViewportSize(viewport);
    const layout = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth, body: document.body.scrollWidth }));
    expect(layout.document).toBeLessThanOrEqual(layout.viewport);
    expect(layout.body).toBeLessThanOrEqual(layout.viewport);
  }
  const unexpectedErrors = runtimeErrors.filter((message) => !message.includes("frame is sandboxed") || !message.includes("allow-scripts"));
  expect(unexpectedErrors).toEqual([]);
});
