/** 目的: Strata のメモリ計画（KV の常駐トークン／RAM の残し量／同時実行数／コンテキスト）を
 *        共通 RuntimeSettings から保存でき、エキスパートの欄として現れること。
 *        320px / 1280px で横にはみ出さないこと。Inkling では Qwen 専用のメモリ欄を出さず、
 *        思考と先読みの選択肢だけを保存すること。
 *  根拠: Strata の KV 確保量が分かりにくいという利用者の指摘と、Nexarch #12 の共通設定化。
 *        レビュー #25 / 2026-11-28 の要修正（旧 「従来の設定画面（すべての引数）」 は削除済み）。
 *        旧個別パネルの <details><summary> と専用ラベル（StrataKVのVRAM保持長など）を
 *        期待していたため、capability に基づく共通 UI（②コンテキスト / ③同時に処理する数 /
 *        ④速さの調整 / ⑥エキスパート）へ追随した。API は page.route で模擬し、実取得はしない。 */
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
    const instance = { alias: "strata-memory", family: "unsloth", model: "ud-iq4_xs", context: "512k", port: 8096,
      parallel: 1, loaded: false,
      ...(width === 1280 ? { kv_resident: 16384, ram_keep_gib: 4 } : {}) };
    await page.route("**/api/v1/models/strata/instances", route => route.fulfill({ json: { runtime: { installed: true }, instances: [instance] } }));
    await page.route("**/api/v1/models/strata/instances/strata-memory", async route => {
      saved = route.request().postDataJSON();
      await route.fulfill({ json: saved });
    });
    await page.goto("/models");
    await page.getByRole("button", { name: "strata-memoryの個別設定を開く" }).click();
    const sheet = page.getByRole("dialog", { name: "strata-memory · 個別設定", exact: true });
    const settings = sheet.locator('[data-runtime-settings="strata"]');
    // 共通設定の ①〜⑥ が揃い、エキスパート（⑥）は入のときだけ出る。
    await expect(settings.locator(":scope > section > h3")).toHaveText([
      "モデル", "コンテキストの長さ", "同時に処理する数", "速さの調整", "起動",
    ]);
    await expect(settings.locator(":scope > details > summary", { hasText: "エキスパート" })).toBeVisible();
    // メモリ計画の細かい数値は共通 UI ではエキスパートの数値入力（旧専用パネルの欄ではない）。
    const kv = sheet.getByLabel("KV の常駐トークン（数値入力）", { exact: true });
    const ram = sheet.getByLabel("RAM に残す量（数値入力）", { exact: true });
    await expect(kv).toHaveValue(width === 1280 ? "16384" : "");
    await expect(ram).toHaveValue(width === 1280 ? "4" : "");
    await expect(sheet.getByLabel("コンテキストの長さ", { exact: true })).toHaveValue("512k");
    await sheet.getByLabel("同時に処理する数（数値入力）", { exact: true }).fill("6");
    await sheet.getByLabel("コンテキストの長さ", { exact: true }).selectOption("256k");
    await kv.fill("32768");
    await ram.fill("8");
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await sheet.getByLabel("KV の常駐トークン（数値入力）", { exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: `/tmp/strata-memory-settings-${width}.png` });
    await sheet.getByTestId("runtime-settings-save").click();
    await expect.poll(() => saved?.parallel).toBe(6);
    expect(saved.context).toBe("256k");
    expect(saved.kv_resident).toBe(32768);
    expect(saved.ram_keep_gib).toBe(8);
  });
}


