// 目的: 設定 → 機能と拡張 → オプション機能で、毎日の自動更新のオン・時刻を保存でき、「今すぐすべて更新」で
//       一括更新の Job が始まり、終わると前回の結果（更新・失敗）が出ること。320px でも横にはみ出さないこと。
// 根拠: 2026-11-20 利用者「オプション機能を 1 日 1 回ぐらい時間を決めて自動でバージョンアップ。ボタンと時間設定を」。
//       API は模擬する（実際の更新は走らせない）。
import { expect, test } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

for (const width of [320, 1280]) {
  test(`feature auto update settings and run now (${width}px)`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials required");
    await page.setViewportSize({ width, height: 800 });
    await establishSession(page, context);
    let settings = { enabled: false, time: "04:00", running: false, last: {} as Record<string, unknown> };
    const saved: unknown[] = [];
    let jobPolls = 0;
    await page.route(/\/api\/v1\/features\/auto-update$/, async (route) => {
      if (route.request().method() === "PUT") {
        const patch = route.request().postDataJSON();
        saved.push(patch);
        settings = { ...settings, ...patch };
      }
      await route.fulfill({ json: settings });
    });
    await page.route(/\/api\/v1\/features\/auto-update\/run-jobs$/, (route) => route.fulfill({ status: 201, json: { job_id: "bulk1" } }));
    await page.route(/\/api\/v1\/jobs\/bulk1$/, (route) => {
      jobPolls += 1;
      if (jobPolls < 2) return route.fulfill({ json: { status: "running", error: "", progress: { status: "OpenCode を確認・更新中" } } });
      settings = { ...settings, last: { finished_at: Date.now() / 1000, trigger: "manual", results: [
        { id: "opencode-v2", name: "OpenCode", before: "1.0", after: "1.1", status: "updated", message: "" },
        { id: "strata", name: "Strata", before: "v1", after: "v1", status: "failed", message: "モデルが稼働中" },
      ] } };
      return route.fulfill({ json: { status: "succeeded", error: "" } });
    });
    await page.goto("/settings/features");
    const panel = page.locator("[data-feature-auto-update]");
    await expect(panel).toBeVisible();
    await panel.getByLabel("オプション機能を毎日自動で更新する").click();
    await expect(panel.getByLabel("オプション機能を毎日自動で更新する")).toBeChecked();
    await expect.poll(() => saved).toEqual([{ enabled: true }]);
    await panel.getByLabel("自動更新の時刻").fill("03:30");
    await panel.getByLabel("自動更新の時刻").blur();
    await expect.poll(() => saved).toEqual([{ enabled: true }, { time: "03:30" }]);
    await panel.getByRole("button", { name: "今すぐすべて更新" }).click();
    await expect(panel.locator("[data-auto-update-last]")).toContainText("OpenCode 1.0 → 1.1", { timeout: 15_000 });
    await expect(panel.locator("[data-auto-update-last]")).toContainText("失敗 Strata（モデルが稼働中）");
    await expect(panel.getByRole("button", { name: "今すぐすべて更新" })).toBeEnabled();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    await page.screenshot({ path: `test-results/feature-auto-update-${width}.png` });
  });
}
