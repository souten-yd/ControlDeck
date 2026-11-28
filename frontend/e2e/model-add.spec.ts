/** 目的: Models の「モデル登録」の目録（系列 → 検閲あり／無検閲 → 量子化。容量・メモリ・載るか）を、本番と同じ
 *        目録のデータ（fixtures/model-catalog-20261128.json。backend の api_payload を、本番から読んだ Strata 0.2.23 の
 *        目録・Maya 1.1.84 の probe と本番の PC の RAM・VRAM で組み立てたもの）で確かめる。
 *  根拠: 2026-11-28 利用者「目録はまず大きな種類（Qwen・Hy4・Inkling など）を選ばせ、検閲・無検閲、量子化を選べる
 *        ように。載るかどうかと容量を調査して目録を作って。llama.cpp は目録をやめて従来のファイル選択に戻す」。
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
  // カードを開いたときの詳細（HF の代わり）: 目録の値をそのまま返す（作り話の大きさで写真を撮らない）
  await page.route("**/api/v1/models/catalog/*/details*", (r) => {
    const url = new URL(r.request().url());
    const runtime = url.pathname.split("/").at(-2) ?? "";
    const model = block(runtime).models.find((item) => item.id === url.searchParams.get("model"));
    calls.push(`details:${runtime}:${model?.name}`);
    return r.fulfill({ json: {
      model_id: model?.id, hardware: catalog.hardware, libraries: [],
      variants: (model?.variants ?? []).map((v) => (
        { id: v.id, size_gib: v.size_gib, fit: v.fit, supported: v.supported, reason: v.reason, state: "available", local_path: "" })),
    } });
  });
  await page.route("**/api/v1/features/maya-s/models/download-jobs", (r) => {
    calls.push(`maya:${JSON.stringify(r.request().postDataJSON())}`);
    return r.fulfill({ status: 201, json: { job_id: "job-e2e", alias: "maya-x" } });
  });
  await page.route("**/api/v1/commander/workdir/roots", (r) => r.fulfill({ json: ["/data1tb", "/data2tb"] }));
  await page.route("**/api/v1/files/list*", (r) => {
    const path = new URL(r.request().url()).searchParams.get("path") ?? "/data2tb";
    return r.fulfill({ json: { path, entries: [{ name: "Qwen3.8-27B-UD-Q4_K_XL.gguf", path: `${path}/Qwen3.8-27B-UD-Q4_K_XL.gguf`, is_dir: false, hidden: false }] } });
  });
  await page.route("**/api/v1/models/register/inspect*", (r) => r.fulfill({ json: {
    path: new URL(r.request().url()).searchParams.get("path"), kind: "file", format: "gguf", alias_suggestion: "qwen3.8-27b",
    size_bytes: 17_600_000_000, metadata: { quantization: "UD-Q4_K_XL" },
    runtimes: ["llama", "strata"].map((runtime) => ({ runtime, eligible: true, reason: "", register_endpoint: `/api/v1/models/${runtime}/instances` })) } }));
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

const card = (sheet: ReturnType<Page["getByRole"]>, runtime: string, name: string) =>
  sheet.locator(`[data-runtime-catalog="${runtime}"] [data-catalog-model]`).filter({
    has: sheet.page().locator("> button .font-semibold", { hasText: new RegExp(`^${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`) }),
  });