// 目的: Strata の個別設定で Inkling 2 Mini の量子化を目録どおりに選べ、思考の設定だけが保存され、
//       Qwen 系のメモリ設定（kv_resident / ram_keep_gib）が送られないこと。
// 根拠: 2026-11-23 利用者「Strata や Radiance で inkling がサポートされていれば候補として追加できるように」。
//       2026-11-26 の指示書 M1（目録を backend の 1 か所に置き、画面は capability から読む。0.2.22 で Inkling も思考あり）。
//       レビュー #25 / 2026-11-28: 旧「従来の設定画面（すべての引数）」は削除済みなので共通 RuntimeSettings へ追随した。
test("Strata settings offer Inkling 2 Mini with thinking and without Qwen-only memory settings", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials required");
  await page.addInitScript(() => {
    try { localStorage.setItem("control-deck:commander-expert", "1"); } catch { /* 無視 */ }
  });
  await page.setViewportSize({ width: 390, height: 900 });
  await establishSession(page, context);
  const quantIds = ["sq-20", "sq-15", "sq-10", "ud-q8_k_xl", "ud-q4_k_xl", "ud-q4_k_m", "ud-iq4_xs",
    "ud-q3_k_xl", "ud-iq3_xxs", "ud-q2_k_xl", "ud-iq2_xxs", "ud-iq1_m", "ud-iq1_s"];
  const ids = ["alias", "family", "model", "context", "parallel", "kv", "drafting", "vision",
    "reasoning_effort", "thinking_budget", "port", "idle_exclude", "auto_start"];
  await page.route("**/api/v1/models/settings/capabilities", route => route.fulfill({ json: { runtimes: {
    strata: {
      sections: [
        { id: "model", label: "モデル", fields: [
          { id: "alias", label: "モデル名", type: "text", default: "", expert: false },
          { id: "family", label: "系列", type: "select", default: "inkling-2-mini", choices: ["inkling-2-mini"], expert: false },
          { id: "model", label: "量子化", type: "select", default: quantIds[0], choices: quantIds, expert: false },
        ] },
        { id: "context", label: "コンテキストの長さ", fields: [
          { id: "context", label: "コンテキストの長さ", type: "select", default: "128k", choices: ["128k", "256k"], expert: false },
        ] },
        { id: "parallel", label: "同時に処理する数", fields: [
          { id: "parallel", label: "同時に処理する数", type: "int", default: 1, min: 1, max: 8, expert: false },
        ] },
        { id: "speed", label: "速さの調整", fields: [
          { id: "kv", label: "KV キャッシュの形式", type: "select", default: "int8", choices: ["int8", "fp16"], expert: false },
          { id: "drafting", label: "先読み", type: "select", default: "auto", choices: ["auto", "off"], expert: false },
          { id: "vision", label: "画像の読み取り", type: "select", default: "on", choices: ["on", "off"], expert: false },
          { id: "reasoning_effort", label: "思考の既定", type: "select", default: "", choices: ["", "low", "high"], expert: false },
          { id: "thinking_budget", label: "思考の上限", type: "int", default: 0, min: 0, max: 32768, expert: false },
        ] },
        { id: "launch", label: "起動", fields: [
          { id: "idle_exclude", label: "アイドルでも止めない", type: "bool", default: false, expert: false },
          { id: "auto_start", label: "PC起動時に自動で起動", type: "bool", default: false, expert: false },
        ] },
        { id: "expert", label: "エキスパート", fields: [
          { id: "port", label: "ポート", type: "int", default: 8095, min: 8081, max: 65535, expert: true },
        ] },
      ],
      always_on: [], save_keys: ids, endpoints: { instances: "/api/v1/models/strata/instances" },
    },
  } } }));
  let saved: any;
  await page.route("**/api/v1/models/runtime-environment", async route => {
    const env = await (await route.fetch()).json();
    env.policy.selected_runtime = "strata";
    await route.fulfill({ json: env });
  });
  await page.route("**/api/v1/models/providers/*/models", route => route.fulfill({ json:
    route.request().url().includes("/strata/") ? [{ id: "ink", name: "ink", size_bytes: 0, loaded: false, details: {} }] : [],
  }));
  const instance = { alias: "ink", family: "inkling-2-mini", model: "sq-20", context: "128k", port: 8110, parallel: 1,
    loaded: false, reasoning_effort: "low" };
  await page.route("**/api/v1/models/strata/instances", route => route.fulfill({ json: { runtime: { installed: true }, instances: [instance] } }));
  await page.route("**/api/v1/models/strata/instances/ink", async route => { saved = route.request().postDataJSON(); await route.fulfill({ json: saved }); });
  await page.goto("/models");
  await page.getByRole("button", { name: "inkの個別設定を開く" }).click();
  const sheet = page.getByRole("dialog", { name: "ink · 個別設定", exact: true });
  await sheet.getByLabel("系列", { exact: true }).selectOption("inkling-2-mini");
  await expect(sheet.getByLabel("量子化", { exact: true })).toHaveValue("sq-20");
  const options = sheet.getByLabel("量子化", { exact: true }).locator("option");
  await expect(options).toHaveCount(13);
  expect(await options.evaluateAll((els) => els.map((el) => (el as HTMLOptionElement).value))).toEqual(quantIds);
  await expect(sheet.getByLabel("KV の常駐トークン（数値入力）", { exact: true })).toHaveCount(0);
  await expect(sheet.getByLabel("RAM に残す量（数値入力）", { exact: true })).toHaveCount(0);
  await expect(sheet.getByLabel("思考の既定", { exact: true })).toBeVisible();
  await expect(sheet.getByLabel("先読み", { exact: true }).locator("option")).toHaveCount(2);
  await page.screenshot({ path: "test-results/strata-inkling-390.png", fullPage: true });
  await sheet.getByTestId("runtime-settings-save").click();
  await expect.poll(() => saved?.family).toBe("inkling-2-mini");
  expect(saved.model).toBe("sq-20");
  expect(saved.reasoning_effort).toBe("low");
  expect(saved.thinking_budget).toBe(0);
  expect(saved).not.toHaveProperty("kv_resident");
  expect(saved).not.toHaveProperty("ram_keep_gib");
});
