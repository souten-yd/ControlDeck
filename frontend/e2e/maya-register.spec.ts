import { expect, test } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

const SHOTS = process.env.E2E_SHOTS_DIR || "/tmp";

function catalog(folder: string) {
  const file = (name: string, size: number | null, present: boolean, required = true, what = "model") =>
    ({ name, path: `${folder}/${name}`, size, present, required, what });
  return {
    folder, memory: { ram_gb: 91.6, vram_gb: 34.2 },
    quants: [
      { family: "glm", setup_id: "glm-maya-s", tier: "Maya-S", model: "GLM-5.3-Flash", size_gb: 96.5, memory_gb: 96,
        alias: "maya-glm-s", registered: "maya-s", ready: false, missing_gb: 0, blockers: [], files: [] },
      { family: "glm", setup_id: "glm-maya-s24", tier: "Maya-S24", model: "GLM-5.3-Flash", size_gb: 94.7, memory_gb: 95,
        alias: "maya-glm-s24", registered: "", ready: !!folder, missing_gb: 0, blockers: [],
        hf: "hf download peasantsmith/GLM-5.3-Flash-Maya-GGUF --include \"Maya-S24/*\" --local-dir /data1tb/LLM/GLM-5.3-Flash-Maya",
        files: folder ? [file("GLM-5.3-Flash-Maya-S24-IQ2_XXS_S-00001-of-00003.gguf", null, true),
                         file("mmproj-GLM-5.3-Flash-F16.gguf", null, false, false, "vision")] : [] },
      { family: "deepseek", setup_id: "deepseek-maya-xs", tier: "Maya-XS", model: "DeepSeek-V4.1-Flash", size_gb: 282.4,
        memory_gb: 121, alias: "maya-deepseek-xs", registered: "", ready: false, missing_gb: folder ? 282.4 : 0,
        published: true, blockers: ["Maya の Python に tokenizers がありません（DeepSeek の pack に要ります）。"],
        folder_error: folder ? "DeepSeek はフォルダ名を DeepSeek-V4.1-Flash-1017-Maya-GGUF にしてください" : undefined,
        hf: "hf download peasantsmith/DeepSeek-V4.1-Flash-1017-Maya-GGUF --revision c33d87b8 --include \"Maya-XS/*\" --include \"dspark/dspark-xs2*\" --local-dir /data1tb/LLM/DeepSeek-V4.1-Flash-1017-Maya-GGUF",
        files: folder ? [file("DeepSeek-V4.1-Flash-1017-Maya-XS-v2-IQ1_S-00001-of-00003.gguf", 47993866016, false)] : [] },
      { family: "deepseek", setup_id: "deepseek-maya-m", tier: "Maya-M", model: "DeepSeek-V4.1-Flash", size_gb: 0,
        memory_gb: 230, alias: "maya-deepseek-m", registered: "", ready: false, missing_gb: 0, published: false,
        blockers: [], files: [] },
      { family: "minimax", setup_id: "minimax-maya-xs", tier: "Maya-XS", model: "MiniMax-M3.1-Flash", size_gb: 93,
        memory_gb: 100, alias: "maya-minimax-xs", registered: "", ready: false, missing_gb: folder ? 93 : 0,
        blockers: [], files: folder ? [file("MiniMax-M3.1-Flash-Maya-XS-00001-of-00002.gguf", 50e9, false),
                                       file("MiniMax-M3.1-Flash-Maya-XS-00002-of-00002.gguf", 43e9, false)] : [],
        ...(folder ? { download_root: folder, download_gb: 93, free_gb: 206.3, can_download: true,
          download_files: ["MiniMax-M3.1-Flash-Maya-XS-00001-of-00002.gguf", "MiniMax-M3.1-Flash-Maya-XS-00002-of-00002.gguf"] } : {}) },
    ],
  };
}

