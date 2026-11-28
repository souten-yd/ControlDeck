/** 目的: Models の「モデル登録」の目録を、本番と同じ目録のデータ（fixtures/model-catalog-20261128.json。
 *        作り直した backend の api_payload を本番の PC の RAM・VRAM で組み立てたもの）で確かめる。
 *        1 モデル 1 枚のカード・Strata の重複が無い・開くと量子化を選べて主のボタンが 1 つ・開発者のメモや
 *        内部の項目名が出ない・320px で崩れない、を見る。
 *  根拠: 2026-11-28 利用者（スマホの写真つき）「途中から精度が落ちてるし、なんだか目録が変。追加する機能の
 *        ところ全てやり直して」。前回は模擬の 1 行だけで画面を確かめていて、本番の目録の崩れを見逃した。
 */
import { readFileSync } from "node:fs";
import { expect, test, type BrowserContext, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

const catalog = JSON.parse(readFileSync(new URL("./fixtures/model-catalog-20261128.json", import.meta.url), "utf8"));
type Variant = { id: string; acquire: string; size_gib: number | null; fit: string; supported: boolean; reason: string };
type Model = { id: string; name: string; variants: Variant[] };
const block = (id: string) => catalog.runtimes.find((item: { id: string }) => item.id === id) as { models: Model[] };

// 利用者に出してはいけない言葉（開発者向けのメモ・内部の項目名）
const FORBIDDEN = ["引数", "RADIANCE_", "alias", "model_path", "setup_id", "probe", "/api/v1"];

async function mock(page: Page, calls: string[]) {
  const installed = { runtime: { installed: true, version: "fixture", warning: "" }, instances: [] };
  const ids = ["llama.cpp", "strata", "radiance", "sly-radiance", "maya-s"];
  await page.route("**/api/v1/models/runtime-environment", (r) => r.fulfill({ json: {
    platform: "linux", gpu: "e2e",
    runtimes: ids.map((id) => ({ id, runtime: id, label: id, installed: true, available: true })),
    policy: { selected_runtime: "llama.cpp", selected_backend: "", coexistence: "exclusive",
      idle_unload_enabled: false, idle_unload_minutes: 30, max_loaded_models: 1, assistant_name: "Assistant" },
  } }));
  await page.route("**/api/v1/models/providers/*/models", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/v1/models/llama/status", (r) => r.fulfill({ json: { installed: true, instances: [] } }));
  await page.route("**/api/v1/models/strata/instances", (r) => r.fulfill({ json: installed }));
  await page.route("**/api/v1/models/radiance/instances", (r) => r.fulfill({ json: installed }));
  await page.route("**/api/v1/models/sly-radiance/instances", (r) => r.fulfill({ json: installed }));
  await page.route("**/api/v1/features/maya-s/model", (r) => r.fulfill({ json: { installed: true } }));
  await page.route("**/api/v1/features/maya-s/catalog*", (r) => r.fulfill({ json: { folder: "", memory: { ram_gb: 92, vram_gb: 32 }, quants: [] } }));
  await page.route("**/api/v1/models/instances", (r) => r.fulfill({ json: { instances: [] } }));
  await page.route("**/api/v1/models/catalog", (r) => r.fulfill({ json: catalog }));
  await page.route("**/api/v1/jobs*", (r) => r.fulfill({ json: [] }));
  // カードを開いたときの大きさ（HF の代わり）: 量子化の順に 9・18・27… GiB
  await page.route("**/api/v1/models/catalog/*/details*", (r) => {
    const url = new URL(r.request().url());
    const runtime = url.pathname.split("/").at(-2) ?? "";
    const model = block(runtime).models.find((item) => item.id === url.searchParams.get("model"));
    calls.push(`details:${runtime}:${model?.name}`);
    return r.fulfill({ json: {
      model_id: model?.id, hardware: catalog.hardware, libraries: [],
      variants: (model?.variants ?? []).map((v, i) => {
        const size = v.acquire === "hf" ? 9 * (i + 1) : v.size_gib;
        const fit = size == null ? v.fit : size * 1.15 <= 31.9 ? "fits" : size * 1.15 <= 123.7 ? "tight" : "no";
        return { id: v.id, size_gib: size, fit, supported: v.supported, reason: v.reason, state: "available", local_path: "" };
      }),
    } });
  });
  await page.route("**/api/v1/models/acquire-jobs", (r) => {
    calls.push(`acquire:${r.request().postDataJSON().entry_id}`);
    return r.fulfill({ status: 201, json: { job_id: "job-e2e", repo: "x/y" } });
  });
  await page.route("**/api/v1/jobs/job-e2e", (r) => r.fulfill({ json: {
    id: "job-e2e", kind: "model.hf_download", title: "取得", status: "running", progress: { completed: 3e9, total: 1e10, status: "ダウンロード中" },
    can_resume: false, result: null, error: "",
  } }));
  await page.route("**/api/v1/models/settings/capabilities", (r) => r.fulfill({ json: { runtimes: Object.fromEntries(
    ["llama", "strata", "radiance", "sly-radiance", "maya"].map((id) => [id, { sections: [
      { id: "model", label: "モデル", fields: [{ id: "alias", label: "モデル名", type: "text", default: "", expert: false }] },
    ], always_on: [], endpoints: {} }])) } }));
}

async function openSheet(page: Page, context: BrowserContext, calls: string[]) {
  test.skip(!hasSession(), "E2E 資格情報がありません");
  await establishSession(page, context);
  await mock(page, calls);
  await page.goto("/models");
  await page.getByRole("button", { name: "モデル登録", exact: true }).click();
  const sheet = page.getByRole("dialog", { name: "モデル登録" });
  await expect(sheet).toBeVisible();
  return sheet;
}

async function noForbidden(sheet: ReturnType<Page["getByRole"]>) {
  const text = await sheet.innerText();
  for (const word of FORBIDDEN) expect(text, `画面に「${word}」が出ている`).not.toContain(word);
}

for (const [width, scheme] of [[320, "dark"], [1280, "light"], [320, "light"], [1280, "dark"]] as const) {
  test(`Strata: 重複の無いモデルのカード → 開いて量子化を選ぶ → 登録（${width}px ${scheme}）`, async ({ page, context }) => {
    await page.emulateMedia({ colorScheme: scheme });
    await page.setViewportSize({ width, height: 900 });
    const calls: string[] = [];
    const sheet = await openSheet(page, context, calls);
    await sheet.getByRole("button", { name: "Strata", exact: true }).click();
    const list = sheet.locator('[data-runtime-catalog="strata"]');
    await expect(list.locator("[data-catalog-model]").first()).toBeVisible();
    // 載らないものは既定で隠す。名前の重複が無い（Strata の二重を消した）
    const names = await list.locator("[data-catalog-model] > button .font-semibold").allInnerTexts();
    expect(new Set(names).size).toBe(names.length);
    expect(names.filter((n) => n === "Qwen3.8-Flash-Next")).toHaveLength(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
    await noForbidden(sheet);
    await page.screenshot({ path: `test-results/model-add-strata-list-${width}-${scheme}.png` });

    const card = list.locator("[data-catalog-model]").filter({ hasText: "Qwen3.8-Flash-Next" }).first();
    await card.locator("> button").click();
    await expect(card.getByRole("radio")).toHaveCount(4);
    await card.getByRole("radio").filter({ hasText: "IQ2_XS" }).click();
    await expect(card.getByTestId("catalog-primary")).toHaveText("登録する（初回の起動で取得）");
    await noForbidden(sheet);
    await page.screenshot({ path: `test-results/model-add-strata-open-${width}-${scheme}.png` });
    await card.getByTestId("catalog-primary").click();
    await expect(sheet.locator('[data-runtime-settings="strata"]')).toBeVisible();
    expect(calls.some((c) => c.startsWith("details:strata:Qwen3.8-Flash-Next"))).toBe(false);   // Strata 自身が取る行は HF を読まない
  });

  test(`llama.cpp: 開いたときだけ大きさを読み、取得のボタンに大きさが出る（${width}px ${scheme}）`, async ({ page, context }) => {
    await page.emulateMedia({ colorScheme: scheme });
    await page.setViewportSize({ width, height: 900 });
    const calls: string[] = [];
    const sheet = await openSheet(page, context, calls);
    const list = sheet.locator('[data-runtime-catalog="llama"]');
    await expect(list.locator("[data-catalog-model]").first()).toBeVisible();
    expect(calls.filter((c) => c.startsWith("details:"))).toHaveLength(0);     // 開くまで読まない
    const card = list.locator("[data-catalog-model]").filter({ hasText: "Qwen3.8-27B" }).first();
    await card.locator("> button").click();
    await expect(card.getByRole("radio").first()).toContainText("GiB");
    await card.getByRole("radio").filter({ hasText: "UD-Q4_K_XL" }).click();
    await expect(card.getByTestId("catalog-primary")).toHaveText(/^取得する（約 \d+(\.\d)? GiB）$/);
    await noForbidden(sheet);
    await page.screenshot({ path: `test-results/model-add-llama-open-${width}-${scheme}.png` });
    await card.getByTestId("catalog-primary").click();
    await expect(card.locator("[data-acquire-job]")).toBeVisible();
    expect(calls).toContain("acquire:unsloth-qwen38-27b-ud-q4_k_xl");
    await page.screenshot({ path: `test-results/model-add-llama-job-${width}-${scheme}.png` });
  });
}

test("全ランタイム: 開発者のメモ・内部の項目名が通常の画面に出ず、エキスパートの欄も出ない", async ({ page, context }) => {
  await page.setViewportSize({ width: 390, height: 900 });
  const calls: string[] = [];
  const sheet = await openSheet(page, context, calls);
  for (const name of ["llama.cpp", "Strata", "Maya", "Radiance"]) {
    await sheet.getByRole("button", { name, exact: true }).click();
    // Maya の目録は畳んである（Maya 自身の量子化の一覧が主）。開いてから見る
    if (name === "Maya") await sheet.locator("summary").filter({ hasText: /^目録から取得/ }).click();
    const cards = sheet.locator("[data-catalog-model]");
    await expect(cards.first()).toBeVisible();
    const count = await cards.count();
    for (let i = 0; i < count; i += 1) {
      await cards.nth(i).locator("> button").click();
    }
    await expect(sheet.locator("[data-catalog-expert]")).toHaveCount(0);
    await noForbidden(sheet);
    await cards.first().scrollIntoViewIfNeeded();
    await page.screenshot({ path: `test-results/model-add-all-open-${name.replace(".", "")}-390.png` });
  }
});
