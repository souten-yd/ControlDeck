// 目的: 設定の「GPU 設定」で、周波数と電圧を別々に指定でき、試験設定は入力欄を埋めるだけで「適用」を押すまで書かないこと。
//       設定値と実測を分けて見られ、Models の共通設定から電力・静音が移り、移動ボタンが消えていること。
// 根拠: 2026-11-18 利用者「電力制限 210 W のままホットスポット温度を下げたい」。API は模擬する（実機へ書かない）。
import { expect, test } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

const state = (core: number, voltage: number, previous = {}) => ({
  available: true, bdf: "0000:03:00.0", xfx: true, helper_ready: true, previous,
  preset: { label: "XFX R9700 試験設定（−500 MHz / −25 mV）", core: -500, voltage: -25 },
  boot: { enabled: false, core: null, voltage: null, state: "", message: "" },
  items: { core: { supported: true, unit: "MHz", current: core, min: -500, max: 1000 }, voltage: { supported: true, unit: "mV", current: voltage, min: -200, max: 0 } },
  live: { core_mhz: 2976, power_w: 205, power_limit_w: 210, hotspot_c: 90, fan_rpm: 2647, busy_percent: 100 },
});

for (const width of [320, 1280]) {
  test(`GPU settings apply only on request (${width}px)`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials required");
    await page.setViewportSize({ width, height: 800 });
    await establishSession(page, context);
    const applied: unknown[] = [];
    await page.route(/\/api\/v1\/system\/gpu-tuning$/, (route) => route.fulfill({ json: state(applied.length ? -500 : 0, applied.length ? -25 : 0, applied.length ? { core: 0, voltage: 0 } : {}) }));
    await page.route(/\/api\/v1\/system\/gpu-tuning\/apply$/, (route) => { applied.push(route.request().postDataJSON()); return route.fulfill({ json: state(-500, -25, { core: 0, voltage: 0 }) }); });
    await page.goto("/settings/gpu");
    const panel = page.locator("[data-gpu-tuning]");
    await expect(panel).toContainText("指定できる範囲: −500 MHz 〜 +1000 MHz");
    await expect(panel.locator("[data-gpu-live]")).toContainText("2,976 MHz");
    await panel.getByRole("button", { name: /試験設定/ }).click();
    await expect(panel.getByLabel(/最大コア周波数オフセット/)).toHaveValue("-500");
    await expect(panel.getByLabel(/コア電圧オフセット/)).toHaveValue("-25");
    expect(applied).toEqual([]);                                         // 選んだだけでは書かない
    await panel.getByRole("button", { name: "適用", exact: true }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "適用", exact: true }).click();
    await expect.poll(() => applied.length).toBe(1);
    expect(applied[0]).toEqual({ core_offset_mhz: -500, voltage_offset_mv: -25 });
    await expect(panel.getByRole("button", { name: "適用前の値に戻す" })).toBeVisible();
    await expect(panel.locator("[data-gpu-boot]")).toContainText("再起動後も自動で適用する");
    await expect(page.getByRole("link", { name: /^温度管理/ }).first()).toBeVisible();
    await expect(page.getByRole("link", { name: /^AI モデル/ })).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
  });
}
