// 目的: ファン設定の画面で、温度マージンの編集・保存と、利用者プリセットの保存・削除ができること。
// 根拠: 温度マージン（目標追従の導入時）。プリセットの追加と削除は 2026-11-17 の依頼。
import { expect, test } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

for (const width of [320, 1280]) {
  test(`temperature margin is editable and saved at ${width}px`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials required");
    await page.setViewportSize({ width, height: 800 });
    await establishSession(page, context);
    let snapshot: any;
    let saved: any;
    await page.route("**/api/v1/system/fan-control", async (route) => {
      if (!snapshot) {
        const response = await route.fetch();
        snapshot = await response.json();
        snapshot.settings = { ...snapshot.settings, enabled: false, control_mode: "target",
          temperature_margin_c: 5, warm_rate_percent_per_min: 30, targets_c: { temp2: 90 } };
        snapshot.devices = [{ bdf: "0000:03:00.0", sensors: [{ id: "temp2", label: "junction",
          value_c: 65, crit_c: 110, max_target_c: 100, default_target_c: 90 }],
          curve_supported: true, zero_rpm_supported: false, curve_limits: null, fan_rpm: 1000 }];
      }
      if (route.request().method() === "PUT") {
        saved = route.request().postDataJSON();
        snapshot.settings = saved;
      }
      await route.fulfill({ json: snapshot });
    });
    await page.goto("/settings/hardware");
    await page.getByText("設定（有効化・プリセット・目標温度）", { exact: true }).click();
    const margin = page.getByRole("spinbutton", { name: "温度マージン 数値入力", exact: true });
    await expect(margin).toHaveValue("5");
    await margin.fill("7.5");
    await expect(page.getByText(/追従温度 82.5°C/)).toBeVisible();
    await page.getByRole("button", { name: "保存して適用", exact: true }).click();
    await expect.poll(() => saved?.temperature_margin_c).toBe(7.5);
    expect(saved.control_mode).toBe("target");
    await expect(margin).toHaveValue("7.5");
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `/tmp/fan-temperature-margin-${width}.png`, fullPage: true });
  });
}

test("user fan presets can be saved from the current settings and deleted", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials required");
  await page.setViewportSize({ width: 390, height: 844 });
  await establishSession(page, context);
  let snapshot: any;
  let added: any;
  let deleted = "";
  await page.route("**/api/v1/system/fan-control", async (route) => {
    if (!snapshot) {
      const response = await route.fetch();
      snapshot = await response.json();
      snapshot.settings = { ...snapshot.settings, enabled: false, control_mode: "pid", min_percent: 25,
        targets_c: { temp2: 88 }, preset_id: "custom" };
      snapshot.devices = [{ bdf: "0000:03:00.0", sensors: [{ id: "temp2", label: "junction",
        value_c: 65, crit_c: 110, max_target_c: 100, default_target_c: 90 }],
        curve_supported: true, zero_rpm_supported: false, curve_limits: null, fan_rpm: 1000 }];
    }
    await route.fulfill({ json: snapshot });
  });
  await page.route("**/api/v1/system/fan-control/presets**", async (route) => {
    if (route.request().method() === "POST") {
      added = route.request().postDataJSON();
      const preset = { id: "user-0a1b2c3d", label: added.label, description: "", user: true, settings: added.settings };
      snapshot.presets = [...snapshot.presets, preset];
      return route.fulfill({ status: 201, json: preset });
    }
    deleted = route.request().url().split("/").pop()!;
    snapshot.presets = snapshot.presets.filter((preset: any) => preset.id !== deleted);
    return route.fulfill({ status: 204, body: "" });
  });
  await page.goto("/settings/hardware");
  await page.getByText("設定（有効化・プリセット・目標温度）", { exact: true }).click();
  await page.getByRole("textbox", { name: "保存するプリセットの名前" }).fill("夜の静音");
  await page.getByRole("button", { name: "プリセットに保存" }).click();
  await expect.poll(() => added?.label).toBe("夜の静音");
  expect(added.settings.control_mode).toBe("pid");
  expect(added.settings.min_percent).toBe(25);
  const card = page.getByRole("button", { name: /夜の静音/ }).first();
  await expect(card).toBeVisible();
  await expect(card).toHaveAttribute("aria-pressed", "true");
  const remove = page.getByRole("button", { name: "プリセット「夜の静音」を削除" });
  expect((await remove.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  await remove.click();
  await page.getByRole("button", { name: "削除する" }).click();
  await expect.poll(() => deleted).toBe("user-0a1b2c3d");
  await expect(page.getByRole("button", { name: /夜の静音/ })).toHaveCount(0);
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  // 組み込みのプリセットには削除が無い
  await expect(page.getByRole("button", { name: /を削除$/ })).toHaveCount(0);
});
