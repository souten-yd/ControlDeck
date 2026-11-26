/** Strata のモデル登録画面が backend の目録 API（GET /api/v1/models/strata/catalog）から
 * 全系列・全量子化を読み、1280px と 320px の両方で崩れずに選べることを確かめる。
 * 量子化の選択肢には id に加えてダウンロード目安・メモリ目安・「載る／ぎりぎり／載らない／不明」が出る。
 * 手作業が要る orca は選べない案内表示になり、Inkling では 0.2.22 の思考欄が残る。
 * 目録の取得失敗時は alert を出して保存ボタンを無効にする。
 *
 * 根拠: 2026-11-26 の指示書 M1（目録を backend の 1 か所に置き、画面は API から読む。Strata 0.2.22 の
 * 全系列・全量子化を登録画面に出す）。AGENTS.md「不具合・診断・テストの決まり」（目的と根拠を書いてから足す）
 * と UI ルール（320px・ダーク/ライト・等幅数字・日本語）。
 * API は page.route でモックし、外部（HF・npm・GitHub）へ取りに行かない。
 */
import { expect, test } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

type Quant = { id: string; label: string; download_gib: number | null; memory_gib: number | null;
  fit: "fits" | "tight" | "no" | "unknown"; uncensored: boolean; manual: boolean };
const q = (id: string, download_gib: number | null, memory_gib: number | null, fit: Quant["fit"]): Quant =>
  ({ id, label: id.toUpperCase().replace(/-/g, "_"), download_gib, memory_gib, fit, uncensored: false, manual: false });
const family = (id: string, name: string, engine: "qwen" | "other", quantizations: Quant[],
  extra: Partial<{ visible: boolean; manual: boolean; uncensored: boolean; note: string; thinking: boolean }> = {}) =>
  ({ id, name, short_name: id, engine, thinking: true, visible: true, manual: false, uncensored: false, note: "", quantizations, ...extra });

const CATALOG = {
  families: [
    family("qwen", "Qwen3.8-Flash-Next（GSQ-RCO・推奨）", "qwen",
      [q("q2_0", 66, 37.6, "fits"), q("iq2_xs", 70, 39.2, "fits"), q("iq3_xxs", 76, 47, "fits"), q("iq3_s", null, 54.8, "tight")]),
    family("coder", "Qwen3.8-Flash-Next Coder（コード向け）", "qwen", [q("iq1_m", null, 32, "fits")]),
    family("swift", "Swift 1.5（思考が短い fine-tune）", "qwen", [q("q2_0", 66, 37.6, "fits")]),
    family("unsloth", "Qwen3.8-Flash-Next（Unsloth）", "qwen",
      [q("ud-iq4_xs", 94, 72, "fits"), q("ud-q4_k_xl", 111, 88, "fits"), q("q8_0", 188, 136, "no")]),
    family("gyro", "Gyro（agentionai・小さいが遅い）", "qwen", [q("gyro-s", 58.5, 27.6, "fits")]),
    family("qwen36", "Qwen3.6-35B-A3B（Unsloth）", "other", [q("ud-q4_k_xl", 22.9, 32, "fits")]),
    family("hy4-flash", "Tencent Hy4-Flash", "other", [q("sq-64", 62, 64, "fits"), q("sq-96", 89.6, 96, "no")]),
    family("inkling-2-mini", "Inkling 2 Mini（Thinking Machines）", "other",
      ["sq-20", "sq-15", "sq-10", "ud-q8_k_xl", "ud-q4_k_xl", "ud-q4_k_m", "ud-iq4_xs",
        "ud-q3_k_xl", "ud-iq3_xxs", "ud-q2_k_xl", "ud-iq2_xxs", "ud-iq1_m", "ud-iq1_s"].map((id) => q(id, 19.3, 20, "fits"))),
    family("deepseek41", "DeepSeek V4.1 Flash", "other", [q("ista-das-coder", 70, 40, "unknown")]),
    // NVIDIA が要る系列。この PC では目録にはあるが画面には出さない。
    family("nvfp4", "NVFP4（NVIDIA だけ）", "qwen", [q("nvfp4", 60, 40, "fits")], { visible: false }),
    // 公式目録に無い。手作業の変換が要るので選べない案内行として出す。
    family("orca", "OrcaRouter Qwen3.8-Flash-Next-Uncensored IQ3_XXS", "other", [], { manual: true, uncensored: true,
      note: "無検閲・手作業が要る（案内）" }),
  ],
  hardware: { ram_total_gib: 92, vram_total_gib: 24 },
};

