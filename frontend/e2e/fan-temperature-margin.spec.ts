// 目的: ファン制御 v2 の設定画面で、利用者が決める最小限の値（上限温度・マージン・方式・温度マップ）を
//       編集して保存でき、自動調整を確認してから始められること。iPhone 幅（320px）でも横にはみ出さないこと。
// 根拠: 2026-11-17 利用者の依頼「設定が多すぎて分からない・入れ子・プリセットが謎」で画面を作り直した
//       （4 方式・プリセット・入れ子を撤去し、PID / 温度マップの 2 方式と 1 画面に）。旧画面の
//       「プリセットの保存・削除」と「温度追従のマージン」の確認はこのファイルで置き換えた。
//       API は page.route で模擬する（実機の GPU とファンを動かさない）。
import { expect, test, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

const BDF = "0000:03:00.0";

function snapshot(overrides: Record<string, unknown> = {}) {
  const sensors = [
    { id: "temp1", label: "edge", kind: "edge", value_c: 55, crit_c: 100, max_limit_c: 95, default_limit_c: 85 },
    { id: "temp2", label: "junction", kind: "junction", value_c: 72, crit_c: 110, max_limit_c: 100, default_limit_c: 95 },
    { id: "temp3", label: "mem", kind: "mem", value_c: 66, crit_c: 105, max_limit_c: 95, default_limit_c: 90 },
  ];
  return {
    settings: {
      enabled: true, bdf: BDF, mode: "pid", limits_c: { temp1: 85, temp2: 95, temp3: 90 }, margin_c: 5,
      min_percent: 20, smoothing_s: 60, pid_kp: 1.33, pid_ti: 50, map_points: [], startup_mode: "apply_saved",
    },
    devices: [{ bdf: BDF, vram_bytes: 32 * 2 ** 30, sensors, fan_rpm: 1500, curve_supported: true,
      power_w: 180, power_cap_w: 300, restore_supported: false,
      curve_limits: { temp_min_c: 25, temp_max_c: 110, pwm_min_percent: 15, pwm_max_percent: 100 } }],
    helper_installed: true, state: "controlling", error: "", pending_auto_restore: false,
    active: {
      bdf: BDF, fan_rpm: 1500, fan_percent: 42, power_w: 180, governing_sensor: "temp2",
      sensors: sensors.map((s) => ({ ...s, limit_c: s.default_limit_c, target_c: s.default_limit_c - 5,
        headroom_c: s.default_limit_c - s.value_c })),
      control: { output_percent: 42, mode: "pid", governing_sensor: "temp2", error_c: -18, feedforward_percent: 30,
        p_percent: 4, i_percent: 8, emergency: false, pv_c: { temp2: 72 } },
    },
    poll_interval_seconds: 3, model: null,
    autotune: { phase: "off", message: "", running: false, elapsed_s: null, hold_percent: null, result: null },
    warning: "上限温度は保証値ではありません。",
    ...overrides,
  };
}

/** GET はいまの状態、PUT は送られた設定をそのまま保存したことにする。history は空で返す。 */
async function mockFanApi(page: Page, state: { status: any; saved?: any; tune?: any }) {
  await page.route("**/api/v1/system/fan-control", async (route) => {
    if (route.request().method() === "PUT") {
      state.saved = route.request().postDataJSON();
      state.status = { ...state.status, settings: state.saved };
    }
    await route.fulfill({ json: state.status });
  });
  await page.route("**/api/v1/system/fan-control/history**", (route) => route.fulfill({ json: { samples: [] } }));
  await page.route("**/api/v1/system/fan-control/autotune", async (route) => {
    state.tune = route.request().postDataJSON();
    state.status = { ...state.status, autotune: { phase: "idle", message: "アイドルの温度を測っています",
      running: true, elapsed_s: 12, hold_percent: 40, result: null, llm: "strata-test", tokens: 0 } };
    await route.fulfill({ json: state.status.autotune });
  });
  await page.route("**/api/v1/models/llama/capacity", (route) => route.fulfill({
    json: { endpoints: [{ running_alias: "strata-test" }], failed: [] } }));
}

const noOverflow = (page: Page) =>
  expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);