for (const [width, scheme] of [[320, "dark"], [1280, "light"], [320, "light"], [1280, "dark"]] as const) {
  test(`Strata: 系列 → 通常／無検閲 → 量子化（${width}px ${scheme}）`, async ({ page, context }) => {
    await page.emulateMedia({ colorScheme: scheme });
    await page.setViewportSize({ width, height: 900 });
    const calls: string[] = [];
    const sheet = await openSheet(page, context, calls);
    await sheet.getByRole("button", { name: "Strata", exact: true }).click();
    const list = sheet.locator('[data-runtime-catalog="strata"]');
    await expect(list.locator("[data-catalog-model]").first()).toBeVisible();
    for (const name of ["Qwen3.8-Flash-Next", "Tencent Hy4-Flash", "Inkling-2-Mini", "Ornith-1.5-35B-A3B"]) {
      await expect(card(sheet, "strata", name)).toHaveCount(1);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
    await noForbidden(sheet);
    await page.screenshot({ path: `test-results/model-add-strata-list-${width}-${scheme}.png` });

    const flash = card(sheet, "strata", "Qwen3.8-Flash-Next");
    await flash.locator("> button").click();
    await expect(flash.getByRole("tab", { name: "通常" })).toHaveAttribute("aria-selected", "true");
    await expect(flash.getByRole("radio")).toHaveCount(4);
    await expect(flash.getByRole("radio").first()).toContainText("容量 66 GB");
    await expect(flash.getByRole("radio").first()).toContainText("メモリ 35 GB");
    await page.screenshot({ path: `test-results/model-add-strata-open-${width}-${scheme}.png` });
    await flash.getByRole("tab", { name: "無検閲" }).click();
    await expect(flash.getByRole("radio")).toHaveCount(4);
    await expect(flash.getByRole("radio").first()).toContainText("SC117 の版");
    await expect(flash.getByTestId("catalog-primary")).toHaveText(/^取得する（約 \d+ GB）$/);
    await page.screenshot({ path: `test-results/model-add-strata-uncensored-${width}-${scheme}.png` });
    await flash.getByRole("tab", { name: "通常" }).click();
    await flash.getByRole("radio").filter({ hasText: "IQ2_XS" }).click();
    await expect(flash.getByTestId("catalog-primary")).toHaveText("登録する（初回の起動で取得）");
    await flash.getByTestId("catalog-primary").click();
    await expect(sheet.locator('[data-runtime-settings="strata"]')).toBeVisible();
  });

  test(`Maya: GLM・DeepSeek・MiniMax・Hy4 を取得して登録（${width}px ${scheme}）`, async ({ page, context }) => {
    await page.emulateMedia({ colorScheme: scheme });
    await page.setViewportSize({ width, height: 900 });
    const calls: string[] = [];
    const sheet = await openSheet(page, context, calls);
    await sheet.getByRole("button", { name: "Maya", exact: true }).click();
    for (const name of ["GLM-5.3-Flash", "DeepSeek-V4.1-Flash", "MiniMax-M3.1-Flash", "Hy4-Flash"]) {
      await expect(card(sheet, "maya", name)).toHaveCount(1);
    }
    const glm = card(sheet, "maya", "GLM-5.3-Flash");
    await glm.locator("> button").click();
    await glm.getByRole("radio").filter({ hasText: "Maya-XS" }).click();
    await expect(glm.getByTestId("catalog-primary")).toHaveText("取得して登録（約 73 GB）");
    await noForbidden(sheet);
    await page.screenshot({ path: `test-results/model-add-maya-open-${width}-${scheme}.png` });
    await glm.getByTestId("catalog-primary").click();
    await expect.poll(() => calls.find((c) => c.startsWith("maya:"))).toContain('"setup_id":"glm-maya-xs"');
    expect(calls.find((c) => c.startsWith("maya:"))).toContain('"folder":"/data2tb/LLM/GLM-5.3-Flash-Maya-GGUF","create_folder":true');   // まだ無いフォルダは作る（2026-11-28）
  });
}

test("llama.cpp は目録を出さず、手元のファイルを選んで登録する", async ({ page, context }) => {
  await page.setViewportSize({ width: 390, height: 900 });
  const sheet = await openSheet(page, context, []);
  await expect(sheet.locator('[data-runtime-register-file="llama"]')).toBeVisible();
  await expect(sheet.locator('[data-runtime-catalog="llama"]')).toHaveCount(0);
  await expect(sheet.getByRole("button", { name: "目録から取得" })).toHaveCount(0);
  // ファイルを選ぶと、容量と「この PC に載るか」が出る（2026-11-28 利用者「容量と乗るかを見やすく」）
  await sheet.getByRole("button", { name: "モデルのファイルを選ぶ" }).click();
  const picker = page.getByRole("dialog", { name: /モデルのファイルを選択/ });
  await picker.getByRole("button", { name: "/data2tb", exact: true }).click();
  await picker.getByRole("button", { name: "Qwen3.8-27B-UD-Q4_K_XL.gguf" }).click();
  await expect(sheet.locator('[data-file-fit="fits"]')).toContainText("この PC に載ります");
  await expect(sheet.getByText(/gguf · 17\.6 GB/)).toBeVisible();
  await page.screenshot({ path: "test-results/model-add-llama-file-390.png" });
});

test("Radiance: 作者の文章の LLM を系列ごとに・無検閲の切り替え・開発者のメモが出ない", async ({ page, context }) => {
  await page.setViewportSize({ width: 390, height: 900 });
  const sheet = await openSheet(page, context, []);
  await sheet.getByRole("button", { name: "Radiance", exact: true }).click();
  await sheet.getByRole("switch", { name: "Sly-Radiance で登録" }).click();      // 元の Radiance の目録
  const q27 = card(sheet, "radiance", "Qwen3.8-27B");
  await q27.locator("> button").click();
  await expect(q27.getByRole("radio")).toHaveCount(18);
  await q27.getByRole("tab", { name: "無検閲" }).click();
  await expect(q27.getByRole("radio")).toHaveCount(2);
  await noForbidden(sheet);
  await page.screenshot({ path: "test-results/model-add-radiance-390.png" });
});

// 目的: Models のメイン画面に「ダウンロード中」をまとめて出し、今の量・全体・何個目のファイルかが分かること。
// 根拠: 2026-11-28 利用者「ダウンロードが進んでいるのか分からない。メインのところにダウンロード中と、速度・容量の最大と
//       今・何分の何かが分かるように」。
test("Models のメイン画面: ダウンロード中の取得をまとめて出す（320px）", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E 資格情報がありません");
  await page.setViewportSize({ width: 320, height: 800 });
  await establishSession(page, context);
  await mock(page, []);
  const job = {
    id: "dl-1", kind: "model.maya-s.download", title: "Mayaダウンロード Hy4-Flash · Maya-L", status: "running",
    progress: { status: "Hy4-Flash · Maya-L をダウンロード中（2/5 ファイル・/data2tb/LLM/Hy4-Flash-Maya-GGUF）",
      completed: 41_000_000_000, total: 152_900_000_000 }, error: "", can_resume: false,
  };
  await page.route("**/api/v1/jobs?kind=model.*", (r) => r.fulfill({ json: [job] }));
  await page.route("**/api/v1/jobs/dl-1", (r) => r.fulfill({ json: job }));
  await page.goto("/models");
  const panel = page.locator("[data-model-downloads]");
  await expect(panel).toContainText("ダウンロード中");
  await expect(panel).toContainText("2/5 ファイル");
  await expect(panel).toContainText("41.0 GB / 152.9 GB");
  await expect(panel).toContainText("27%");
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
  await page.screenshot({ path: "test-results/model-downloads-320.png" });
});

