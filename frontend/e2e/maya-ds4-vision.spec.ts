/** 目的: DeepSeek（DS-XS）の画像の読み取りが、モデル一覧の「VISION」の印と、個別設定の入／切に出ること。
 *        画像の部品（vision tower）が無いモデルでは入／切を選べず、案内が出ること。320px と PC 幅で横にはみ出さないこと。
 *  根拠: 2026-11-18 利用者「vision の設定 ON/OFF は ControlDeck の設定として入るようになっているか」
 *        「（モデル画面で）Vision 表示になっていない」。API は page.route で模擬する（実機のモデルを動かさない）。
 *  更新: Nexarch #24。個別設定には「表示名の保存」（無効）と下端の「保存」が並ぶため、
 *        目的の設定保存は .last() で指定する（曖昧な「保存」単体では strict mode 違反）。 */
import { expect, test, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

const ALIAS = "maya-deepseek-xs";

function settings(available: boolean, vision: "on" | "off") {
  return { family: "deepseek", context: 262144, parallel: 1, gpu_index: 0, reasoning_effort: "high", thinking_budget: 32768,
    temperature: 1, top_p: 1, vram_reserve_mib: 3072, on_demand_start: true, idle_exclude: false, auto_start: false,
    running: false, dspark: "on", cpu_threads: 0, vram_experts: 0, mmap_experts: "auto", ssd_cache: "off",
    prefill_chunk: 0, vision_available: available, vision };
}

async function mock(page: Page, available: boolean, state: { put?: Record<string, unknown> }) {
  await page.route("**/api/v1/models/runtime-environment", async (route) => {
    const response = await route.fetch();
    const env = await response.json();
    env.policy = { ...env.policy, selected_runtime: "maya-s" };
    await route.fulfill({ response, json: env });
  });
  await page.route(/\/api\/v1\/models\/providers\/(llama\.cpp|strata|radiance)\/models$/, (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/models/providers/maya-s/models", (route) => route.fulfill({ json: [{
    id: ALIAS, name: "Maya · DeepSeek-V4.1-Flash · Maya-XS", size_bytes: 282.4e9, loaded: false,
    details: { runtime: "maya-s", display_name: "DeepSeek-V4.1-Flash · Maya-XS", quantization: "Maya-XS",
      vision_enabled: available, port: 8101 } }] }));
  await page.route(`**/api/v1/features/maya-s/models/${ALIAS}/settings`, async (route) => {
    if (route.request().method() === "PUT") {
      state.put = route.request().postDataJSON();
      return route.fulfill({ json: settings(available, (state.put?.vision as "on" | "off") ?? "on") });
    }
    return route.fulfill({ json: settings(available, available ? "on" : "off") });
  });
}

for (const width of [320, 1280]) {
  test(`DS-XS shows the vision badge and its read-images switch at ${width}px`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials required");
    await page.setViewportSize({ width, height: 900 });
    await establishSession(page, context);
    const state: { put?: Record<string, unknown> } = {};
    await mock(page, true, state);
    await page.goto("/models");
    const row = page.getByRole("listitem").filter({ hasText: "DeepSeek-V4.1-Flash · Maya-XS" });
    await expect(row.getByText("vision", { exact: false })).toBeVisible();
    await row.getByText("DeepSeek-V4.1-Flash · Maya-XS").click();
    const select = page.getByLabel("DeepSeek-V4.1-Flash · Maya-XS 画像の読み取り");
    await expect(select).toBeEnabled();
    await expect(select).toHaveValue("on");
    await select.selectOption("off");
    await page.getByRole("button", { name: "保存", exact: true }).last().click();
    await expect.poll(() => state.put).toEqual({ vision: "off" });            // 選んだ項目だけを送る
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
  });
}

test("a DeepSeek model without the vision tower cannot switch images on", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials required");
  await page.setViewportSize({ width: 390, height: 900 });
  await establishSession(page, context);
  await mock(page, false, {});
  await page.goto("/models");
  const row = page.getByRole("listitem").filter({ hasText: "DeepSeek-V4.1-Flash · Maya-XS" });
  await expect(row.getByText("vision", { exact: false })).toHaveCount(0);
  await row.getByText("DeepSeek-V4.1-Flash · Maya-XS").click();
  await expect(page.getByLabel("DeepSeek-V4.1-Flash · Maya-XS 画像の読み取り")).toBeDisabled();
  await expect(page.getByText("画像の部品（vision tower）が入っていません", { exact: false })).toBeVisible();
});
