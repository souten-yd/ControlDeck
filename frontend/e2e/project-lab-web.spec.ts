import { expect, test } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;
const projectId = process.env.CONTROL_DECK_E2E_PROJECT;

test("starts, previews, and stops an isolated Project Lab Web profile", async ({ page }) => {
  test.skip(!username || !password || !projectId, "Project Lab Web E2E credentials are required");
  const runtimeErrors: string[] = [];
  page.on("console", (message) => message.type() === "error" && runtimeErrors.push(message.text()));
  page.on("pageerror", (error) => runtimeErrors.push(error.message));
  await page.setViewportSize({ width: 320, height: 700 });
  await page.goto("/project-lab");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByLabel("ユーザー名")).toBeHidden();
  await page.goto("/project-lab");
  await page.getByRole("button", { name: /Codex Project Web E2E/ }).click();
  await expect(page.getByRole("heading", { name: "Codex Project Web E2E" })).toBeVisible();
  await page.waitForTimeout(300);
  runtimeErrors.length = 0;
  await page.getByRole("button", { name: "起動", exact: true }).click();
  const runButton = page.getByRole("button", { name: /#\d+ · web/ }).first();
  await expect(runButton).toBeVisible();
  await expect.poll(async () => page.evaluate(async (id) => {
    const response = await fetch(`/api/v1/project-lab/runs?project_id=${encodeURIComponent(id)}`, { credentials: "same-origin" });
    const rows = await response.json() as Array<{ previewReady: boolean }>;
    return rows[0]?.previewReady ?? false;
  }, projectId!)).toBe(true);
  await runButton.click();
  const frame = page.frameLocator('iframe[title="Codex Project Web E2E Web preview"]');
  await expect(frame.getByRole("heading", { name: "Project Web Preview" })).toBeVisible({ timeout: 10_000 });

  for (const viewport of [
    { width: 320, height: 700 }, { width: 390, height: 844 }, { width: 1280, height: 800 },
  ]) {
    await page.setViewportSize(viewport);
    const layout = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth, body: document.body.scrollWidth }));
    expect(layout.document).toBeLessThanOrEqual(layout.viewport);
    expect(layout.body).toBeLessThanOrEqual(layout.viewport);
  }
  await page.getByRole("button", { name: "停止", exact: true }).click();
  await expect(page.getByText("CANCELED", { exact: true }).first()).toBeVisible();
  expect(runtimeErrors).toEqual([]);
});
