/** Maya のモデル登録（手元のファイル／ダウンロード）を、共通 UI の導線で確かめる。
 *
 *  目的: 目録の量子化ごとの案内（不足ファイル・フォルダ名の規則・ライセンス）が出て、ファイルが揃った
 *        量子化だけ登録できること。未準備のモデルでは一覧に準備カードを出さないこと。
 *  根拠: 2026-11-18 の Maya 登録画面と、Nexarch #11（共通目録・登録・設定への置換）。
 *        #24 で旧ラベル・旧導線を共通 UI に追随させた。レビュー #25 / 2026-11-28 の要修正で、
 *        フォルダ欄は既定（エキスパート切）では読むだけになったため、欄を触る試験ではエキスパートを入れる。
 *        ダウンロードを伴う登録は確認ダイアログを挟む（利用者の明示操作）。
 *        API は page.route で模擬し、実モデルの取得はしない。
 *
 *  共有の注意: 共通 RuntimeSettings の保存／登録ボタンは「登録する」（初期値がある個別設定は「設定を保存」）。
 *              旧「登録」ボタンは存在しない。 */
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
      { family: "hy4", setup_id: "hy4-flash-maya-xs", tier: "Maya-XS", model: "Hy4-Flash", size_gb: 108.1,
        memory_gb: 52, ngram_gb: 63.4, alias: "maya-hy4-xs", registered: "", ready: false, missing_gb: folder ? 108.1 : 0,
        published: true, blockers: [],
        hf: 'hf download peasantsmith/Hy4-Flash-Maya-GGUF --include "Maya-XS/*" --local-dir /data1tb/LLM/Hy4-Flash-Maya',
        files: folder ? [file("Hy4-Flash-Maya-XS-IQ2_XXS-00001-of-00002.gguf", 44.8e9, false),
                         file("Hy4-Flash-Maya-XS-IQ2_XXS-00002-of-00002.gguf", 63.4e9, false),
                         file("mmproj-Hy4-Flash-Maya-XS.gguf", 0.78e9, false, false, "vision")] : [] },
      { family: "hy4", setup_id: "hy4-flash-maya-s", tier: "Maya-S", model: "Hy4-Flash", size_gb: 116.4,
        memory_gb: 64, ngram_gb: 63.4, alias: "maya-hy4-s", registered: "", ready: false, missing_gb: folder ? 116.4 : 0,
        published: true, blockers: [],
        hf: 'hf download peasantsmith/Hy4-Flash-Maya-GGUF --include "Maya-S/*" --include "Maya-XS/Hy4-Flash-Maya-XS-IQ2_XXS-00002-of-00002.gguf" --local-dir /data1tb/LLM/Hy4-Flash-Maya',
        files: folder ? [file("Hy4-Flash-Maya-S-IQ2_S-00001-of-00002.gguf", 53.0e9, false),
                         file("Hy4-Flash-Maya-XS-IQ2_XXS-00002-of-00002.gguf", 63.4e9, false)] : [] },
      { family: "hy4", setup_id: "hy4-flash-maya-m", tier: "Maya-M", model: "Hy4-Flash", size_gb: 131.8,
        memory_gb: 76, ngram_gb: 63.4, alias: "maya-hy4-m", registered: "", ready: false, missing_gb: folder ? 131.8 : 0,
        published: true, blockers: [], files: folder ? [file("Hy4-Flash-Maya-M-IQ3_XXS-00001-of-00002.gguf", 68.4e9, false),
                                                        file("Hy4-Flash-Maya-XS-IQ2_XXS-00002-of-00002.gguf", 63.4e9, false)] : [] },
      { family: "hy4", setup_id: "hy4-flash-maya-l", tier: "Maya-L", model: "Hy4-Flash", size_gb: 152.9,
        memory_gb: 100, ngram_gb: 63.4, alias: "maya-hy4-l", registered: "", ready: false, missing_gb: folder ? 152.9 : 0,
        published: true, blockers: [], files: folder ? [file("Hy4-Flash-Maya-L-IQ4_XS-00001-of-00002.gguf", 89.5e9, false),
                                                        file("Hy4-Flash-Maya-XS-IQ2_XXS-00002-of-00002.gguf", 63.4e9, false)] : [] },
    ],
  };
}

