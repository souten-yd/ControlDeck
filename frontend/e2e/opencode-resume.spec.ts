import { expect, test } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

/** OpenCode 画面: 閉じた（TUI を終えた）セッションが一覧に残り、続きから開ける。API は差し替える。 */
for (const width of [1280, 320]) {
  test(`closed OpenCode session can be resumed (${width}px)`, async ({ page }) => {
    test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
    await page.setViewportSize({ width, height: 800 });
    await page.goto("/");
    await page.getByLabel("ユーザー名").fill(username!);
    await page.getByLabel("パスワード").fill(password!);
    await page.getByRole("button", { name: "ログイン", exact: true }).click();
    await expect(page.getByLabel("ユーザー名")).toBeHidden();

    await page.route(/\/api\/v1\/meta$/, async (route) => {
      const response = await route.fetch();
      const meta = await response.json();
      meta.enabled_features = [...(meta.enabled_features ?? []), "opencode-v2"];
      return route.fulfill({ response, json: meta });
    });
    const feature = { id: "opencode-v2", installed: true, managed: true, enabled: true, version: "2.3.0", health: "ok", executable: "" };
    await page.route(/\/api\/v1\/opencode\/status/, (route) => route.fulfill({ json: {
      feature, settings: { base_url: "http://127.0.0.1:1/v1", model: "auto", project_path: "", runtime: "v2" },
      active_runtime: "v2", runtimes: { v2: feature },
    } }));
    await page.route(/\/api\/v1\/opencode\/projects$/, (route) => route.fulfill({ json: { root: "/CodeDEV", projects: [] } }));
    await page.route(/\/api\/v1\/opencode\/workflows/, (route) => route.fulfill({ json: [] }));
    const posted: unknown[] = [];
    let forgotten = false;
    await page.route(/\/api\/v1\/opencode\/sessions(\/forget)?$/, async (route) => {
      const request = route.request();
      if (request.url().endsWith("/forget")) {
        forgotten = true;
        return route.fulfill({ status: 204, body: "" });
      }
      if (request.method() === "POST") {
        posted.push(request.postDataJSON());
        return route.fulfill({ status: 201, json: { id: "zzresume" } });
      }
      return route.fulfill({ json: { sessions: forgotten ? [] : [
        { terminal_id: "old1", project_path: "/CodeDEV/Train", project_name: "Train", started_at: 1794887000, running: false },
      ] } });
    });

    await page.goto("/opencode-v2");
    const row = page.getByRole("listitem").filter({ hasText: "Train" });
    await expect(page.getByText("閉じたセッション（続きから開けます）")).toBeVisible();
    await expect(row.getByText("終了済み")).toBeVisible();
    const button = row.getByRole("button", { name: "続きから開く" });
    const box = await button.boundingBox();
    expect(box!.height).toBeGreaterThanOrEqual(44);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    await button.click();
    await expect.poll(() => posted.length).toBe(1);
    expect(posted[0]).toMatchObject({ project_path: "/CodeDEV/Train", resume: "last" });

    // 一覧から外す
    await page.goto("/opencode-v2");
    await page.getByRole("button", { name: "Train を一覧から外す" }).click();
    await expect(page.getByText("閉じたセッション（続きから開けます）")).toHaveCount(0);
  });
}
