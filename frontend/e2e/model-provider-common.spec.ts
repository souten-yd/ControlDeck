import { expect, test, type Page } from "@playwright/test";

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

test("uses the common provider routes for pull and model configuration", async ({ page }) => {
  const configRequests: string[] = [];
  let pullRequests = 0;
  const json = (route: Parameters<Parameters<Page["route"]>[1]>[0], body: unknown, status = 200) =>
    route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

  await page.route("**/api/v1/models/status", (route) => json(route, {
    available: true, version: "fixture", base_url: "http://127.0.0.1:11434",
  }));
  await page.route("**/api/v1/models/runtime-environment", (route) => json(route, {
    platform: "linux", gpu: "fixture", runtimes: [], amd_gpu: null,
    policy: {
      selected_runtime: "ollama", selected_backend: "", coexistence: "exclusive",
      idle_unload_enabled: false, idle_unload_minutes: 30, max_loaded_models: 1,
      default_model_ref: "", assistant_name: "Assistant",
      chat: { reasoning: "off", timeout_seconds: 120 },
      deep_research: { evidence_context_chars: 90000, max_report_tokens: 32768, timeout_seconds: 1800 },
      amd_gpu: { enabled: false, profile: "balanced", power_limit_watts: 0,
        memory_clock_mode: "auto", memory_clock_level: 0, core_clock_mode: "auto", core_clock_level: 0 },
    },
  }));
  await page.route("**/api/v1/models/running", (route) => json(route, []));
  await page.route("**/api/v1/models/fake-model/show", (route) => json(route, {
    parameters: "", details: {}, license: "", context_length: 4096, capabilities: [],
  }));
  await page.route("**/api/v1/models/providers/ollama/models/fake-model/config*", (route) => {
    configRequests.push(route.request().method());
    return json(route, route.request().method() === "GET" ? { num_ctx: 4096 } : { num_ctx: 4096 });
  });
  await page.route("**/api/v1/models/providers/ollama/pull-jobs", (route) => {
    pullRequests += 1;
    return json(route, { job_id: "fixture-job" }, 201);
  });
  await page.route("**/api/v1/jobs/fixture-job", (route) => json(route, {
    id: "fixture-job", kind: "model.pull", title: "モデル取得: fake-model", status: "succeeded",
    progress: { status: "完了", completed: 1, total: 1 }, error: "",
  }));
  await page.route("**/api/v1/jobs?kind=model.*", (route) => json(route, []));
  await page.route("**/api/v1/models", (route) => json(route, [{
    id: "fake-model", name: "fake-model", size: 1024, parameter_size: "1B",
    quantization: "Q4", family: "fixture", loaded: false, expires_at: null, vram: null,
  }]));

  await login(page);
  for (const viewport of [{ width: 320, height: 700 }, { width: 1280, height: 800 }]) {
    await page.setViewportSize(viewport);
    await page.goto("/models");
    await page.getByRole("button", { name: /fake-model/ }).click();
    await expect(page.getByText("このモデルの個別設定")).toBeVisible();
    await page.getByRole("button", { name: "保存のみ" }).click();
    await expect.poll(() => configRequests.filter((method) => method === "PUT").length).toBeGreaterThan(0);
    await page.getByRole("button", { name: "閉じる" }).click();

    await page.getByRole("button", { name: /モデル取得/ }).click();
    await page.getByPlaceholder(/llama3\.2/).fill("fake-model");
    await page.getByRole("button", { name: "取得", exact: true }).click();
    await expect.poll(() => pullRequests).toBeGreaterThan(0);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
    expect(overflow).toBeLessThanOrEqual(1);
    await page.getByRole("button", { name: "閉じる" }).click();
  }
  expect(configRequests).toContain("GET");
  expect(configRequests).toContain("PUT");
  expect(pullRequests).toBe(2);
});
