import { expect, test, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;
const manifestPath = new URL("../../tools/fake-addon/control-deck-addon.json", import.meta.url);

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page).not.toHaveURL(/\/login/);
}

async function addonRequest(page: Page, path: string, method: string, body?: unknown) {
  return page.evaluate(async ({ path, method, body }) => {
    const response = await fetch(`/api/v1${path}`, {
      method,
      credentials: "same-origin",
      headers: {
        "X-Requested-With": "ControlDeck",
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    return { status: response.status, text: await response.text() };
  }, { path, method, body });
}

test.afterEach(async ({ page }) => {
  await addonRequest(page, "/addons/fake-addon", "DELETE").catch(() => undefined);
});

test("embedded Add-on Bridge preserves host authority and lifecycle UX", async ({ page, request }) => {
  test.setTimeout(120_000);
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  const fakeHealth = await request.get("http://127.0.0.1:9130/health").catch(() => null);
  test.skip(!fakeHealth?.ok(), "the fake add-on service must be running on port 9130");

  const manifest = JSON.parse(await readFile(manifestPath, "utf8")) as Record<string, unknown>;
  await login(page);
  await addonRequest(page, "/addons/fake-addon", "DELETE");
  expect((await addonRequest(page, "/addons", "POST", manifest)).status).toBe(201);
  expect((await addonRequest(page, "/addons/fake-addon/enable", "POST", {
    granted_capabilities: manifest.host_capabilities,
  })).status).toBe(200);

  await page.emulateMedia({ colorScheme: "light" });
  await page.evaluate(() => localStorage.setItem("cd-theme", "system"));
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/x/fake-addon/workspace");
  const embedded = page.frameLocator('iframe[title="Control Deck Fake Add-on — workspace"]');
  await expect(embedded.getByText("Host Bridge ready")).toBeVisible();
  await expect(embedded.getByText(/WebSocket: .*ready/)).toBeVisible();
  await expect(embedded.locator("html")).toHaveAttribute("data-locale", /^(en|ja)$/);
  await expect(embedded.locator("html")).toHaveAttribute("data-safe-area", '{"top":0,"right":0,"bottom":0,"left":0}');
  const firstLoad = await embedded.locator("html").getAttribute("data-load-id");
  expect(firstLoad).toBeTruthy();

  await page.emulateMedia({ colorScheme: "dark" });
  await expect(embedded.locator("html")).toHaveAttribute("data-scheme", "dark");
  expect(await embedded.locator("html").getAttribute("data-load-id")).toBe(firstLoad);

  await embedded.getByRole("button", { name: "Open details route" }).click();
  await expect(page).toHaveURL(/\/x\/fake-addon\/workspace\/details$/);
  await expect(embedded.locator("#route-state")).toHaveText("/details");
  expect(await embedded.locator("html").getAttribute("data-load-id")).toBe(firstLoad);
  await page.goBack();
  await expect(page).toHaveURL(/\/x\/fake-addon\/workspace$/);
  await expect(embedded.locator("#route-state")).toHaveText("/");
  await page.goForward();
  await expect(page).toHaveURL(/\/x\/fake-addon\/workspace\/details$/);
  expect(await embedded.locator("html").getAttribute("data-load-id")).toBe(firstLoad);

  await page.goto("/x/fake-addon/workspace/details");
  await expect(embedded.getByText("Host Bridge ready")).toBeVisible();
  await expect(embedded.locator("#route-state")).toHaveText("/details");
  const sharedUrlLoad = await embedded.locator("html").getAttribute("data-load-id");
  expect(sharedUrlLoad).not.toBe(firstLoad);
  await page.reload();
  await expect(embedded.locator("#route-state")).toHaveText("/details");

  await embedded.locator("body").press("Control+k");
  await expect(page.getByLabel("コマンド検索")).toBeVisible();
  await page.keyboard.press("Escape");
  await embedded.getByRole("button", { name: "Show notification" }).click();
  await expect(page.getByText("Fake Add-on: Bridge notification")).toBeVisible();
  await embedded.getByRole("button", { name: "Toggle unsaved" }).click();
  await expect(page.getByText("未保存", { exact: true })).toBeVisible();
  await embedded.getByRole("button", { name: "Toggle unsaved" }).click();
  await expect(page.getByText("未保存", { exact: true })).toHaveCount(0);

  await embedded.getByRole("button", { name: "Pick file" }).click();
  const fileDialog = page.getByText("Fake Add-onへ渡すファイル").locator("xpath=ancestor::div[contains(@class,'fixed')]");
  await fileDialog.getByRole("button", { name: "/data1tb/ControlDeck/app", exact: true }).click();
  await fileDialog.getByRole("button", { name: "AGENTS.md", exact: true }).click();
  await expect(embedded.locator("#picker-state")).toContainText("File grant:");
  await expect(embedded.locator("#picker-state")).not.toContainText("/data1tb/");

  const projects = await page.evaluate(async () => {
    const response = await fetch("/api/v1/project-lab/projects", { credentials: "same-origin" });
    return await response.json() as Array<{ id: string; name: string }>;
  });
  expect(projects.length).toBeGreaterThan(0);
  await embedded.getByRole("button", { name: "Pick project" }).click();
  const projectDialog = page.getByRole("dialog", { name: "Fake Add-onへ渡すプロジェクト" });
  await projectDialog.locator("button").filter({ hasText: projects[0].name }).first().click();
  await expect(embedded.locator("#picker-state")).toContainText(`Project: ${projects[0].id}`);
  await embedded.getByRole("button", { name: "Pick project" }).focus();
  await embedded.getByRole("button", { name: "Pick project" }).press("Tab");
  await expect.poll(() => page.evaluate(() => document.activeElement?.tagName)).not.toBe("IFRAME");

  await page.setViewportSize({ width: 320, height: 700 });
  await page.goto("/x/fake-addon/workspace");
  await expect(page.getByRole("heading", { level: 1, name: "Control Deck Fake Add-on" })).toBeVisible();
  await expect(page.locator("iframe")).toHaveCount(0);

  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/x/fake-addon/silent");
  await expect(page.getByText("8秒以内に応答がありませんでした。serviceと設定を確認してください。")).toBeVisible({ timeout: 10_000 });

  await page.goto("/x/fake-addon/workspace");
  await expect(embedded.getByText("Host Bridge ready")).toBeVisible();
  await page.evaluate(() => {
    (window as typeof window & { addonDisable?: Promise<Response> }).addonDisable = fetch(
      "/api/v1/addons/fake-addon/disable",
      { method: "POST", credentials: "same-origin", headers: { "X-Requested-With": "ControlDeck" } },
    );
  });
  await expect(embedded.locator("html")).toHaveAttribute("data-disable-pending", "true", { timeout: 1_800 });
  await page.evaluate(async () => { await (window as typeof window & { addonDisable?: Promise<Response> }).addonDisable; });
  await expect(page.getByRole("heading", { name: "拡張機能を利用できません" })).toBeVisible();
});
