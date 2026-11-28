/** 目的: Models の「モデル登録」に Sly-Radiance が出て、目録から取得（共通の取得 Job）→ 取得したフォルダが
 *        共通設定のフォームへ入る、フォルダの中身（safetensors の数・量子化）が見えて量子化の切り替えを提案する、
 *        登録の本文が API の形（同時に答える数 1〜8・先読み・prefill chunk・追加の環境変数）で送られる、ことを守る。
 *        320px と 1280px で横にあふれない。API はすべてモック。
 *  根拠: 2026-11-27 利用者「Sly-Radiance を Radiance と同様のメニュー・導入方法で、目録からの取得・フォルダ選択・並列の設定も」。
 *        AGENTS.md「不具合・診断・テストの決まり」（目的と根拠）。
 *  更新: Nexarch #24。旧「Sly-Radiance目録のモデル／同時実行数／量子化／先読みのフォルダ／詳しい設定」は
 *        共通 UI では「目録から取得」の行と共通フィールド（エキスパート）に置き換わったので、その導線へ追随した。
 *        旧 /models/sly-radiance/catalog は削除済み（#13）。共通 /models/catalog をモックする。
 *  更新: レビュー #25 / 2026-11-28。目録の見出しを「目録から取得」へ固定し、Sly の prefix cache は
 *        常時オンの決まり（切る選択肢を出さない）を画面と保存本文の両方で明示的に確かめる。 */
import { expect, test } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

const RUNTIME_IDS = ["llama", "strata", "radiance", "sly-radiance", "maya"] as const;

const entry = (over: object) => ({
  id: "x", repo: "a/b", name: "x", kind: "model", format: "safetensors", quantization: "",
  size_gib: null, memory_gib: null, uncensored: false, supported: true, note: "",
  acquire: "hf", source: "e2e", fit: "unknown", ...over,
});
const repoOf = (entryId: string) =>
  entryId === "dflash2" ? "syvai/Qwen3.8-27B-DFlash2-W4A16" : "slybase/Swift-1.5-Qwen3.8-27B-heretic-MXFP4-GPTQ";