for (const width of [320, 1280]) {
  test(`Maya のモデルを手元のファイルから登録できる（${width}px）`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials required");
    // フォルダ欄（text/path）はエキスパートのときだけ編集できる（UI ルール「不要な表示は隠す」）。
    await page.addInitScript(() => {
      try { localStorage.setItem("control-deck:commander-expert", "1"); } catch { /* 無視 */ }
    });
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
    // 手元のフォルダから登録する Maya の量子化の選択は「ファイルから登録」の中（2026-11-28。目録が主）
    await sheet.locator("summary").filter({ hasText: /^ファイルから登録/ }).click();
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
    // 共通 UI では「登録する」自体は有効で、押したときに理由を出す（旧 UI は無効だった。#21 に報告する挙動差）。
    await expect(sheet.getByText("ファイルが揃うと登録できます")).toBeVisible();
    await sheet.getByTestId("runtime-settings-save").click();
    await expect(page.getByText("ファイルが揃っていません。別のフォルダを選んでください")).toBeVisible();
    expect(registered).toBeNull();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `${SHOTS}/maya-register-missing-${width}.png` });
    // Hy4-Flash (Tencent): four sizes with the shared n-gram table, the license note, and the alias.
    await expect(sheet.getByText("Hy4-Flash", { exact: true })).toBeVisible();
    const hy4 = sheet.getByRole("radio", { name: /Maya-XS.*108\.1 GB/ });
    await expect(hy4).toBeEnabled();
    await hy4.click();
    await expect(sheet.getByText("Hy4-Flash · Maya-XS")).toBeVisible();
    await expect(sheet.getByText(/ゲートウェイでの名前: maya-hy4-xs/)).toBeVisible();
    await expect(sheet.getByText(/Tencent Hy License/)).toBeVisible();
    await expect(sheet.getByText(/あと約 108\.1 GB 足りません/)).toBeVisible();
    await expect(sheet.getByText(/メモリ 52 GB/)).toBeVisible();
    await expect(sheet.getByText(/mmproj-Hy4-Flash-Maya-XS\.gguf（任意）/)).toBeVisible();
    // DeepSeek のときに開いた「自分で取得する場合のコマンド」は量子化を替えても開いたまま。
    // もう一度押すと閉じるので押さない（中身が選んだ量子化のコマンドに替わることを見る）。
    await expect(sheet.locator("details").filter({ hasText: "自分で取得する場合のコマンド" })).toHaveAttribute("open", "");
    await expect(sheet.getByText(/hf download peasantsmith\/Hy4-Flash-Maya-GGUF/)).toBeVisible();
    await expect(sheet.getByText("ファイルが揃うと登録できます")).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/maya-register-hy4-${width}.png` });
    // GLM-5.3-Flash Maya-S24: the files are there.
    await sheet.getByRole("radio", { name: /Maya-S24.*そろっています/ }).click();
    await expect(sheet.getByText("ゲートウェイでの名前: maya-glm-s24")).toBeVisible();
    await expect(sheet.getByText(/mmproj-GLM-5.3-Flash-F16.gguf（任意）/)).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/maya-register-ready-${width}.png` });
    await sheet.getByRole("button", { name: "登録する", exact: true }).click();
    await expect.poll(() => registered?.setup_id).toBe("glm-maya-s24");
    expect(registered).toMatchObject({ folder: "/data1tb/LLM/GLM-5.3-Flash-Maya", context: 65536, start: false });
  });
}

for (const width of [320, 1280]) {
  test(`Maya のモデルをダウンロードして登録できる・一覧に準備カードを出さない（${width}px）`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials required");
    // フォルダ欄を触るのでエキスパートを入れる（既定は切）。
    await page.addInitScript(() => {
      try { localStorage.setItem("control-deck:commander-expert", "1"); } catch { /* 無視 */ }
    });
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
    // 手元のフォルダから登録する Maya の量子化の選択は「ファイルから登録」の中（2026-11-28。目録が主）
    await sheet.locator("summary").filter({ hasText: /^ファイルから登録/ }).click();
    await sheet.getByLabel("Mayaモデルのフォルダ").fill("/data1tb/LLM");
    await sheet.getByRole("radio", { name: /MiniMax|Maya-XS.*93 GB/ }).last().click();
    await expect(sheet.getByText("約 93 GB · 空き 206.3 GB")).toBeVisible();
    await expect(sheet.getByText("保存先: /data1tb/LLM")).toBeVisible();
    // 共通 UI では「ダウンロードして登録」は見積もりの見出しで、ダウンロードは下部の共通
    // 「登録する」ボタン（利用者の明示操作）から始まる。押すと高負荷な取得の確認ダイアログが出る
    // （レビュー #25 / 2026-11-28 の UI 修正）。量子化を選んだだけでは何も始まらない。
    await expect(sheet.getByText("ダウンロードして登録", { exact: true })).toBeVisible();
    expect(sent).toBeNull();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `${SHOTS}/maya-download-confirm-${width}.png` });
    await sheet.getByTestId("runtime-settings-save").click();
    const confirm = page.getByRole("alertdialog", { name: "MiniMax-M3.1-Flash · Maya-XS をダウンロードして登録" });
    await expect(confirm).toBeVisible();
    await expect(confirm.getByText(/約 93 GB/)).toBeVisible();
    expect(sent).toBeNull();  // 承認するまでは取得しない
    await confirm.getByRole("button", { name: "ダウンロードを開始" }).click();
    await expect.poll(() => sent?.setup_id).toBe("minimax-maya-xs");
    expect(sent).toMatchObject({ folder: "/data1tb/LLM", context: 65536, gpu_index: 0 });
  });
}
