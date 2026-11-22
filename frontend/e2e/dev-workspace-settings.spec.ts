import { expect, test } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

// 目的: 設定 → 開発（作業場）に、役割の要約・隔離・ディスクの下限が出て、編集が既存の API（workflow・settings）へ届くこと。
// 根拠: 開発の設定が OpenCode 画面・AI アシスタントに散らばっていたので 1 か所に集めた（ROADMAP U10）。新しい設定値は持たない。
for (const width of [320, 1280]) {
  test(`dev workspace settings at ${width}px`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials required");
    await page.setViewportSize({ width, height: 900 });
    await establishSession(page, context);
    const workflow = { auto_review: true, isolation: "box", isolation_effective: "box", roles: { plan: "strata-plan", build: "qwen-build" }, auto_advisor: "off" };
    let workflowPut: any;
    let settingsPut: any;
    await page.route("**/api/v1/opencode/workflow", async (route) => {
      if (route.request().method() === "PUT") { workflowPut = route.request().postDataJSON(); Object.assign(workflow, workflowPut); }
      await route.fulfill({ json: workflow });
    });
    await page.route("**/api/v1/opencode/status**", (route) => route.fulfill({ json: {
      feature: {}, runtimes: {}, active_runtime: "v2",
      settings: { base_url: "http://127.0.0.1:8000/v1", model: "default-model", project_path: "", runtime: "v2", workspace_min_free_gib: 15 },
    } }));
    await page.route("**/api/v1/opencode/settings", async (route) => {
      settingsPut = route.request().postDataJSON();
      await route.fulfill({ json: settingsPut });
    });
    await page.route("**/api/v1/chat/opencode/permission-mode", (route) => route.fulfill({ json: { mode: "auto" } }));

    await page.goto("/settings/dev");
    const section = page.locator("#dev-workspace");
    await expect(section.getByRole("heading", { name: "開発（作業場）" })).toBeVisible();
    await expect(section.getByText("既定のモデル: default-model")).toBeVisible();
    await expect(section.getByText("qwen-build")).toBeVisible();
    await expect(section.getByText("GitHub の PR", { exact: false })).toBeVisible();

    await section.getByLabel("ディスクの空きの下限").fill("30");
    await section.getByRole("button", { name: "保存", exact: true }).click();
    await expect.poll(() => settingsPut?.workspace_min_free_gib).toBe(30);
    expect(settingsPut.model).toBe("default-model");

    await section.getByLabel("自動レビュー").uncheck();
    await expect.poll(() => workflowPut?.auto_review).toBe(false);

    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}