for (const width of [320, 1280]) {
  test(`Sly-Radiance の登録: 目録から取得してフォルダを入れ、設定を送る（${width}px）`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials required");
    // フォルダの中身・量子化の提案・環境変数・先読みのフォルダはエキスパート欄にある（UI ルール）。
    await page.addInitScript(() => {
      try { localStorage.setItem("control-deck:commander-expert", "1"); } catch { /* 無視 */ }
    });
    await page.setViewportSize({ width, height: 900 });
    await establishSession(page, context);
    await page.route("**/api/v1/models/runtime-environment", async (route) => {
      const env = await (await route.fetch()).json();
      env.policy = { ...env.policy, selected_runtime: "sly-radiance" };
      env.runtimes = RUNTIME_IDS.map((id) => ({ id, runtime: id, label: id, installed: true, available: true }));
      await route.fulfill({ json: env });
    });
    await page.route("**/api/v1/models/providers/*/models", (route) => route.fulfill({ json: [] }));
    for (const runtime of ["radiance", "sly-radiance"]) {
      await page.route(`**/api/v1/models/${runtime}/instances`, async (route) => {
        if (route.request().method() === "POST") return route.fallback();
        await route.fulfill({ json: { runtime: { installed: true, version: "1.13.4-rocm10.1", warning: "" }, instances: [] } });
      });
    }
    // #10 以降の共通目録（runtimes / entries 形状）。旧 /sly-radiance/catalog は存在しない。
    await page.route("**/api/v1/models/catalog", (route) => route.fulfill({ json: { runtimes: [
      ...RUNTIME_IDS.filter((id) => id !== "sly-radiance").map((id) => ({
        id, name: id, installed: true, formats: [], acquire: "hf", entries: [],
      })),
      { id: "sly-radiance", name: "Sly-Radiance", installed: true, formats: ["safetensors"], acquire: "hf", entries: [
        entry({ id: "swift-heretic", repo: "slybase/Swift-1.5-Qwen3.8-27B-heretic-MXFP4-GPTQ", name: "Swift-1.5 Qwen3.8-27B heretic MXFP4（標準）",
          size_gib: 19, uncensored: true, fit: "fits", quantization: "safetensors" }),
        entry({ id: "dflash2", repo: "syvai/Qwen3.8-27B-DFlash2-W4A16", name: "先読み DFlash2", kind: "drafter" }),
        entry({ id: "minicpm-v", repo: "", name: "MiniCPM-V 4.7", supported: false }),
      ] },
    ] } }));
    await page.route("**/api/v1/models/catalog/sly-radiance/entries/*/files", (route) => route.fulfill({ json: {
      repo: "a/b", files: [{ path: "config.json", size: 10 }, { path: "model.safetensors", size: 100 }], total_bytes: 110,
      libraries: [{ id: "lib1", label: "SSD", mounted: true }] } }));
    const downloads: any[] = [];
    await page.route("**/api/v1/models/acquire-jobs", async (route) => {
      const body = route.request().postDataJSON();
      downloads.push(body);
      await route.fulfill({ status: 201, json: { job_id: body.entry_id === "dflash2" ? "job-2" : "job-1", repo: repoOf(body.entry_id) } });
    });
    await page.route("**/api/v1/jobs/job-*", (route) => {
      const drafter = route.request().url().endsWith("job-2");
      return route.fulfill({ json: {
        id: drafter ? "job-2" : "job-1", kind: "model.hf_download", title: "E2E 取得", status: "succeeded",
        progress: { status: "完了", completed: 1, total: 1 }, error: "", can_resume: false,
        result: { files: [drafter ? "/data1tb/LLM/syvai--drafter/config.json" : "/data1tb/LLM/slybase--Swift/config.json"] },
      } });
    });
    await page.route("**/api/v1/models/sly-radiance/inspect*", (route) => route.fulfill({ json: {
      path: "/data1tb/LLM/slybase--Swift", name: "slybase--Swift", safetensors_count: 4, total_bytes: 19 * 1024 ** 3,
      quant_method: "quark", quant_format: "", suggested_quantization: "quark", ready: true } }));
    let saved: any = null;
    await page.route("**/api/v1/models/sly-radiance/instances", async (route) => {
      if (route.request().method() !== "POST") return route.fallback();
      saved = route.request().postDataJSON();
      await route.fulfill({ status: 201, json: saved });
    });
    await page.goto("/models");
    await page.getByRole("button", { name: "モデル登録" }).first().click();
    const sheet = page.getByRole("dialog", { name: "モデル登録" });
    // Sly-Radiance は Radiance の派生: 一覧に独立したボタンは無く、Radiance の中のスイッチで切り替える
    await expect(sheet.getByRole("button", { name: "Sly-Radiance", exact: true })).toHaveCount(0);
    await sheet.getByRole("button", { name: "Radiance", exact: true }).click();
    const slySwitch = sheet.getByRole("switch", { name: "Sly-Radiance で登録" });
    await expect(slySwitch).toHaveAttribute("aria-checked", "true");
    await slySwitch.click();
    await expect(slySwitch).toHaveAttribute("aria-checked", "false");
    await expect(sheet.locator('[data-runtime-catalog="sly-radiance"]')).toHaveCount(0);
    await slySwitch.click();
    // prefix cache は常時オン（2026-11-28 利用者の決まり）。Sly の共通設定が出たところで、
    // 切るトグル／欄が無いことと常時オンの注記を明示的に確かめる。
    await expect(sheet.locator('[data-runtime-settings="sly-radiance"]')).toBeVisible();
    await expect(sheet.getByText(/prefix cache は常にオンです/)).toBeVisible();
    await expect(sheet.getByRole("checkbox", { name: /prefix cache/i })).toHaveCount(0);
    await expect(sheet.getByLabel(/prefix cache/i)).toHaveCount(0);
    // 目録・ファイル登録は閉じた <details> の <summary> なので、開いてから中身を見る。
    await sheet.locator("summary").filter({ hasText: /^目録から取得/ }).click();
    const catalog = sheet.locator('[data-runtime-catalog="sly-radiance"]');
    await expect(catalog.locator('[data-catalog-entry="swift-heretic"]')).toContainText("無検閲寄り");
    await expect(catalog.locator('[data-catalog-entry="minicpm-v"]')).toContainText("未対応");
    await expect(catalog.locator('[data-catalog-entry="minicpm-v"]').getByRole("button", { name: "取得する" })).toHaveCount(0);

    // 目録の行 → 確認パネルの「取得する」→ ジョブ完了 → 行の「登録する」で共通設定の欄へ入る。
    const acquire = async (entryId: string) => {
      const row = catalog.locator(`[data-catalog-entry="${entryId}"]`);
      await row.getByRole("button", { name: "取得する", exact: true }).click();
      // 確認パネルの「取得する」は行の同名ボタンと区別するため、「やめる」の兄弟として取る。
      await catalog.getByRole("button", { name: "やめる" }).locator("..")
        .getByRole("button", { name: "取得する", exact: true }).click();
      // ジョブ完了後に目録の行へ「登録する」が出る（ジョブカードは完了直後に消えるので待ち条件にはしない）。
      await expect(row.getByRole("button", { name: "登録する", exact: true })).toBeVisible();
      await row.getByRole("button", { name: "登録する", exact: true }).click();
    };
    await acquire("swift-heretic");
    await acquire("dflash2");
    expect(downloads).toHaveLength(2);
    expect(downloads[0]).toMatchObject({ runtime: "sly-radiance", entry_id: "swift-heretic",
      files: ["config.json", "model.safetensors"] });
    expect(downloads[1]).toMatchObject({ runtime: "sly-radiance", entry_id: "dflash2" });
    await expect(sheet.getByLabel("モデルのフォルダ", { exact: true })).toHaveValue("/data1tb/LLM/slybase--Swift");
    await expect(sheet.getByLabel("先読みモデルのフォルダ", { exact: true })).toHaveValue("/data1tb/LLM/syvai--drafter");
    await expect(sheet.getByText(/safetensors 4 個・19.0 GiB/)).toBeVisible();
    await sheet.getByRole("button", { name: /量子化を「quark」にする/ }).click();
    await expect(sheet.getByLabel("量子化の切り替え")).toHaveValue("quark");
    // ゲートウェイ名は自動推測（slybase--swift）になる。目的の別名を明示して保存する。
    await sheet.getByLabel("モデル名").fill("qwen3.8-27b-sly");
    await sheet.getByRole("slider", { name: "同時に処理する数" }).press("End");
    await expect(sheet.getByRole("slider", { name: "同時に処理する数" })).toHaveValue("8");   // 上限 8 に丸める
    await sheet.getByLabel("環境変数（RADIANCE_*・R4D_*）").fill("RADIANCE_KV_GROUP_SIZE=8");
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `/tmp/sly-radiance-register-${width}.png` });
    await sheet.getByTestId("runtime-settings-save").click();
    await expect.poll(() => saved?.alias).toBe("qwen3.8-27b-sly");
    expect(saved).toMatchObject({ model_path: "/data1tb/LLM/slybase--Swift", drafter_path: "/data1tb/LLM/syvai--drafter",
      quantization: "quark", max_num_seqs: 8, speculative_tokens: 7, prefill_chunk: 2048,
      engine_settings: { RADIANCE_KV_GROUP_SIZE: "8" } });
  });
}

