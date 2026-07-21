import { expect, test, type Page } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;
const marker = `AUTOMATION_E2E_${Date.now()}`;
const ownedSnippetIds = new Set<number>();
const ownedScheduleIds = new Set<number>();
const ownedSessionIds = new Set<string>();

async function login(page: Page) {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  await page.goto("/terminal");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByLabel("ユーザー名")).toBeHidden();
  await page.goto("/terminal");
  await expect(page.getByRole("heading", { name: "Terminal" })).toBeVisible();
}

test.afterEach(async ({ page }) => {
  const request = page.context().request;
  for (const id of ownedScheduleIds) {
    await request.delete(`/api/v1/terminal-automation/schedules/${id}`, {
      headers: { "X-Requested-With": "ControlDeck" },
    });
  }
  for (const id of ownedSnippetIds) {
    await request.delete(`/api/v1/terminal-automation/snippets/${id}`, {
      headers: { "X-Requested-With": "ControlDeck" },
    });
  }
  for (const id of ownedSessionIds) {
    await request.delete(`/api/v1/terminals/${id}`, {
      headers: { "X-Requested-With": "ControlDeck" },
    });
  }
  ownedScheduleIds.clear();
  ownedSnippetIds.clear();
  ownedSessionIds.clear();
});

test("snippet review, detached run, durable schedule, and per-session entry stay usable", async ({ page }) => {
  test.setTimeout(90_000);
  await page.setViewportSize({ width: 320, height: 700 });
  await login(page);

  await page.getByRole("button", { name: "Snippets" }).click();
  const dialog = page.getByRole("dialog", { name: "Terminal snippets and automation" });
  await expect(dialog).toBeVisible();
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0);
  const mobileBox = await dialog.boundingBox();
  expect(mobileBox?.width).toBeLessThanOrEqual(320);

  await dialog.getByRole("button", { name: "Add", exact: true }).click();
  await dialog.getByLabel("Name").fill(marker);
  await dialog.getByLabel("Description").fill("Safe automation end-to-end check");
  await dialog.getByLabel("Code or prompt").fill(`printf '${marker}_%s\\n' '{{task}}'`);
  await expect(dialog.getByText("task", { exact: true })).toBeVisible();
  const editorVisibility = await dialog.locator("[data-snippet-code]").evaluate((element) => {
    const style = getComputedStyle(element);
    return { background: style.backgroundColor, color: style.color, border: style.borderTopColor };
  });
  expect(editorVisibility.background).not.toBe(editorVisibility.color);
  expect(editorVisibility.border).not.toBe(editorVisibility.background);
  const templateHelpStyle = await dialog.locator("label:has([data-snippet-code]) + p").evaluate((element) => {
    const style = getComputedStyle(element);
    return { background: style.backgroundColor, color: style.color };
  });
  expect(templateHelpStyle.background).toBe("rgb(39, 39, 42)");
  expect(templateHelpStyle.color).toBe("rgb(244, 244, 245)");
  const parameterSurface = dialog.locator("[data-detected-parameters]");
  await expect(parameterSurface).toBeVisible();
  expect(await parameterSurface.evaluate((element) => getComputedStyle(element).backgroundColor)).not.toBe("rgba(0, 0, 0, 0)");
  await dialog.getByLabel("Tags").fill("e2e, nightly");
  await dialog.getByRole("button", { name: "Save", exact: true }).click();
  const snippet = dialog.getByRole("listitem").filter({ hasText: marker });
  await expect(snippet).toBeVisible();

  const snippetResponse = await page.request.get("/api/v1/terminal-automation/snippets");
  const snippetBody = await snippetResponse.json() as { snippets: Array<{ id: number; name: string }> };
  const snippetId = snippetBody.snippets.find((item) => item.name === marker)?.id;
  expect(snippetId).toBeTruthy();
  ownedSnippetIds.add(snippetId!);

  await snippet.getByRole("button", { name: "Use" }).click();
  await dialog.getByLabel("task", { exact: true }).fill("READY");
  const scheduleButton = dialog.getByRole("button", { name: "Schedule", exact: true });
  await expect(scheduleButton).toBeDisabled();
  await dialog.getByRole("button", { name: "Review", exact: true }).click();
  await expect(dialog.locator("[data-automation-preview]")).toContainText(`${marker}_%s`);
  await expect(dialog.locator("[data-automation-preview]")).toContainText("READY");
  await expect(scheduleButton).toBeEnabled();

  const runResponsePromise = page.waitForResponse((response) =>
    response.url().endsWith("/api/v1/terminal-automation/runs") && response.request().method() === "POST",
  );
  await dialog.getByRole("button", { name: "Run now", exact: true }).click();
  const runId = (await (await runResponsePromise).json() as { id: number }).id;
  const recent = dialog.getByText(`Run #${runId} · Detached`, { exact: true });
  await expect(recent).toBeVisible();
  await expect(recent.locator("xpath=ancestor::button")).toContainText("SUCCEEDED", { timeout: 30_000 });
  await recent.click();
  await expect(dialog.getByText(`${marker}_READY`, { exact: false })).toBeVisible();

  await dialog.getByRole("button", { name: "Review", exact: true }).click();
  await expect(scheduleButton).toBeEnabled();
  await scheduleButton.click();
  await dialog.getByLabel("Name").fill(`${marker} schedule`);
  await dialog.getByLabel("Repeat").selectOption("biweekly");
  await dialog.getByRole("button", { name: "Save", exact: true }).click();
  await expect(dialog.getByText(`${marker} schedule`, { exact: true })).toBeVisible();
  await expect(dialog.getByText("Every 2 weeks · Detached", { exact: true })).toBeVisible();

  const scheduleResponse = await page.request.get("/api/v1/terminal-automation/schedules");
  const scheduleBody = await scheduleResponse.json() as { schedules: Array<{ id: number; name: string }> };
  const scheduleId = scheduleBody.schedules.find((item) => item.name === `${marker} schedule`)?.id;
  expect(scheduleId).toBeTruthy();
  ownedScheduleIds.add(scheduleId!);

  await dialog.getByRole("button", { name: "閉じる" }).click();
  const createResponse = await page.request.post("/api/v1/terminals", {
    headers: { "X-Requested-With": "ControlDeck" },
  });
  const sessionId = (await createResponse.json() as { id: string }).id;
  ownedSessionIds.add(sessionId);
  await page.reload();
  const sessionCard = page.getByRole("listitem").filter({ hasText: `#${sessionId}` });
  await sessionCard.getByRole("button", { name: /オートメーション設定$/ }).click();
  await expect(dialog.getByRole("button", { name: "Run", exact: true })).toBeVisible();
  await expect(dialog.getByText("Send to session", { exact: true })).toBeVisible();
  await expect(dialog.getByLabel("Terminal")).toHaveValue(sessionId);

  await page.setViewportSize({ width: 1280, height: 800 });
  const desktopBox = await dialog.boundingBox();
  expect(desktopBox?.x).toBeGreaterThan(600);
  expect(desktopBox?.width).toBeLessThanOrEqual(620);
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0);
});