for (const width of [320, 1280]) {
  test(`fan limits and margin are edited and saved at ${width}px`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials required");
    await page.setViewportSize({ width, height: 800 });
    await establishSession(page, context);
    const state: any = { status: snapshot() };
    await mockFanApi(page, state);
    await page.goto("/settings/hardware");

    // 状態: センサーごとの温度・目標・上限と、ファン・電力
    await expect(page.getByRole("meter", { name: "hotspot の温度" })).toBeVisible();
    await expect(page.getByText("目標 90・上限 95")).toBeVisible();
    await expect(page.getByText("42%", { exact: true })).toBeVisible();
    // プリセットの UI は無い
    await expect(page.getByText(/プリセット/)).toHaveCount(0);

    // 保存ボタンは変更があるときだけ出る
    const saveButton = page.getByRole("button", { name: "保存して適用", exact: true });
    await expect(saveButton).toHaveCount(0);
    const margin = page.getByRole("spinbutton", { name: "マージン", exact: true });
    await expect(margin).toHaveValue("5");
    await margin.fill("7.5");
    await expect(page.getByText("目標 87.5°C")).toBeVisible(); // hotspot 上限 95 − 7.5
    const limit = page.getByRole("spinbutton", { name: "VRAMの上限", exact: true });
    await limit.fill("88");
    await expect(saveButton).toBeVisible();
    expect((await saveButton.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    await noOverflow(page);
    await page.screenshot({ path: `/tmp/fan-control-v2-${width}.png`, fullPage: true });

    await saveButton.click();
    await expect.poll(() => state.saved?.margin_c).toBe(7.5);
    expect(state.saved.limits_c.temp3).toBe(88);
    expect(state.saved.mode).toBe("pid");
    await expect(saveButton).toHaveCount(0);
  });
}

test("temperature map is shown only in map mode and its points are saved in order", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials required");
  await page.setViewportSize({ width: 390, height: 844 });
  await establishSession(page, context);
  const state: any = { status: snapshot() };
  await mockFanApi(page, state);
  await page.goto("/settings/hardware");

  // PID のときは効き具合（Kp・Ti）だけ、マップは無い
  await expect(page.getByTestId("fan-pid-gains")).toBeVisible();
  await expect(page.getByTestId("fan-map")).toHaveCount(0);

  const mapMode = page.getByRole("radio", { name: /^温度マップ/ });
  expect((await mapMode.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  await mapMode.click();
  await expect(mapMode).toHaveAttribute("aria-checked", "true");
  await expect(page.getByTestId("fan-pid-gains")).toHaveCount(0);
  const map = page.getByTestId("fan-map");
  await expect(map.getByRole("img", { name: /温度マップ/ })).toBeVisible();
  // 自動調整前は既定の 4 点（目標 90°C から 20・10・5・0°C 下）
  await expect(map.getByRole("spinbutton", { name: /の温度$/ })).toHaveCount(4);
  await expect(map.getByRole("spinbutton", { name: "点1の温度" })).toHaveValue("70");

  // 回転を下げすぎると（温度が高いほど低い）保存できない
  await map.getByRole("spinbutton", { name: "点4の回転" }).fill("10");
  await expect(map.getByRole("alert")).toContainText("回転を同じか高く");
  await expect(page.getByRole("button", { name: "保存して適用" })).toBeDisabled();
  await map.getByRole("spinbutton", { name: "点4の回転" }).fill("65");
  await expect(map.getByRole("alert")).toHaveCount(0);
  await noOverflow(page);

  await page.getByRole("button", { name: "保存して適用" }).click();
  await expect.poll(() => state.saved?.mode).toBe("map");
  expect(state.saved.map_points).toHaveLength(4);
  expect(state.saved.map_points[0].below_c).toBe(20);
  expect(state.saved.map_points[3]).toEqual({ below_c: 0, percent: 65 });
});

test("auto tuning asks for confirmation, then shows its stage and a cancel button", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials required");
  await page.setViewportSize({ width: 320, height: 700 });
  await establishSession(page, context);
  const state: any = { status: snapshot() };
  await mockFanApi(page, state);
  await page.goto("/settings/hardware");

  await page.getByRole("button", { name: "自動調整を始める" }).click();
  const dialog = page.getByRole("alertdialog", { name: "自動調整を始めますか" });
  await expect(dialog).toContainText("30〜90 分");
  await expect(dialog).toContainText("その間 LLM は使えません");
  await dialog.getByRole("combobox", { name: "負荷に使う LLM" }).selectOption("strata-test");
  await dialog.getByRole("button", { name: "始める" }).click();
  await expect.poll(() => state.tune).toEqual({ action: "start", llm: "strata-test" });

  const steps = page.getByRole("list", { name: "自動調整の段階" });
  await expect(steps.locator('[aria-current="step"]')).toHaveText("アイドル");
  await expect(page.getByText("strata-test で調整中")).toBeVisible();
  await expect(page.getByRole("button", { name: "中止" })).toBeVisible();
  await noOverflow(page);
});