async function openStrataRegistration(page: import("@playwright/test").Page) {
  await page.route("**/api/v1/models/runtime-environment", async (route) => {
    const env = await (await route.fetch()).json();
    env.policy.selected_runtime = "strata";
    await route.fulfill({ json: env });
  });
  await page.route("**/api/v1/models/providers/*/models", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/models/strata/instances", (route) =>
    route.fulfill({ json: { runtime: { installed: true }, instances: [] } }));
  await page.goto("/models");
  await page.getByRole("button", { name: "モデル登録", exact: true }).click();
  await page.getByRole("button", { name: "Strata", exact: true }).click();
  return page.getByRole("dialog", { name: "モデル登録" });
}

for (const width of [1280, 320]) {
  test(`Strata catalog shows all families and quantizations at ${width}px`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials required");
    await page.setViewportSize({ width, height: 900 });
    await establishSession(page, context);
    await page.route("**/api/v1/models/strata/catalog", (route) => route.fulfill({ json: CATALOG }));
    const sheet = await openStrataRegistration(page);

    const families = sheet.getByLabel("Strataモデル系列");
    await expect(families.locator("option")).toHaveCount(10);
    expect(await families.locator("option").evaluateAll((els) => els.map((el) => (el as HTMLOptionElement).value)))
      .toEqual(["qwen", "coder", "swift", "unsloth", "gyro", "qwen36", "hy4-flash", "inkling-2-mini", "deepseek41", "orca"]);
    // Playwright の toBeDisabled は <option> の disabled を見ないので、要素の disabled を直接確かめる
    expect(await families.locator("option[value='orca']").evaluate((el) => (el as HTMLOptionElement).disabled)).toBe(true);
    await expect(families.locator("option[value='orca']")).toContainText("無検閲・手作業が要る（案内）");
    await expect(families.locator("option[value='nvfp4']")).toHaveCount(0);

    // 量子化の選択肢に id・ダウンロード目安・メモリ目安・載る印が出る（等幅数字）。
    const quants = sheet.getByLabel("Strataモデル量子化");
    await expect(quants).toHaveClass(/tabular-nums/);
    await expect(quants.locator("option[value='ud-iq4_xs']")).toContainText("94.0GB");
    await expect(quants.locator("option[value='ud-iq4_xs']")).toContainText("72.0GB");
    await expect(quants.locator("option[value='ud-iq4_xs']")).toContainText("載る");
    await families.selectOption("hy4-flash");
    await expect(quants.locator("option[value='sq-96']")).toContainText("載らない（SSDから読むので遅い）");
    await families.selectOption("coder");
    await expect(quants.locator("option[value='iq1_m']")).toContainText("不明");

    // 0.2.22 では Inkling も思考あり。思考欄が消えず、投機デコードは auto/off だけ。
    await families.selectOption("inkling-2-mini");
    await expect(sheet.getByText("要求が深さを指定しないときの既定")).toBeVisible();
    await expect(sheet.getByLabel("思考の深さ")).toBeVisible();
    await expect(sheet.getByLabel("投機デコード").locator("option")).toHaveCount(2);
    await expect(sheet.getByLabel("StrataKVのVRAM保持長", { exact: true })).toHaveCount(0);

    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `test-results/strata-catalog-${width}.png`, fullPage: true });
  });
}

test("Strata catalog failure shows an alert and disables save at 1280px", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials required");
  await page.setViewportSize({ width: 1280, height: 900 });
  await establishSession(page, context);
  await page.route("**/api/v1/models/strata/catalog", (route) => route.fulfill({ status: 500, json: { detail: "catalog failure" } }));
  const sheet = await openStrataRegistration(page);
  await expect(sheet.getByRole("alert")).toContainText("目録を取得できませんでした。再読み込みしてください");
  await expect(sheet.getByRole("button", { name: "登録", exact: true })).toBeDisabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: "test-results/strata-catalog-error-1280.png", fullPage: true });
});
