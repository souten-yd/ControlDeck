import { expect, test, type Page, type Route } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

async function login(page: Page) {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  await page.goto("/login");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page).not.toHaveURL(/\/login/);
}

const json = (route: Route, body: unknown) => route.fulfill({
  contentType: "application/json", body: JSON.stringify(body),
});

test("explains cold bootstrap, warm threshold, and suppression at mobile and desktop widths", async ({ page }) => {
  await login(page);
  let basis: "cold" | "warm" = "cold";
  await page.route("**/api/v1/models/runtime-environment", (route) => json(route, {
    platform: "linux", gpu: "fixture", runtimes: [{
      id: "llama.cpp-rocm", runtime: "llama.cpp", backend: "rocm", label: "llama.cpp / ROCm",
      available: true, installed: true, selected: true, running: true,
    }], amd_gpu: null,
    policy: {
      selected_runtime: "llama.cpp", selected_backend: "rocm", coexistence: "exclusive",
      idle_unload_enabled: false, idle_unload_minutes: 30, max_loaded_models: 1,
      supervision: "managed", gateway_only: true, warm_idle_sec: 600,
      min_uptime_sec: 120, drain_timeout_sec: 120, yield_max_level: 4,
      default_model_ref: "", assistant_name: "Assistant", chat: { timeout_seconds: 300 },
      deep_research: { evidence_context_chars: 90000, max_report_tokens: 32768, timeout_seconds: 1800 },
      amd_gpu: { enabled: false, profile: "quiet", power_limit_watts: 210,
        memory_clock_mode: "auto", memory_clock_level: 0, core_clock_mode: "auto", core_clock_level: 0 },
    },
  }));
  await page.route("**/api/v1/resources", (route) => json(route, {
    telemetry: {
      recent_events: [{ at: 1, event: "yield.suppressed", reason: "runtime_unknown" }],
      load_profiles: [{
        residency_key: "llama:model", measured_at: 2,
        cold_load_cost_sec: { p50: 82, p90: 83, count: 5 },
        warm_reload_cost_sec: { p50: 7.5, p90: 8, count: basis === "warm" ? 4 : 2 },
        yield_basis: basis,
        yield_threshold_sec: basis === "warm" ? 16 : 166,
      }],
    },
  }));

  await page.setViewportSize({ width: 320, height: 700 });
  await page.goto("/models");
  await page.getByRole("button", { name: "LLM 共通設定" }).click();
  let settings = page.getByRole("dialog", { name: "LLM 共通設定" });
  await expect(settings.getByText("cold 実測（暫定）")).toBeVisible();
  await expect(settings.getByText(/初回読み込みの実測値を暫定で使っています/)).toBeVisible();
  await expect(settings.getByRole("button", { name: "今すぐ退避" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
  await settings.getByRole("button", { name: "閉じる" }).click();

  basis = "warm";
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/models");
  await page.getByRole("button", { name: "LLM 共通設定" }).click();
  settings = page.getByRole("dialog", { name: "LLM 共通設定" });
  await expect(settings.getByText("warm 実測", { exact: true })).toBeVisible();
  await expect(settings.getByText(/8\.0 秒（warm p90、サンプル 4 件）/)).toBeVisible();
  await expect(settings.getByText(/推定実行時間が 16\.0 秒を超えるジョブ/)).toBeVisible();
  await expect(settings.getByText("処理時間が未申告", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
});