// 目的: Strata の量子化の選択肢が系列ごと（Inkling を選ぶと SQ-20 などが出て、qwen の「q2_0」を出さない）。
// 根拠: 2026-11-28 利用者（写真）「インクリングの SQ20 って Q2 なの？ちゃんと取れてるのかが分からない」。
test("Strata の設定: 量子化の選択肢は系列ごと", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E 資格情報がありません");
  await page.setViewportSize({ width: 390, height: 900 });
  const sheet = await openSheet(page, context, []);
  // 設定の形は、シートを開いた後に足す（後から足した route が先に効く）
  await page.route("**/api/v1/models/settings/capabilities", (r) => r.fulfill({ json: { runtimes: { strata: { sections: [
    { id: "model", label: "モデル", fields: [
      { id: "alias", label: "モデル名", type: "text", default: "", expert: false },
      { id: "family", label: "系列", type: "select", default: "qwen", expert: false, choices: ["qwen", "inkling-2-mini"],
        choice_labels: { qwen: "Qwen3.8-Flash-Next", "inkling-2-mini": "Inkling-2-Mini" } },
      { id: "model", label: "量子化", type: "select", default: "iq2_xs", expert: false, choices: ["q2_0", "iq2_xs"],
        choices_by: { field: "family", map: { qwen: ["q2_0", "iq2_xs"], "inkling-2-mini": ["sq-20", "sq-15"] } },
        choice_labels: { q2_0: "Q2_0", iq2_xs: "IQ2_XS", "sq-20": "SQ-20", "sq-15": "SQ-15" } },
    ] }], always_on: [], endpoints: {} } } } }));
  await sheet.getByRole("button", { name: "Strata", exact: true }).click();
  await sheet.getByRole("button", { name: "ファイルから登録", exact: true }).click();
  await sheet.getByRole("button", { name: "モデルのファイルを選ぶ" }).click();
  const picker = page.getByRole("dialog", { name: /モデルのファイルを選択/ });
  await picker.getByRole("button", { name: "/data2tb", exact: true }).click();
  await picker.getByRole("button", { name: "Qwen3.8-27B-UD-Q4_K_XL.gguf" }).click();
  await sheet.locator('[data-runtime-register-file="strata"]').getByRole("button", { name: "登録する" }).click();
  const settings = sheet.locator('[data-runtime-settings="strata"]');
  await settings.getByLabel("系列").selectOption("inkling-2-mini");
  await expect(settings.getByLabel("量子化")).toHaveValue("sq-20");
  await expect(settings.getByLabel("量子化").locator("option")).toHaveText(["SQ-20", "SQ-15"]);
  await expect(settings.getByLabel("系列").locator("option:checked")).toHaveText("Inkling-2-Mini");
});