// 目的: 設定 → オプション機能で、Sly-Radiance は独立したカードにならず、Radiance のカードの中の切り替え（スイッチ）で出る。
// 根拠: 2026-11-27 利用者「Sly-Radiance は Radiance の派生なので、Radiance の追加機能の中でトグルで切り替えられるように」。
// 押すと導入（約 6.6GB の取得）が始まるので、ここでは押さずに位置と状態だけを見る。
test("Sly-Radiance は Radiance のカードの中のスイッチ（320px）", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials required");
  await page.setViewportSize({ width: 320, height: 900 });
  await establishSession(page, context);
  await page.goto("/settings/features");
  const radiance = page.locator("#feature-radiance");
  await expect(radiance).toBeVisible();
  const child = radiance.locator("[data-feature-child=sly-radiance]");
  await expect(child).toBeVisible();
  await expect(child.getByRole("switch", { name: /Sly-Radiance.*を使う/ })).toHaveAttribute("aria-checked", /true|false/);
  await expect(page.locator("#feature-sly-radiance")).toHaveCount(1);   // Radiance の中の 1 つだけ
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0);
  await radiance.screenshot({ path: "test-results/sly-radiance-toggle-320.png" });
});

// 目的: 登録済みの Sly-Radiance の個別設定を開いて保存すると、登録の項目だけが送られる（表示用の項目を混ぜない）。
// 根拠: 2026-11-27 利用者の iPhone で「runtime: Extra inputs are not permitted / base_url … / ctx_size … / n_parallel …」で保存できなかった。
//       #24: 共通 UI では「保存」が表示名用と設定用に分かれたので、設定用を data-testid で指定する。
test("Sly-Radiance の個別設定を保存できる（表示用の項目を送らない）", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials required");
  await page.setViewportSize({ width: 390, height: 900 });
  await establishSession(page, context);
  const instance = {
    alias: "swift-heretic-sly", model_path: "/data2tb/LLM/Swift", drafter_path: "/data2tb/LLM/drafter", port: 8102,
    context: 131072, kv_cache: "fp8", max_num_seqs: 8, speculative_tokens: 7, gpu_memory_utilization: 0.98,
    prefill_chunk: 2048, prefix_cache: true, reasoning_effort: "", quantization: "", engine_settings: {},
    on_demand_start: true, idle_exclude: false, auto_start: false,
    // 一覧（_present）が足す表示用の項目
    runtime: "sly-radiance", role: "llm", base_url: "http://127.0.0.1:8102/v1", loaded: false,
    runtime_status: "STOPPED", ctx_size: 131072, n_parallel: 8,
  };
  let saved: Record<string, unknown> | null = null;
  await page.route("**/api/v1/models/runtime-environment", async (route) => {
    const env = await (await route.fetch()).json();
    env.policy.selected_runtime = "radiance";
    await route.fulfill({ json: env });
  });
  await page.route("**/api/v1/models/providers/*/models", (route) => route.fulfill({ json:
    route.request().url().includes("/sly-radiance/") ? [{ id: "swift-heretic-sly", name: "swift-heretic-sly", size_bytes: 0, loaded: false, details: {} }] : [] }));
  await page.route("**/api/v1/models/sly-radiance/instances", (route) => route.fulfill({ json: { runtime: { installed: true }, instances: [instance] } }));
  await page.route("**/api/v1/models/radiance/instances", (route) => route.fulfill({ json: { runtime: { installed: true }, instances: [] } }));
  await page.route("**/api/v1/models/sly-radiance/instances/swift-heretic-sly", async (route) => {
    if (route.request().method() === "PUT") {
      saved = route.request().postDataJSON();
      return route.fulfill({ json: { ...instance, ...saved } });
    }
    return route.fulfill({ json: instance });
  });
  await page.goto("/models");
  await page.getByRole("button", { name: "swift-heretic-slyの個別設定を開く" }).click();
  const sheet = page.getByRole("dialog", { name: "swift-heretic-sly · 個別設定", exact: true });
  await sheet.getByTestId("runtime-settings-save").click();
  await expect.poll(() => saved).not.toBeNull();
  for (const key of ["runtime", "role", "base_url", "runtime_status", "ctx_size", "n_parallel", "loaded"]) {
    expect(saved).not.toHaveProperty(key);
  }
  // prefix_cache は共通 UI では常時オンの注記だけで欄が無く、保存本文にも載らない。
  expect(saved).not.toHaveProperty("prefix_cache");
  expect(saved).toMatchObject({ alias: "swift-heretic-sly", max_num_seqs: 8 });
});
