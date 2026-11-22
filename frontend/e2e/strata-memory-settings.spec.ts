import { expect, test } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

for (const width of [320, 1280]) {
  test(`Strata memory planning and settings at ${width}px`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials required");
    await page.setViewportSize({ width, height: 900 });
    await establishSession(page, context);
    let saved: any;
    await page.route("**/api/v1/models/runtime-environment", async route => {
      const env = await (await route.fetch()).json();
      env.policy.selected_runtime = "strata";
      await route.fulfill({ json: env });
    });
    await page.route("**/api/v1/models/providers/*/models", route => route.fulfill({ json:
      route.request().url().includes("/strata/") ? [{ id: "strata-memory", name: "strata-memory", size_bytes: 0, loaded: false, details: {} }] : [],
    }));
    const instance = { alias: "strata-memory", family: "unsloth", model: "ud-iq4_xs", context: "512k", port: 8096, parallel: 1, loaded: false };
    await page.route("**/api/v1/models/strata/instances", route => route.fulfill({ json: { runtime: { installed: true }, instances: [instance] } }));
    await page.route("**/api/v1/models/strata/instances/strata-memory", async route => {
      saved = route.request().postDataJSON();
      await route.fulfill({ json: saved });
    });
    await page.route("**/api/v1/models/strata/memory-estimate", async route => {
      const cfg = route.request().postDataJSON();
      await route.fulfill({ json: {
        supported: true, context_tokens: parseInt(cfg.context) * 1024, parallel: cfg.parallel, note: "概算テスト",
        model: { disk_bytes: 200 * 2 ** 30, expert_bytes: 55 * 2 ** 30 },
        hardware: { ram_total: 91 * 2 ** 30, ram_available: 87 * 2 ** 30, gpu: null },
        kv_ram_bytes: cfg.parallel * 3 * 2 ** 30, full_ram_context_tokens: 262144,
      } });
    });
    await page.route("**/api/v1/models/strata/instances/strata-memory/memory", route => route.fulfill({ json: {
      active: true, status: "STARTING", note: "起動ログの確保量", rows: [
        { category: "KVキャッシュ", location: "RAM", bytes: 23 * 2 ** 30, source: "起動ログ" },
        { category: "画像処理（VLM）", location: "VRAM", bytes: null, source: "内訳なし" },
      ],
    } }));
    if (width === 1280) Object.assign(instance, { engine_settings: { "kv-resident": "16384", STRATA_RAM_KEEP_GIB: "4" } });
    await page.goto("/models");
    await page.getByRole("button", { name: "strata-memoryの個別設定を開く" }).click();
    const sheet = page.getByRole("dialog", { name: "strata-memory · 個別設定", exact: true });
    await expect(sheet.getByLabel("StrataKVのVRAM保持長", { exact: true })).toHaveValue(width === 1280 ? "16384" : "");
    await expect(sheet.getByLabel("StrataRAM余裕", { exact: true })).toHaveValue(width === 1280 ? "4" : "");
    await sheet.getByLabel("Strata同時実行数", { exact: true }).fill("6");
    await sheet.getByLabel("Strataコンテキスト", { exact: true }).selectOption("256k");
    await sheet.getByLabel("StrataKVのVRAM保持長", { exact: true }).fill("32768");
    await sheet.getByLabel("StrataRAM余裕", { exact: true }).fill("8");
    await expect(sheet.getByText("256K × 6会話", { exact: true })).toBeVisible();
    await expect(sheet.getByText("画像処理（VLM）", { exact: true })).toBeVisible();
    await expect(sheet.getByRole("cell", { name: "不明", exact: true })).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await sheet.getByLabel("Strataメモリ目安").scrollIntoViewIfNeeded();
    await page.screenshot({ path: `/tmp/strata-memory-settings-${width}.png` });
    // 個別設定には別の欄の「保存」（無効）もある。押すのは下の全体の「保存」
    await sheet.getByRole("button", { name: "保存", exact: true }).last().click();
    await expect.poll(() => saved?.parallel).toBe(6);
    expect(saved.context).toBe("256k");
    expect(saved.kv_resident).toBe(32768);
    expect(saved.ram_keep_gib).toBe(8);
  });
}
