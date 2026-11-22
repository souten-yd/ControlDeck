// 目的: LLM 共通設定の「Add-on への GPU の譲り方」パネル（方針の説明と連動、載せ直し制御、
// 譲り合いの目安）がモバイル（320px・横溢れなし）と PC の両幅で出ることを確かめる。
// 根拠: 退避(yield)機構は 2026-09-04 の 4deb560「画像生成をRAMへ載せてLLMと共存させ、退避(yield)機構を廃止する」で
// 廃止され、この spec が確かめていた cold/warm 実測・今すぐ退避の UI は GPU Broker パネルごと画面から無くなった。
// 2026-11-22 の監査で本 spec が main で落ちていることを確認（HANDOFF-opencode-dev-environment 不具合と課題 14）。
// 保証していた「退避の根拠の説明」は対象の機能が製品から削除されたため立て直せず、後継パネルの説明に読み替えた。
import { expect, test, type Page, type Route } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

async function login(page: Page) {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  await page.goto("/login");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/);
}

const json = (route: Route, body: unknown) => route.fulfill({
  contentType: "application/json", body: JSON.stringify(body),
});

test("explains the add-on GPU policy and reload controls at mobile and desktop widths", async ({ page }) => {
  await login(page);
  await page.route("**/api/v1/models/runtime-environment", (route) => json(route, {
    platform: "linux", gpu: "fixture", runtimes: [{
      id: "llama.cpp-rocm", runtime: "llama.cpp", backend: "rocm", label: "llama.cpp / ROCm",
      available: true, installed: true, selected: true, running: true,
    }], amd_gpu: null,
    policy: {
      selected_runtime: "llama.cpp", selected_backend: "rocm", coexistence: "exclusive",
      idle_unload_enabled: false, idle_unload_minutes: 30, max_loaded_models: 1,
      assistant_name: "Assistant", chat: { timeout_seconds: 300 },
      deep_research: { evidence_context_chars: 90000, max_report_tokens: 32768, timeout_seconds: 1800 },
      addon_gpu: { mode: "auto", restore: true, restore_delay_sec: 15, max_lend_sec: 1800 },
      amd_gpu: { enabled: false, profile: "quiet", power_limit_watts: 210,
        memory_clock_mode: "auto", memory_clock_level: 0, core_clock_mode: "auto", core_clock_level: 0 },
    },
  }));

  for (const viewport of [{ width: 320, height: 700 }, { width: 1280, height: 800 }]) {
    await page.setViewportSize(viewport);
    await page.goto("/models");
    await page.getByRole("button", { name: "LLM 共通設定" }).click();
    const settings = page.getByRole("dialog", { name: "LLM 共通設定" });
    await expect(settings.getByRole("heading", { name: "Add-on への GPU の譲り方" })).toBeVisible();
    await expect(settings.getByRole("combobox", { name: "方針" })).toHaveValue("auto");
    await expect(settings.getByText(/LLM が推論していなければ降ろして/)).toBeVisible();
    await expect(settings.getByRole("checkbox", { name: /生成が終わったら LLM を載せ直す/ })).toBeChecked();
    await expect(settings.getByRole("combobox", { name: "載せ直すまでの間" })).toBeVisible();
    await expect(settings.getByRole("combobox", { name: "貸す時間の上限" })).toBeVisible();
    await expect(settings.getByText(/画像 1 枚（FLUX\.2 Klein）/)).toBeVisible();
    await expect(settings.getByText(/載せ直し: 約10秒/)).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);

    // 方針を「LLM 優先」にすると、載せ直し制御は出ておらず（降ろさない）旨の説明に切り替わる
    await settings.getByRole("combobox", { name: "方針" }).selectOption("llm_first");
    await expect(settings.getByText(/LLM は降ろしません/)).toBeVisible();
    await expect(settings.getByRole("checkbox", { name: /生成が終わったら LLM を載せ直す/ })).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
    await settings.getByRole("button", { name: "閉じる" }).click();
  }
});