for (const width of [320, 1280]) {
  test(`Maya のモデルを手元のファイルから登録できる（${width}px）`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials required");
    await page.setViewportSize({ width, height: 900 });
    await establishSession(page, context);
    await page.route("**/api/v1/features/maya-s/model", (route) => route.fulfill({ json: {
      installed: true, available: true, configured: true, built: true, running: false, version: "v1.1.64",
      settings: {}, entrypoint: "", models_dir: "", error: "", warning: "" } }));
    await page.route("**/api/v1/features/maya-s/catalog**", (route) => {
      const folder = new URL(route.request().url()).searchParams.get("folder") || "";
      return route.fulfill({ json: catalog(folder) });
    });
    let registered: any = null;
    await page.route("**/api/v1/features/maya-s/models/register-jobs", async (route) => {
      registered = route.request().postDataJSON();
      await route.fulfill({ status: 201, json: { job_id: "job-1", alias: "maya-glm-s24" } });
    });
    await page.route("**/api/v1/models/runtime-environment", async (route) => {
      const snapshot = await (await route.fetch()).json();
      snapshot.policy = { ...snapshot.policy, selected_runtime: "maya-s", selected_backend: "" };
      await route.fulfill({ json: snapshot });
    });
    await page.goto("/models");
    await page.getByRole("button", { name: "モデル登録" }).first().click();
    const sheet = page.getByRole("dialog", { name: "モデル登録" });
    await sheet.getByRole("button", { name: "Maya", exact: true }).click();
    // Without a folder: the list, nothing selectable for registration yet.
    await expect(sheet.getByText("GLM-5.3-Flash", { exact: true })).toBeVisible();
    await expect(sheet.getByRole("radio", { name: /Maya-S\s*登録済み/ })).toBeDisabled();
    await expect(sheet.getByRole("radio", { name: /Maya-M\s*まだ公開/ })).toBeDisabled();
    await sheet.getByLabel("Mayaモデルのフォルダ").fill("/data1tb/LLM/GLM-5.3-Flash-Maya");
    await expect(sheet.getByText(/RAM 91.6 GB ＋ VRAM 34.2 GB/)).toBeVisible();
    // DeepSeek: missing files, the download command, and the folder rule. Registration stays off.
    await sheet.getByRole("radio", { name: /Maya-XS.*フォルダ名が違います.*282.4 GB/ }).click();
    await expect(sheet.getByText("DeepSeek-V4.1-Flash · Maya-XS")).toBeVisible();
    await expect(sheet.getByText(/フォルダ名を DeepSeek-V4.1-Flash-1017-Maya-GGUF/)).toBeVisible();
    await sheet.getByText("自分で取得する場合のコマンド").click();
    await expect(sheet.getByText(/hf download peasantsmith\/DeepSeek-V4.1-Flash-1017-Maya-GGUF/)).toBeVisible();
    await expect(sheet.getByText(/tokenizers がありません/)).toBeVisible();
    await expect(sheet.getByRole("button", { name: "登録", exact: true })).toBeDisabled();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `${SHOTS}/maya-register-missing-${width}.png` });
    // GLM-5.3-Flash Maya-S24: the files are there.
    await sheet.getByRole("radio", { name: /Maya-S24.*そろっています/ }).click();
    await expect(sheet.getByText("ゲートウェイでの名前: maya-glm-s24")).toBeVisible();
    await expect(sheet.getByText(/mmproj-GLM-5.3-Flash-F16.gguf（任意）/)).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/maya-register-ready-${width}.png` });
    await sheet.getByRole("button", { name: "登録", exact: true }).click();
    await expect.poll(() => registered?.setup_id).toBe("glm-maya-s24");
    expect(registered).toMatchObject({ folder: "/data1tb/LLM/GLM-5.3-Flash-Maya", context: 65536, start: false });
  });
}

for (const width of [320, 1280]) {
  test(`Maya のモデルをダウンロードして登録できる・一覧に準備カードを出さない（${width}px）`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials required");
    await page.setViewportSize({ width, height: 900 });
    await establishSession(page, context);
    // Maya is installed but the first model is not prepared: the list must not show a setup card.
    await page.route("**/api/v1/features/maya-s/model", (route) => route.fulfill({ json: {
      installed: true, available: true, configured: false, built: true, running: false, version: "v1.1.64",
      settings: {}, entrypoint: "", models_dir: "", error: "", warning: "" } }));
    await page.route("**/api/v1/features/maya-s/catalog**", (route) => {
      const folder = new URL(route.request().url()).searchParams.get("folder") || "";
      return route.fulfill({ json: catalog(folder) });
    });
    let sent: any = null;
    await page.route("**/api/v1/features/maya-s/models/download-jobs", async (route) => {
      sent = route.request().postDataJSON();
      await route.fulfill({ status: 201, json: { job_id: "job-2", alias: "maya-minimax-xs" } });
    });
    await page.route("**/api/v1/models/runtime-environment", async (route) => {
      const snapshot = await (await route.fetch()).json();
      snapshot.policy = { ...snapshot.policy, selected_runtime: "maya-s", selected_backend: "" };
      await route.fulfill({ json: snapshot });
    });
    await page.goto("/models");
    await expect(page.getByText(/モデルが未準備です/)).toHaveCount(0);
    await expect(page.getByRole("button", { name: /モデルをダウンロード/ })).toHaveCount(0);
    await page.getByRole("button", { name: "モデル登録" }).first().click();
    const sheet = page.getByRole("dialog", { name: "モデル登録" });
    await sheet.getByRole("button", { name: "Maya", exact: true }).click();
    await sheet.getByLabel("Mayaモデルのフォルダ").fill("/data1tb/LLM");
    await sheet.getByRole("radio", { name: /MiniMax|Maya-XS.*93 GB/ }).last().click();
    await expect(sheet.getByText("約 93 GB · 空き 206.3 GB")).toBeVisible();
    await expect(sheet.getByText("保存先: /data1tb/LLM")).toBeVisible();
    await sheet.getByRole("button", { name: "ダウンロードして登録" }).click();
    const confirm = page.locator("[role=dialog], [role=alertdialog]").filter({ hasText: "ダウンロードを開始" });
    await expect(confirm).toContainText("約 93 GB を /data1tb/LLM にダウンロード");
    await expect(confirm).toContainText("MiniMax-M3.1-Flash-Maya-XS-00002-of-00002.gguf");
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `${SHOTS}/maya-download-confirm-${width}.png` });
    expect(sent).toBeNull();  // nothing is downloaded before the owner agrees
    await confirm.getByRole("button", { name: "ダウンロードを開始" }).click();
    await expect.poll(() => sent?.setup_id).toBe("minimax-maya-xs");
    expect(sent).toMatchObject({ folder: "/data1tb/LLM", context: 65536, gpu_index: 0 });
  });
}
