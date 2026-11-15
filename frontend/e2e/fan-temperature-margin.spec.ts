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
