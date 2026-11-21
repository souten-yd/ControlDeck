import { expect, test } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

/** モデルの表示名: Models の個別設定で名前を付けると、一覧の名前が変わる。ゲートウェイでの名前（alias）は変えない。
 *  根拠: 2026-11-21 利用者「モデルが同じ名前になるので分かりにくい。任意の名前に変えれるようにしたい」。
 *  検証用インスタンスには Strata のモデルが無いので、一覧と登録の応答だけ差し替える（表示名の保存は本物の API）。 */
const INSTANCE = {
  alias: "qwen-flash-next-uncensored", family: "unsloth", model: "ud-q4_k_xl", context: "512k", port: 8097,
  model_path: "/data1tb/LLM/Qwen3.8-Flash-Next-Uncensored-Q4_K_S/Q4_K_S/Qwen3.8-Flash-Next-Uncensored-Q4_K_S-00001-of-00003.gguf",
  kv: "int8", vision: "gpu", parallel: 1, drafting: "auto", loaded: false, runtime_status: "STOPPED",
};

test("Models: 表示名を付ける", async ({ page, context }) => {
  test.skip(!hasSession(), "ログイン情報がありません");
  await page.setViewportSize({ width: 390, height: 844 });
  await establishSession(page, context);
  let name = "Qwen3.8-Flash-Next-Uncensored";
  await page.route("**/api/v1/models/providers/strata/models", (route) => route.fulfill({ json: [{
    id: INSTANCE.alias, name: "Strata", size_bytes: 111769915520, loaded: false,
    details: { runtime: "strata", display_name: name, quantization: "Q4_K_S", port: 8097 } }] }));
  await page.route("**/api/v1/models/strata/instances", (route) =>
    route.fulfill({ json: { runtime: { installed: true, models_dir: "" }, instances: [INSTANCE] } }));
  await page.route("**/api/v1/models/display-names/**", async (route) => {
    const response = await route.fetch();
    name = (await response.json()).name || "Qwen3.8-Flash-Next-Uncensored";
    await route.fulfill({ response });
  });
  // 検証用インスタンスの既定のランタイムは Ollama なので、Strata を選んだ状態に差し替える（runtime-preferred-model と同じやり方）
  await page.route("**/api/v1/models/runtime-environment", async (route) => {
    const snapshot = await (await route.fetch()).json();
    snapshot.policy = { ...snapshot.policy, selected_runtime: "strata", selected_backend: "" };
    snapshot.runtimes = [{ id: "strata", runtime: "strata", backend: "", label: "Strata", installed: true, available: true }];
    await route.fulfill({ json: snapshot });
  });
  for (const other of ["llama.cpp", "maya-s", "radiance"]) {
    await page.route(`**/api/v1/models/providers/${other}/models`, (route) => route.fulfill({ json: [] }));
  }
  await page.goto("/models");
  await page.getByRole("button", { name: `${INSTANCE.alias}の個別設定を開く` }).click();
  const field = page.getByLabel("モデルの表示名");
  await expect(field).toHaveValue("Qwen3.8-Flash-Next-Uncensored");
  await field.fill("無検閲 Q4_K_S");
  await page.getByRole("button", { name: "保存", exact: true }).first().click();
  await expect(page.getByText("表示名を「無検閲 Q4_K_S」にしました")).toBeVisible();
  await page.screenshot({ path: "test-results/model-display-name.png" });
  await page.keyboard.press("Escape");
  await expect(page.getByText("無検閲 Q4_K_S", { exact: true })).toBeVisible();
});
