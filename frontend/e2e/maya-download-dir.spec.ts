import { expect, test } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

for (const width of [320, 1280]) {
  test(`Maya のダウンロードで保存先を選べる（${width}px）`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials required");
    await page.setViewportSize({ width, height: 900 });
    await establishSession(page, context);
    await page.route("**/api/v1/models/runtime-environment", async (route) => {
      const env = await (await route.fetch()).json();
      env.policy.selected_runtime = "maya-s";
      await route.fulfill({ json: env });
    });
    await page.route("**/api/v1/features/maya-s/model", (route) => route.fulfill({ json: {
      installed: true, available: true, configured: false, built: true, running: false, version: "v1.1.64",
      settings: { gpu_index: 0 }, entrypoint: "", models_dir: "/data/models/maya-s", error: "", warning: "" } }));
    let sent: Record<string, unknown> | null = null;
    await page.route("**/api/v1/features/maya-s/model/prepare-jobs", async (route) => {
      sent = route.request().postDataJSON();
      await route.fulfill({ status: 201, json: { job_id: "j1" } });
    });
    await page.route("**/api/v1/files/roots", (route) => route.fulfill({ json: ["/data1tb"] }));
    await page.route("**/api/v1/files/list**", (route) => route.fulfill({ json: {
      path: "/data1tb/LLM", parent: "/data1tb", entries: [] } }));
    await page.goto("/models");
    await page.getByRole("button", { name: "モデルをダウンロード（約96.5GB）" }).click();
    const dialog = page.locator("[role=dialog], [role=alertdialog]").filter({ hasText: "Maya-Sをダウンロードしますか" });
    await expect(dialog).toContainText("/data/models/maya-s（既定）");
    await dialog.getByRole("button", { name: "変更" }).click();
    await page.getByRole("button", { name: "このフォルダを選択" }).click();
    await expect(dialog).toContainText("/data1tb/LLM");
    await page.screenshot({ path: `/tmp/claude-1000/-data1tb-ControlDeck/2d315ae0-9a5d-45cb-b6d8-b90a6f6db5f7/scratchpad/maya-dir-${width}.png` });
    await dialog.getByRole("button", { name: "ダウンロードを開始" }).click();
    await expect.poll(() => sent).toEqual({ gpu_index: 0, download_dir: "/data1tb/LLM" });
  });
}
