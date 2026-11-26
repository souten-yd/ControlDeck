import { expect, test } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

// 目的: Models の「モデル登録」に Sly-Radiance が出て、目録から取得（HF の取得 Job）→ 取得したフォルダがフォームへ入る、
// フォルダの中身（safetensors の数・量子化）が見えて量子化の切り替えを提案する、登録の本文が API の形（同時に答える数 1〜8・
// 先読み・prefill chunk・追加の環境変数）で送られる、ことを守る。320px と 1280px で横にあふれない。API はすべてモック。
// 根拠: 2026-11-27 利用者「Sly-Radiance を Radiance と同様のメニュー・導入方法で、目録からの取得・フォルダ選択・並列の設定も」。
// AGENTS.md「不具合・診断・テストの決まり」（目的と根拠）。
for (const width of [320, 1280]) {
  test(`Sly-Radiance の登録: 目録から取得してフォルダを入れ、設定を送る（${width}px）`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials required");
    await page.setViewportSize({ width, height: 900 });
    await establishSession(page, context);
    await page.route("**/api/v1/models/runtime-environment", async (route) => {
      const env = await (await route.fetch()).json();
      env.policy.selected_runtime = "sly-radiance";
      await route.fulfill({ json: env });
    });
    await page.route("**/api/v1/models/providers/*/models", (route) => route.fulfill({ json: [] }));
    await page.route("**/api/v1/models/radiance/instances", (route) => route.fulfill({ json: { runtime: { installed: false }, instances: [] } }));
    await page.route("**/api/v1/models/sly-radiance/instances", async (route) => {
      if (route.request().method() === "POST") return route.fallback();
      await route.fulfill({ json: { runtime: { installed: true, version: "1.13.4-rocm10.1", warning: "" }, instances: [] } });
    });
    const entry = (over: object) => ({ id: "x", repo: "a/b", name: "x", kind: "model", quantization: "", size_gib: null,
      uncensored: false, supported: true, note: "", fit: "unknown", ...over });
    await page.route("**/api/v1/models/sly-radiance/catalog", (route) => route.fulfill({ json: { hardware: { vram_total_gib: 32 }, entries: [
      entry({ id: "swift-heretic", repo: "slybase/Swift-1.5-Qwen3.8-27B-heretic-MXFP4-GPTQ", name: "Swift-1.5 Qwen3.8-27B heretic MXFP4（標準）",
        size_gib: 19, uncensored: true, fit: "fits" }),
      entry({ id: "nvfp4", repo: "unsloth/Qwen3.8-27B-NVFP4", name: "Qwen3.8-27B NVFP4", quantization: "nvfp4" }),
      entry({ id: "dflash2", repo: "syvai/Qwen3.8-27B-DFlash2-W4A16", name: "先読み DFlash2", kind: "drafter" }),
      entry({ id: "minicpm-v", repo: "", name: "MiniCPM-V 4.7", supported: false }),
    ] } }));
    await page.route("**/api/v1/models/sly-radiance/catalog/*/files", (route) => route.fulfill({ json: {
      repo: "a/b", files: [{ path: "config.json", size: 10 }, { path: "model.safetensors", size: 100 }], total_bytes: 110,
      libraries: [{ id: "lib1", label: "SSD", mounted: true }] } }));
    const downloads: any[] = [];
    await page.route("**/api/v1/models/hf/download-jobs", async (route) => {
      downloads.push(route.request().postDataJSON());
      await route.fulfill({ status: 201, json: { job_id: `job-${downloads.length}` } });
    });
    await page.route("**/api/v1/jobs/job-*", (route) => {
      const drafter = route.request().url().endsWith("job-2");
      return route.fulfill({ json: { status: "succeeded", error: "", result: { files: [
        drafter ? "/data1tb/LLM/syvai--drafter/config.json" : "/data1tb/LLM/slybase--Swift/config.json"] } } });
    });
    await page.route("**/api/v1/models/sly-radiance/inspect**", (route) => route.fulfill({ json: {
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
    await sheet.getByRole("button", { name: "Sly-Radiance", exact: true }).click();
    await sheet.getByText("目録から取る").click();
    await expect(sheet.getByLabel("Sly-Radiance目録のモデル")).toContainText("無検閲寄り");
    await expect(sheet.getByRole("option", { name: /MiniCPM-V.*未対応/ })).toHaveAttribute("disabled", "");
    await sheet.getByRole("button", { name: "取得する", exact: true }).click();
    await expect.poll(() => downloads.length).toBe(2);
    expect(downloads[0]).toMatchObject({ repo: "slybase/Swift-1.5-Qwen3.8-27B-heretic-MXFP4-GPTQ", files: ["config.json", "model.safetensors"], expected_bytes: 110 });
    await expect(sheet.getByLabel("Sly-Radianceモデルのフォルダ")).toHaveValue("/data1tb/LLM/slybase--Swift");
    await expect(sheet.getByLabel("Sly-Radiance先読みのフォルダ")).toHaveValue("/data1tb/LLM/syvai--drafter");
    await expect(sheet.getByText(/safetensors 4 個・19.0 GiB/).first()).toBeVisible();
    await sheet.getByRole("button", { name: /量子化を「Quark/ }).first().click();
    await expect(sheet.getByLabel("Sly-Radiance量子化")).toHaveValue("quark");
    await sheet.getByLabel("Sly-Radiance同時実行数").fill("12");
    await expect(sheet.getByLabel("Sly-Radiance同時実行数")).toHaveValue("8");   // 上限 8 に丸める
    await sheet.locator("summary", { hasText: "詳細" }).click();
    await sheet.getByLabel("Sly-Radiance詳しい設定").fill("RADIANCE_KV_GROUP_SIZE=8");
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `/tmp/sly-radiance-register-${width}.png` });
    await sheet.getByRole("button", { name: "登録", exact: true }).click();
    await expect.poll(() => saved?.alias).toBe("qwen3.8-27b-sly");
    expect(saved).toMatchObject({ model_path: "/data1tb/LLM/slybase--Swift", drafter_path: "/data1tb/LLM/syvai--drafter",
      quantization: "quark", max_num_seqs: 8, speculative_tokens: 7, prefill_chunk: 2048,
      engine_settings: { RADIANCE_KV_GROUP_SIZE: "8" } });
  });
}
