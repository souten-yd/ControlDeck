/** 目的: Strata のメモリ計画（KV の常駐トークン／RAM の残し量／同時実行数／コンテキスト）を
 *        個別設定から保存でき、メモリ目安と起動ログの内訳が見えること。320px / 1280px で横にはみ出さないこと。
 *  根拠: Strata の KV 確保量が分かりにくいという利用者の指摘と、Nexarch #12 の共通設定化。
 *  #24 更新: 共通 UI では詳細な Strata 専用欄はエキスパートの「従来の設定画面（すべての引数）」に
 *            移った（UI ルール「不要な表示は隠し、エキスパートで開く」）。その導線へ追随した。 */
import { expect, test } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

for (const width of [320, 1280]) {
  test(`Strata memory planning and settings at ${width}px`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials required");
    await page.addInitScript(() => {
      try { localStorage.setItem("control-deck:commander-expert", "1"); } catch { /* 無視 */ }
    });
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
    // Strata 専用の詳細欄は共通設定の下の <details> に畳まれている（エキスパート）。
    await sheet.locator("summary", { hasText: "従来の設定画面（すべての引数）" }).click();
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


// 目的: Strata の登録で Inkling 2 Mini を選べる。選ぶと量子化が API の目録（13 件）から入り、Qwen 用のメモリ設定は消え、
// 投機デコードは「自動（内蔵の DSpark）」か「使わない」だけになる。Strata 0.2.22 では Inkling も思考を持つので、
// 思考の欄は残り、保存に reasoning_effort / thinking_budget が載る。
// 根拠: 2026-11-23 利用者「Strata や Radiance で inkling がサポートされていれば候補として追加できるように」。
// 2026-11-26 の指示書 M1（目録を backend の 1 か所に置き、画面は API から読む。0.2.22 で Inkling も思考あり）。
// AGENTS.md「不具合・診断・テストの決まり」（目的と根拠を書いてから足す）。
test("Strata registration offers Inkling 2 Mini with thinking and without Qwen-only memory settings", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials required");
  await page.addInitScript(() => {
    try { localStorage.setItem("control-deck:commander-expert", "1"); } catch { /* 無視 */ }
  });
  await page.setViewportSize({ width: 390, height: 900 });
  await establishSession(page, context);
  const quantIds = ["sq-20", "sq-15", "sq-10", "ud-q8_k_xl", "ud-q4_k_xl", "ud-q4_k_m", "ud-iq4_xs",
    "ud-q3_k_xl", "ud-iq3_xxs", "ud-q2_k_xl", "ud-iq2_xxs", "ud-iq1_m", "ud-iq1_s"];
  await page.route("**/api/v1/models/strata/catalog", route => route.fulfill({ json: {
    families: [{ id: "inkling-2-mini", name: "Inkling 2 Mini（Thinking Machines）", short_name: "Inkling",
      engine: "other", thinking: true, visible: true, manual: false, uncensored: false, note: "",
      quantizations: quantIds.map((id) => ({ id, label: id, download_gib: 10, memory_gib: 20, fit: "fits", uncensored: false, manual: false })) }],
    hardware: { ram_total_gib: 92, vram_total_gib: 24 },
  } }));
  let saved: any;
  await page.route("**/api/v1/models/runtime-environment", async route => {
    const env = await (await route.fetch()).json();
    env.policy.selected_runtime = "strata";
    await route.fulfill({ json: env });
  });
  await page.route("**/api/v1/models/providers/*/models", route => route.fulfill({ json:
    route.request().url().includes("/strata/") ? [{ id: "ink", name: "ink", size_bytes: 0, loaded: false, details: {} }] : [],
  }));
  const instance = { alias: "ink", family: "unsloth", model: "ud-iq4_xs", context: "128k", port: 8110, parallel: 1, loaded: false,
    reasoning_effort: "low" };
  await page.route("**/api/v1/models/strata/instances", route => route.fulfill({ json: { runtime: { installed: true }, instances: [instance] } }));
  await page.route("**/api/v1/models/strata/instances/ink", async route => { saved = route.request().postDataJSON(); await route.fulfill({ json: saved }); });
  await page.route("**/api/v1/models/strata/memory-estimate", route => route.fulfill({ json: { supported: false, note: "このモデル系列のKV係数は未対応です",
    context_tokens: 131072, parallel: 1, model: {}, hardware: {} } }));
  await page.route("**/api/v1/models/strata/instances/ink/memory", route => route.fulfill({ json: { active: false, rows: [] } }));
  await page.goto("/models");
  await page.getByRole("button", { name: "inkの個別設定を開く" }).click();
  const sheet = page.getByRole("dialog", { name: "ink · 個別設定", exact: true });
  await sheet.locator("summary", { hasText: "従来の設定画面（すべての引数）" }).click();
  await sheet.getByLabel("Strataモデル系列").selectOption("inkling-2-mini");
  await expect(sheet.getByLabel("Strataモデル量子化")).toHaveValue("sq-20");
  const options = sheet.getByLabel("Strataモデル量子化").locator("option");
  await expect(options).toHaveCount(13);
  expect(await options.evaluateAll((els) => els.map((el) => (el as HTMLOptionElement).value))).toEqual(quantIds);
  await expect(sheet.getByLabel("StrataKVのVRAM保持長", { exact: true })).toHaveCount(0);
  await expect(sheet.getByText("要求が深さを指定しないときの既定")).toBeVisible();
  await expect(sheet.getByLabel("投機デコード").locator("option")).toHaveCount(2);
  await page.screenshot({ path: "test-results/strata-inkling-390.png", fullPage: true });
  await sheet.getByRole("button", { name: "保存", exact: true }).last().click();
  await expect.poll(() => saved?.family).toBe("inkling-2-mini");
  expect(saved.model).toBe("sq-20");
  expect(saved.reasoning_effort).toBe("low");
  expect(saved.thinking_budget).toBe(0);
  expect(saved.kv_resident).toBeNull();
});
